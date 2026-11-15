"""Raccolta dati di sistema (nessuna dipendenza Qt).

Sorgenti, tutte opzionali (se una manca, le metriche relative non compaiono):
  - psutil                : CPU, RAM, dischi, rete, processi, batteria
  - NVML (nvidia-ml-py)   : GPU NVIDIA (carico, VRAM, temperatura, potenza, clock, throttling)
  - LibreHardwareMonitor  : temperature, ventole, tensioni, potenze, GPU Intel/AMD
                            (via pythonnet; per i sensori serve l'avvio come amministratore)
"""
from __future__ import annotations

import ctypes
import glob
import io
import json
import os
import platform
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field

LHM_DLL = "LibreHardwareMonitorLib.dll"
NUGET = "https://api.nuget.org/v3-flatcontainer"
TFM_PREF = ["net472", "net471", "net47", "net462", "net461", "net46", "net452", "net451",
            "net45", "net40", "net35", "net20", "netstandard2.0", "netstandard1.6",
            "netstandard1.3", "netstandard1.1", "netstandard1.0"]
UNITS = {
    "Voltage": "V", "Current": "A", "Power": "W", "Clock": "MHz", "Temperature": "°C",
    "Load": "%", "Frequency": "Hz", "Fan": "RPM", "Flow": "L/h", "Control": "%",
    "Level": "%", "Factor": "", "Data": "GB", "SmallData": "MB", "Throughput": "B/s",
    "TimeSpan": "s", "Energy": "mWh", "Noise": "dBA", "Conductivity": "uS/cm",
    "Humidity": "%",
}
THROTTLE_BITS = [
    (0x01, "idle"), (0x02, "clock applicazioni"), (0x04, "limite potenza (SW)"),
    (0x08, "rallentamento HW"), (0x20, "termico (SW)"), (0x40, "termico (HW)"),
    (0x80, "freno potenza (HW)"),
]


# --------------------------------------------------------------------------
# Amministratore
# --------------------------------------------------------------------------
def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def relaunch_as_admin() -> bool:
    """Rilancia il programma con elevazione UAC. True se la richiesta e' partita."""
    try:
        if getattr(sys, "frozen", False):
            args = sys.argv[1:]
        else:
            args = [os.path.abspath(sys.argv[0])] + sys.argv[1:]
        params = " ".join(f'"{a}"' for a in args)
        r = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params,
                                                os.getcwd(), 1)
        return int(r) > 32
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------
# Modello dati
# --------------------------------------------------------------------------
@dataclass
class Sample:
    t: float
    m: dict = field(default_factory=dict)        # metriche piatte "chiave" -> float
    cores: list = field(default_factory=list)    # % per core logico
    gpus: list = field(default_factory=list)     # dict per GPU
    sensors: list = field(default_factory=list)  # (gruppo, tipo, nome, valore, unita')
    parts: list = field(default_factory=list)    # partizioni
    dio: list = field(default_factory=list)      # I/O per disco fisico
    nics: list = field(default_factory=list)     # schede di rete
    procs: list | None = None                    # (pid, nome, cpu%, MB, thread)
    ram: dict = field(default_factory=dict)
    status: dict = field(default_factory=dict)   # backend -> (testo, ok)


# --------------------------------------------------------------------------
# LibreHardwareMonitor: percorsi, download NuGet, backend
# --------------------------------------------------------------------------
def lhm_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "usbtree", "lhm")


def find_lhm() -> str | None:
    here = os.path.dirname(os.path.abspath(sys.argv[0] if sys.argv and sys.argv[0] else __file__))
    for d in (lhm_dir(), os.path.join(here, "lhm"), here):
        if os.path.isfile(os.path.join(d, LHM_DLL)):
            return d
    return None


def pawnio_installed() -> bool:
    """Driver PawnIO (accesso MSR/RAPL per temperatura e potenza CPU nelle LHM recenti)."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\PawnIO"))
        return True
    except OSError:
        return False


def install_pawnio(log=print) -> str:
    """Installa PawnIO con winget (serve amministratore)."""
    import subprocess
    r = subprocess.run(["winget", "install", "-e", "--id", "namazso.PawnIO",
                        "--accept-package-agreements", "--accept-source-agreements"],
                       capture_output=True, text=True, timeout=300, creationflags=0x08000000)
    out = (r.stdout + r.stderr).strip()
    log(out[-400:])
    if r.returncode != 0 and not pawnio_installed():
        raise RuntimeError(out[-400:] or f"winget codice {r.returncode}")
    return "ok"


def have_pythonnet() -> bool:
    try:
        import clr  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _http(url: str, timeout: int = 90) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "usbtree-sysmon"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _norm(tf: str) -> str:
    t = tf.lower().replace(".netframework", "net").replace(".netstandard", "netstandard")
    return t.replace(".", "").replace("-", "")


def _versions(pkg: str) -> list[str]:
    return json.loads(_http(f"{NUGET}/{pkg.lower()}/index.json"))["versions"]


def _latest_stable(pkg: str) -> str:
    v = _versions(pkg)
    stable = [x for x in v if "-" not in x]
    return (stable or v)[-1]


def _best_lib(names: list[str]):
    libs: dict[str, list[str]] = {}
    for n in names:
        m = re.match(r"lib/([^/]+)/([^/]+\.dll)$", n, re.I)
        if m:
            libs.setdefault(_norm(m[1]), []).append(n)
    for t in TFM_PREF:
        if _norm(t) in libs:
            return t, libs[_norm(t)]
    return None, []


def _deps(nuspec: bytes, tfm: str) -> list[tuple[str, str]]:
    root = ET.fromstring(nuspec)
    local = lambda e: e.tag.rsplit("}", 1)[-1]  # noqa: E731
    groups = [g for g in root.iter() if local(g) == "group"]
    chosen = next((g for g in groups if _norm(g.get("targetFramework", "")) == _norm(tfm)), None)
    if chosen is None:
        chosen = next((g for g in groups if not g.get("targetFramework")), None)
    if chosen is None:
        if groups:
            return []
        return [(d.get("id"), d.get("version", "")) for d in root.iter() if local(d) == "dependency"]
    return [(d.get("id"), d.get("version", "")) for d in chosen if local(d) == "dependency"]


def _install_github(dest: str, log=print) -> str:
    """Fallback: scarica lo zip della release GitHub e ne estrae tutte le DLL."""
    rel = json.loads(_http("https://api.github.com/repos/LibreHardwareMonitor/LibreHardwareMonitor/releases/latest"))
    assets = [a for a in rel.get("assets", []) if a["name"].lower().endswith(".zip")]
    if not assets:
        raise RuntimeError("nessuno zip nella release GitHub")
    assets.sort(key=lambda a: (a["name"].lower() != "librehardwaremonitor.zip",
                               "net" in a["name"].lower().replace("librehardwaremonitor", ""),
                               a["name"]))
    a = assets[0]
    log(f"GitHub: {a['name']} ({rel.get('tag_name', '?')})")
    z = zipfile.ZipFile(io.BytesIO(_http(a["browser_download_url"], 180)))
    skip = ("aga.controls", "oxyplot", "microsoft.win32.taskscheduler")  # solo GUI, inutili qui
    for name in z.namelist():
        base = os.path.basename(name)
        if not base.lower().endswith(".dll") or base.lower().startswith(skip):
            continue
        data = z.read(name)
        path = os.path.join(dest, base)
        try:
            if os.path.isfile(path) and os.path.getsize(path) == len(data):
                continue
            with open(path, "wb") as f:
                f.write(data)
        except OSError as e:
            if base.lower() == LHM_DLL.lower() and not os.path.isfile(path):
                raise
            log(f"salto {base}: {e}")
    if not os.path.isfile(os.path.join(dest, LHM_DLL)):
        raise RuntimeError(f"{LHM_DLL} non trovata nello zip")
    ver = str(rel.get("tag_name", "github"))
    with open(os.path.join(dest, "version.txt"), "w") as f:
        f.write(ver)
    return ver


def install_lhm(dest: str | None = None, log=print) -> str:
    """Installa LibreHardwareMonitorLib in `dest`: prima da GitHub (build .NET Framework), poi NuGet."""
    dest = dest or lhm_dir()
    os.makedirs(dest, exist_ok=True)
    try:
        return _install_github(dest, log)
    except Exception as e1:  # noqa: BLE001
        log(f"GitHub non riuscito ({e1}); provo NuGet")
        try:
            return _install_nuget(dest, log)
        except Exception as e2:  # noqa: BLE001
            raise RuntimeError(f"GitHub: {e1}\nNuGet: {e2}\n\n"
                               f"Scarica a mano LibreHardwareMonitor.zip da GitHub e copia le DLL in:\n{dest}") from e2


def _install_nuget(dest: str, log=print) -> str:
    dest = dest or lhm_dir()
    os.makedirs(dest, exist_ok=True)
    seen: set[str] = set()
    main_version = ""

    def inst(pkg: str, ver: str, depth: int) -> None:
        nonlocal main_version
        if pkg.lower() in seen or depth > 6:
            return
        seen.add(pkg.lower())
        m = re.search(r"\d+(?:\.\d+){1,3}", ver or "")
        ver = m[0] if m else _latest_stable(pkg)
        try:
            data = _http(f"{NUGET}/{pkg.lower()}/{ver}/{pkg.lower()}.{ver}.nupkg")
        except Exception:  # noqa: BLE001
            ver = _latest_stable(pkg)
            data = _http(f"{NUGET}/{pkg.lower()}/{ver}/{pkg.lower()}.{ver}.nupkg")
        z = zipfile.ZipFile(io.BytesIO(data))
        names = z.namelist()
        tfm, files = _best_lib(names)
        log(f"{pkg} {ver} ({tfm or 'nessuna libreria'})")
        for n in files:
            with open(os.path.join(dest, os.path.basename(n)), "wb") as f:
                f.write(z.read(n))
        if depth == 0:
            main_version = ver
            if not files:
                raise RuntimeError("pacchetto senza DLL compatibili con .NET Framework")
        spec = next((n for n in names if n.lower().endswith(".nuspec")), None)
        if spec and tfm:
            for dep_id, dep_ver in _deps(z.read(spec), tfm):
                if dep_id:
                    inst(dep_id, dep_ver, depth + 1)

    inst("LibreHardwareMonitorLib", "", 0)
    with open(os.path.join(dest, "version.txt"), "w") as f:
        f.write(main_version)
    return main_version


class LhmBackend:
    def __init__(self, dll_dir: str) -> None:
        try:
            from pythonnet import load
            load("netfx")
        except Exception:  # noqa: BLE001
            pass
        import clr
        dlls = sorted(glob.glob(os.path.join(dll_dir, "*.dll")),
                      key=lambda p: os.path.basename(p).lower() == LHM_DLL.lower())
        for p in dlls:
            try:
                clr.AddReference(p)
            except Exception:  # noqa: BLE001
                pass
        from LibreHardwareMonitor.Hardware import Computer
        c = Computer()
        for attr in ("IsCpuEnabled", "IsGpuEnabled", "IsMemoryEnabled", "IsMotherboardEnabled",
                     "IsStorageEnabled", "IsControllerEnabled", "IsBatteryEnabled", "IsPsuEnabled"):
            try:
                setattr(c, attr, True)
            except Exception:  # noqa: BLE001
                pass
        c.Open()
        self.c = c

    def _flat(self, hw, out: list) -> None:
        ht, hn = str(hw.HardwareType), str(hw.Name)
        sens = [(ht, hn, str(s.SensorType), str(s.Name), s) for s in hw.Sensors]
        out.append((hw, ht, sens))
        for sub in hw.SubHardware:
            self._flat(sub, out)

    def read(self) -> list:
        """Legge i sensori. Gli oggetti .NET sono risolti una sola volta (ogni accesso
        attraverso pythonnet costa); i dischi (SMART) si aggiornano ogni 5 letture."""
        self._n = getattr(self, "_n", 0) + 1
        if getattr(self, "_plan", None) is None or self._n % 120 == 0:
            plan: list = []
            for hw in self.c.Hardware:
                self._flat(hw, plan)
            self._plan = plan
            self._cache = {}
        out: list = []
        for i, (hw, ht, sens) in enumerate(self._plan):
            if ht == "Storage" and self._n % 5 != 1 and i in self._cache:
                out.extend(self._cache[i])
                continue
            hw.Update()
            rows = []
            for ht_, hn, st, name, s in sens:
                v = s.Value
                if v is not None:
                    rows.append((ht_, hn, st, name, float(v)))
            if ht == "Storage":
                self._cache[i] = rows
            out.extend(rows)
        return out

    def close(self) -> None:
        try:
            self.c.Close()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------
# Fallback senza download: contatori PDH (GPU) + WMI/PowerShell (ACPI, dischi)
# --------------------------------------------------------------------------
class WinBackend:
    """Nessuna DLL esterna. GPU di qualsiasi marca da 'GPU Engine' (PDH, nomi inglesi
    indipendenti dalla lingua); temperatura da zona termica ACPI e SMART dei dischi
    (richiedono amministratore; la zona ACPI non e' sempre la CPU)."""

    GPU_COUNTER = r"\GPU Engine(*)\Utilization Percentage"
    PS = ("$o=@{};"
          "try{$o.acpi=@(Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -EA Stop"
          "|%{[math]::Round($_.CurrentTemperature/10-273.15,1)})}catch{};"
          "try{$o.disks=@(Get-PhysicalDisk|%{$r=$_|Get-StorageReliabilityCounter;"
          "@{n=[string]$_.FriendlyName;t=$r.Temperature}})}catch{};"
          "$o|ConvertTo-Json -Compress -Depth 4")

    def __init__(self) -> None:
        import threading
        if sys.platform != "win32":
            raise RuntimeError("solo Windows")
        self.pdh = ctypes.WinDLL("pdh")
        self.q = ctypes.c_void_p()
        if self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.q)):
            raise RuntimeError("PdhOpenQuery")
        self.h = ctypes.c_void_p()
        r = self.pdh.PdhAddEnglishCounterW(self.q, self.GPU_COUNTER, 0, ctypes.byref(self.h))
        if r:
            raise RuntimeError(f"contatore GPU Engine non disponibile (0x{r & 0xffffffff:X})")
        self.pdh.PdhCollectQueryData(self.q)
        self.names = self._adapters()
        self.ps_data: dict = {}
        self._stop = False
        self._th = threading.Thread(target=self._ps_loop, daemon=True)
        self._th.start()

    @staticmethod
    def _adapters() -> dict:
        """luid 'XXXXXXXX_XXXXXXXX' (hex minuscolo) -> descrizione scheda, dal registro DirectX."""
        out: dict = {}
        try:
            import winreg
            base = r"SOFTWARE\Microsoft\DirectX"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
                for i in range(winreg.QueryInfoKey(k)[0]):
                    try:
                        with winreg.OpenKey(k, winreg.EnumKey(k, i)) as sk:
                            desc = winreg.QueryValueEx(sk, "Description")[0]
                            luid = int(winreg.QueryValueEx(sk, "AdapterLuid")[0])
                        out[f"{(luid >> 32) & 0xFFFFFFFF:08x}_{luid & 0xFFFFFFFF:08x}"] = str(desc)
                    except OSError:
                        continue
        except Exception:  # noqa: BLE001
            pass
        return out

    def _ps_loop(self) -> None:
        import subprocess
        flags = 0x08000000  # CREATE_NO_WINDOW
        while not self._stop:
            try:
                r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", self.PS],
                                   capture_output=True, text=True, timeout=40, creationflags=flags)
                self.ps_data = json.loads(r.stdout) if r.stdout.strip() else {}
            except Exception:  # noqa: BLE001
                self.ps_data = {}
            for _ in range(100):
                if self._stop:
                    return
                time.sleep(0.1)

    def _gpu_loads(self) -> dict:
        n = ctypes.c_ulong(0)
        size = ctypes.c_ulong(0)
        self.pdh.PdhCollectQueryData(self.q)
        r = self.pdh.PdhGetFormattedCounterArrayW(self.h, 0x200, ctypes.byref(size), ctypes.byref(n), None)
        if (r & 0xffffffff) != 0x800007D2 or not size.value:  # PDH_MORE_DATA
            return {}
        buf = ctypes.create_string_buffer(size.value)
        r = self.pdh.PdhGetFormattedCounterArrayW(self.h, 0x200, ctypes.byref(size), ctypes.byref(n), buf)
        if r:
            return {}

        class Item(ctypes.Structure):
            _fields_ = [("name", ctypes.c_wchar_p), ("status", ctypes.c_ulong), ("value", ctypes.c_double)]
        arr = ctypes.cast(buf, ctypes.POINTER(Item))
        eng: dict = {}
        for i in range(n.value):
            it = arr[i]
            m = re.search(r"luid_0x([0-9A-Fa-f]{8})_0x([0-9A-Fa-f]{8}).*engtype_(\w+)", it.name or "")
            if m:
                k = (f"{m[1]}_{m[2]}".lower(), m[3])
                eng[k] = eng.get(k, 0.0) + it.value
        out: dict = {}
        for (luid, _t), v in eng.items():
            out[luid] = max(out.get(luid, 0.0), min(v, 100.0))
        return out

    def read(self) -> list:
        rows: list = []
        for luid, load in self._gpu_loads().items():
            name = self.names.get(luid, f"GPU {luid}")
            low = name.lower()
            if "microsoft basic" in low or "remote display" in low:
                continue
            ht = ("GpuNvidia" if "nvidia" in low else "GpuAmd" if ("amd" in low or "radeon" in low)
                  else "GpuIntel")
            rows.append((ht, name, "Load", "GPU Core", float(load)))
        acpi = self.ps_data.get("acpi")
        if isinstance(acpi, (int, float)):
            acpi = [acpi]
        for i, t in enumerate(acpi or []):
            if 0 < t < 120:
                rows.append(("Cpu", "ACPI", "Temperature", f"Zona termica ACPI {i}", float(t)))
        disks = self.ps_data.get("disks")
        if isinstance(disks, dict):
            disks = [disks]
        for d in disks or []:
            t = d.get("t")
            if isinstance(t, (int, float)) and 0 < t < 120:
                rows.append(("Storage", str(d.get("n") or "Disco"), "Temperature", "Temperatura", float(t)))
        return rows

    def close(self) -> None:
        self._stop = True
        try:
            self.pdh.PdhCloseQuery(self.q)
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------
# NVML
# --------------------------------------------------------------------------
def _try(fn, *a, default=None):
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001
        return default


class NvmlBackend:
    def __init__(self) -> None:
        import pynvml as nv
        nv.nvmlInit()
        self.nv = nv
        n = nv.nvmlDeviceGetCount()
        self.handles = [nv.nvmlDeviceGetHandleByIndex(i) for i in range(n)]
        self.names = []
        for h in self.handles:
            name = nv.nvmlDeviceGetName(h)
            self.names.append(name.decode() if isinstance(name, bytes) else str(name))
        if not self.handles:
            raise RuntimeError("nessuna GPU NVIDIA")

    def read(self) -> list[dict]:
        nv = self.nv
        out = []
        for h, name in zip(self.handles, self.names):
            g = {"name": name, "vendor": "Nvidia", "src": "NVML"}
            u = _try(nv.nvmlDeviceGetUtilizationRates, h)
            g["load"] = float(u.gpu) if u else None
            mi = _try(nv.nvmlDeviceGetMemoryInfo, h)
            if mi:
                g["mem_used"] = mi.used / 2**20
                g["mem_total"] = mi.total / 2**20
            g["temp"] = _try(nv.nvmlDeviceGetTemperature, h, nv.NVML_TEMPERATURE_GPU)
            p = _try(nv.nvmlDeviceGetPowerUsage, h)
            g["power"] = p / 1000 if p is not None else None
            pl = _try(nv.nvmlDeviceGetEnforcedPowerLimit, h)
            g["power_limit"] = pl / 1000 if pl is not None else None
            g["clock"] = _try(nv.nvmlDeviceGetClockInfo, h, nv.NVML_CLOCK_GRAPHICS)
            g["mem_clock"] = _try(nv.nvmlDeviceGetClockInfo, h, nv.NVML_CLOCK_MEM)
            g["fan"] = _try(nv.nvmlDeviceGetFanSpeed, h)
            mask = _try(nv.nvmlDeviceGetCurrentClocksThrottleReasons, h, default=0) or 0
            g["throttle"] = ", ".join(n for b, n in THROTTLE_BITS if mask & b and b != 0x01)
            out.append(g)
        return out

    def close(self) -> None:
        _try(self.nv.nvmlShutdown)


# --------------------------------------------------------------------------
# Info statiche
# --------------------------------------------------------------------------
def _reg(path: str, name: str) -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as k:
            return str(winreg.QueryValueEx(k, name)[0]).strip()
    except Exception:  # noqa: BLE001
        return ""


def system_info() -> list[tuple[str, str]]:
    rows = [("Sistema operativo", f"{platform.system()} {platform.release()}  ({platform.version()})")]
    cpu = _reg(r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString") \
        or platform.processor()
    rows.append(("Processore", cpu))
    try:
        import psutil
        rows.append(("Core fisici / logici",
                     f"{psutil.cpu_count(logical=False)} / {psutil.cpu_count(logical=True)}"))
        rows.append(("RAM installata", f"{psutil.virtual_memory().total / 2**30:.1f} GiB"))
        rows.append(("Ultimo avvio", time.strftime("%Y-%m-%d %H:%M:%S",
                                                   time.localtime(psutil.boot_time()))))
    except Exception:  # noqa: BLE001
        pass
    bios = r"HARDWARE\DESCRIPTION\System\BIOS"
    mfr, prod = _reg(bios, "SystemManufacturer"), _reg(bios, "SystemProductName")
    if mfr or prod:
        rows.append(("Sistema / modello", f"{mfr} {prod}".strip()))
    board = f"{_reg(bios, 'BaseBoardManufacturer')} {_reg(bios, 'BaseBoardProduct')}".strip()
    if board:
        rows.append(("Scheda madre", board))
    ver = _reg(bios, "BIOSVersion")
    if ver:
        rows.append(("BIOS", f"{ver}  ({_reg(bios, 'BIOSReleaseDate')})"))
    rows.append(("Python", platform.python_version()))
    rows.append(("Privilegi amministratore", "si" if is_admin() else "no"))
    return rows


# --------------------------------------------------------------------------
# Collettore
# --------------------------------------------------------------------------
class Collector:
    def __init__(self) -> None:
        self.status: dict[str, tuple[str, bool]] = {}
        self.ps = None
        self.nv: NvmlBackend | None = None
        self.lhm: LhmBackend | None = None
        self.win: WinBackend | None = None
        self._tick = 0
        self._last_t = None
        self._net_prev: dict = {}
        self._dio_prev: dict = {}
        self._parts: list = []
        self._nic_stat: dict = {}
        self._procs_cache: list | None = None
        self.lhm_fail = 0
        self.want_procs = True

    # -- avvio backend (lento: chiamare nel thread) -------------------------
    def start(self) -> None:
        try:
            import psutil
            self.ps = psutil
            psutil.cpu_percent(percpu=True)
            psutil.cpu_percent()
            self.status["psutil"] = (f"ok ({psutil.__version__})", True)
        except ImportError:
            self.status["psutil"] = ("non installato: pip install psutil", False)
        try:
            self.nv = NvmlBackend()
            self.status["NVML"] = (f"ok ({len(self.nv.handles)} GPU NVIDIA)", True)
        except Exception as e:  # noqa: BLE001
            self.status["NVML"] = (f"non disponibile ({e}) - pip install nvidia-ml-py", False)
        d = find_lhm()
        if not have_pythonnet():
            self.status["LibreHardwareMonitor"] = ("pythonnet non installato: pip install pythonnet", False)
        elif d is None:
            self.status["LibreHardwareMonitor"] = (
                "DLL non installata: menu Sistema > Scarica LibreHardwareMonitor", False)
        else:
            try:
                self.lhm = LhmBackend(d)
                txt = "ok" + ("" if is_admin() else " (senza amministratore: sensori limitati)")
                self.status["LibreHardwareMonitor"] = (txt, True)
                if not pawnio_installed():
                    self.status["Driver PawnIO"] = (
                        "non installato: senza, temperatura e potenza CPU non sono leggibili "
                        "(menu Sistema > Installa driver PawnIO)", False)
            except Exception as e:  # noqa: BLE001
                import traceback
                self.status["LibreHardwareMonitor"] = (f"errore di caricamento: {e}", False)
                try:
                    with open(os.path.join(os.path.dirname(lhm_dir()), "sysmon.log"), "a", encoding="utf-8") as f:
                        f.write(f"--- {time.ctime()} LHM dir={d}\nDLL: {sorted(os.listdir(d))}\n"
                                f"{traceback.format_exc()}\n")
                except OSError:
                    pass
        if self.lhm is None and sys.platform == "win32":
            try:
                self.win = WinBackend()
                self.status["Windows (PDH/ACPI)"] = (
                    "ok - fallback senza LibreHardwareMonitor: carico GPU, temp. ACPI/dischi"
                    + ("" if is_admin() else " (temperature richiedono amministratore)"), True)
            except Exception as e:  # noqa: BLE001
                self.status["Windows (PDH/ACPI)"] = (f"non disponibile: {e}", False)

    def close(self) -> None:
        if self.lhm:
            self.lhm.close()
        if self.win:
            self.win.close()
        if self.nv:
            self.nv.close()

    # -- campionamento ------------------------------------------------------
    def sample(self) -> Sample:
        now = time.time()
        dt = (now - self._last_t) if self._last_t else None
        self._last_t = now
        s = Sample(t=now, status=dict(self.status))
        if self.ps:
            try:
                self._psutil_part(s, dt)
            except Exception as e:  # noqa: BLE001
                s.status["psutil"] = (f"errore: {e}", False)
        rows: list = []
        if self.lhm:
            try:
                rows = self.lhm.read()
                self.lhm_fail = 0
            except Exception as e:  # noqa: BLE001
                self.lhm_fail += 1
                s.status["LibreHardwareMonitor"] = (f"errore di lettura: {e}", False)
                if self.lhm_fail > 5:
                    self.lhm.close()
                    self.lhm = None
                    self.status["LibreHardwareMonitor"] = (f"disattivato: {e}", False)
        if self.win:
            try:
                rows = self.win.read()
            except Exception as e:  # noqa: BLE001
                s.status["Windows (PDH/ACPI)"] = (f"errore di lettura: {e}", False)
        self._lhm_part(s, rows)
        self._gpu_part(s, rows)
        self._tick += 1
        return s

    # -- psutil -------------------------------------------------------------
    def _psutil_part(self, s: Sample, dt) -> None:
        ps, m = self.ps, s.m
        s.cores = ps.cpu_percent(percpu=True)
        m["cpu.total"] = ps.cpu_percent()
        f = _try(ps.cpu_freq)
        if f:
            m["cpu.freq_mhz"] = f.current
        vm = ps.virtual_memory()
        sm = _try(ps.swap_memory)
        m["ram.percent"] = vm.percent
        m["ram.used_gb"] = (vm.total - vm.available) / 2**30
        m["ram.total_gb"] = vm.total / 2**30
        s.ram = {"total": vm.total, "available": vm.available, "percent": vm.percent,
                 "swap_total": sm.total if sm else 0, "swap_used": sm.used if sm else 0}
        if sm and sm.total:
            m["swap.percent"] = sm.percent
        m["sys.procs"] = len(ps.pids())
        b = _try(ps.sensors_battery)
        if b is not None:
            m["battery.percent"] = b.percent
            m["battery.plugged"] = 1.0 if b.power_plugged else 0.0
            s.sensors.append(("Batteria (psutil)", "Level", "Carica", float(b.percent), "%"))
            if b.secsleft and b.secsleft > 0:
                s.sensors.append(("Batteria (psutil)", "TimeSpan", "Autonomia residua",
                                  float(b.secsleft), "s"))
        # partizioni e schede di rete: aggiornate ogni 5 cicli
        if self._tick % 15 == 0:
            self._parts = self._read_parts()
            self._nic_stat = _try(ps.net_if_stats, default={}) or {}
        s.parts = self._parts
        self._io_rates(s, dt or 0.0)
        if self.want_procs and self._tick % 3 == 0:
            self._procs_cache = self._read_procs()
            s.procs = self._procs_cache

    def _read_parts(self) -> list:
        out = []
        for p in _try(self.ps.disk_partitions, False, default=[]) or []:
            if "cdrom" in p.opts.lower() or not p.fstype:
                continue
            u = _try(self.ps.disk_usage, p.mountpoint)
            if u:
                out.append({"dev": p.device, "mount": p.mountpoint, "fs": p.fstype,
                            "total": u.total, "used": u.used, "percent": u.percent})
        return out

    def _io_rates(self, s: Sample, dt: float) -> None:
        ps, m = self.ps, s.m
        if dt <= 0:  # primo campione: solo contatori di riferimento
            self._dio_prev = _try(ps.disk_io_counters, True, default={}) or {}
            self._net_prev = _try(ps.net_io_counters, True, default={}) or {}
            return
        cur = _try(ps.disk_io_counters, True, default={}) or {}
        rd = wr = busy = 0.0
        for name, c in cur.items():
            prev = self._dio_prev.get(name)
            if prev is None:
                continue
            r = (c.read_bytes - prev.read_bytes) / dt / 1e6
            w = (c.write_bytes - prev.write_bytes) / dt / 1e6
            act = min(100.0, ((c.read_time - prev.read_time) + (c.write_time - prev.write_time))
                      / (dt * 10.0)) if hasattr(c, "read_time") else 0.0
            s.dio.append({"name": name, "read": r, "write": w, "busy": act,
                          "iops": ((c.read_count - prev.read_count)
                                   + (c.write_count - prev.write_count)) / dt})
            rd, wr, busy = rd + r, wr + w, max(busy, act)
        if self._dio_prev:
            m["disk.read_mbs"], m["disk.write_mbs"] = rd, wr
            m["disk.total_mbs"], m["disk.busy"] = rd + wr, busy
        self._dio_prev = cur
        net = _try(ps.net_io_counters, True, default={}) or {}
        down = up = 0.0
        for name, c in net.items():
            prev = self._net_prev.get(name)
            st = self._nic_stat.get(name)
            if prev is None:
                continue
            d = (c.bytes_recv - prev.bytes_recv) / dt / 1e6
            u = (c.bytes_sent - prev.bytes_sent) / dt / 1e6
            s.nics.append({"name": name, "up": bool(st.isup) if st else False,
                           "speed": st.speed if st else 0, "down": d, "upl": u,
                           "rx": c.bytes_recv, "tx": c.bytes_sent,
                           "errin": c.errin, "errout": c.errout,
                           "dropin": c.dropin, "dropout": c.dropout})
            if st and st.isup and "loopback" not in name.lower():
                down, up = down + d, up + u
        if self._net_prev:
            m["net.down_mbs"], m["net.up_mbs"] = down, up
        self._net_prev = net

    def _read_procs(self) -> list:
        ncpu = self.ps.cpu_count() or 1
        out = []
        for p in self.ps.process_iter(["pid", "name", "cpu_percent", "memory_info", "num_threads"]):
            i = p.info
            mi = i.get("memory_info")
            out.append((i["pid"], i.get("name") or "?", (i.get("cpu_percent") or 0.0) / ncpu,
                        (mi.rss / 2**20) if mi else 0.0, i.get("num_threads") or 0))
        out.sort(key=lambda r: (r[2], r[3]), reverse=True)
        return out[:200]

    # -- LibreHardwareMonitor -------------------------------------------------
    def _lhm_part(self, s: Sample, rows: list) -> None:
        m = s.m
        cpu_t = [(n, v) for ht, hn, st, n, v in rows if ht == "Cpu" and st == "Temperature"]
        pick = None
        for want in ("CPU Package", "Core Max", "Core (Tctl/Tdie)", "CPU (Tctl/Tdie)", "Core Average"):
            pick = next((v for n, v in cpu_t if n == want), None)
            if pick is not None:
                break
        if pick is None and cpu_t:
            pick = max(v for _, v in cpu_t)
        if pick is not None:
            m["cpu.temp"] = pick
        pw = next((v for ht, hn, st, n, v in rows
                   if ht == "Cpu" and st == "Power" and n in ("CPU Package", "Package")), None)
        if pw is not None and pw > 0:
            m["cpu.power_w"] = pw
        clocks = [v for ht, hn, st, n, v in rows
                  if ht == "Cpu" and st == "Clock" and re.search(r"Core #\d+", n)]
        if clocks:
            m["cpu.freq_mhz"] = sum(clocks) / len(clocks)
        st_t = [v for ht, hn, st, n, v in rows if ht == "Storage" and st == "Temperature"]
        if st_t:
            m["storage.temp_max"] = max(st_t)
        fans = [v for ht, hn, st, n, v in rows if st == "Fan" and v > 0]
        if fans:
            m["fan.max_rpm"] = max(fans)
        for ht, hn, st, n, v in rows:
            s.sensors.append((f"{hn}  [{ht}]", st, n, v, UNITS.get(st, "")))

    # -- GPU ------------------------------------------------------------------
    def _gpu_part(self, s: Sample, rows: list) -> None:
        gpus: list[dict] = []
        nv_ok = False
        if self.nv:
            try:
                gpus += self.nv.read()
                nv_ok = True
            except Exception as e:  # noqa: BLE001
                s.status["NVML"] = (f"errore: {e}", False)
        by: dict = {}
        for ht, hn, st, n, v in rows:
            if ht in ("GpuIntel", "GpuAmd") or (ht == "GpuNvidia" and not nv_ok):
                by.setdefault((ht, hn), []).append((st, n, v))
        for (ht, hn), sens in by.items():
            g = {"name": hn, "vendor": ht[3:], "src": "LHM"}
            loads = [v for st, n, v in sens if st == "Load"
                     and (n.startswith("GPU Core") or n.startswith("D3D 3D"))] \
                or [v for st, n, v in sens if st == "Load"]
            g["load"] = max(loads) if loads else None
            g["temp"] = next((v for st, n, v in sens if st == "Temperature"), None)
            g["power"] = next((v for st, n, v in sens if st == "Power"), None)
            g["clock"] = next((v for st, n, v in sens if st == "Clock" and n.startswith("GPU Core")),
                              next((v for st, n, v in sens if st == "Clock"), None))
            g["mem_clock"] = next((v for st, n, v in sens if st == "Clock" and "Memory" in n), None)
            g["fan"] = next((v for st, n, v in sens if st in ("Control", "Fan") and "Fan" in n), None)
            used = next((v for st, n, v in sens if st == "SmallData" and "Memory Used" in n), None)
            tot = next((v for st, n, v in sens if st == "SmallData" and "Memory Total" in n), None)
            if used is not None:
                g["mem_used"] = used
            if tot is not None:
                g["mem_total"] = tot
            g["throttle"] = ""
            gpus.append(g)
        m = s.m
        for i, g in enumerate(gpus):
            g["key"] = f"gpu{i}"
            if g.get("mem_total") and g.get("mem_used") is not None:
                g["mem_pct"] = 100.0 * g["mem_used"] / g["mem_total"]
            for k in ("load", "temp", "power", "clock", "mem_clock", "fan", "mem_pct"):
                if g.get(k) is not None:
                    m[f"gpu{i}.{k}"] = float(g[k])
        for agg, k, fn in (("gpu.load", "load", max), ("gpu.temp", "temp", max),
                           ("gpu.mem_pct", "mem_pct", max), ("gpu.power_w", "power", sum)):
            vals = [g[k] for g in gpus if g.get(k) is not None]
            if vals:
                m[agg] = float(fn(vals))
        s.gpus = gpus
        for g in gpus:
            if g["src"] == "NVML":
                grp = f"{g['name']}  [NVML]"
                for key, kind, nm, unit in (("load", "Load", "GPU Core", "%"),
                                            ("temp", "Temperature", "GPU Core", "°C"),
                                            ("power", "Power", "GPU Power", "W"),
                                            ("clock", "Clock", "GPU Core", "MHz"),
                                            ("mem_clock", "Clock", "GPU Memory", "MHz"),
                                            ("fan", "Control", "GPU Fan", "%"),
                                            ("mem_used", "SmallData", "GPU Memory Used", "MB")):
                    if g.get(key) is not None:
                        s.sensors.append((grp, kind, nm, float(g[key]), unit))
