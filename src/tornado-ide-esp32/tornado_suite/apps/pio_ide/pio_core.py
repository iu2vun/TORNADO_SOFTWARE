"""Logica PlatformIO senza GUI: localizzare ``pio``, leggere platformio.ini, creare progetti, interpretare l'output.

Tutto ciò che qui è puro Python è collaudabile senza PlatformIO e senza Qt.
"""
from __future__ import annotations

import configparser
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
SKIP_DIRS = {".pio", ".git", ".vscode", "__pycache__", ".idea", ".tornado"}
INI_NAME = "platformio.ini"


# ------------------------------------------------------------------ localizzare pio

def find_pio(custom: str = "") -> Optional[list[str]]:
    """Comando (argv di base) per lanciare PlatformIO Core, oppure None se non trovato.

    Ordine: percorso impostato dall'utente, ``pio``/``platformio`` nel PATH, installazione standard in
    ``~/.platformio/penv`` (quella creata da VS Code e da get-platformio.py), ``python -m platformio``.
    """
    if custom and os.path.isfile(custom):
        return [custom]
    for name in ("pio", "platformio"):
        p = shutil.which(name)
        if p:
            return [p]
    home = Path(os.path.expanduser("~")) / ".platformio" / "penv"
    for rel in (("Scripts", "pio.exe"), ("Scripts", "platformio.exe"), ("bin", "pio"), ("bin", "platformio")):
        p = home.joinpath(*rel)
        if p.is_file():
            return [str(p)]
    try:
        from . import pio_install      # venv creato dall'IDE («Installa PlatformIO automaticamente»)
        vp = pio_install.venv_pio()
        if vp:
            return [str(vp)]
    except Exception:  # noqa: BLE001
        pass
    if not getattr(sys, "frozen", False):
        try:
            import importlib.util

            if importlib.util.find_spec("platformio"):
                return [sys.executable, "-m", "platformio"]
        except Exception:  # noqa: BLE001
            pass
    return None


# ------------------------------------------------------------------ platformio.ini

@dataclass
class Env:
    name: str
    board: str = ""
    platform: str = ""
    framework: str = ""
    monitor_speed: int = 0
    upload_port: str = ""
    monitor_port: str = ""


@dataclass
class ProjectInfo:
    root: str
    envs: list[Env] = field(default_factory=list)
    default_envs: list[str] = field(default_factory=list)
    error: str = ""

    def env(self, name: str) -> Optional[Env]:
        return next((e for e in self.envs if e.name == name), None)


def _cfg() -> configparser.RawConfigParser:
    # Raw: niente interpolazione di %; i ${sec.key} di PlatformIO li risolviamo noi.
    c = configparser.RawConfigParser(strict=False, allow_no_value=True, interpolation=None, inline_comment_prefixes=None)
    c.optionxform = str  # type: ignore[assignment]
    return c


def _lookup(c: configparser.RawConfigParser, section: str, key: str, seen: Optional[set] = None) -> str:
    """Valore di ``key`` in ``section`` seguendo ``extends`` e ``${sezione.chiave}``."""
    seen = seen or set()
    if (section, key) in seen:
        return ""
    seen.add((section, key))
    val = ""
    if c.has_section(section) and c.has_option(section, key):
        val = c.get(section, key) or ""
    elif c.has_section(section) and c.has_option(section, "extends"):
        for parent in re.split(r"[,\s]+", c.get(section, "extends") or ""):
            parent = parent.strip()
            if parent:
                val = _lookup(c, parent, key, seen)
                if val:
                    break
    if not val and section.startswith("env:") and c.has_section("env") and c.has_option("env", key):
        val = c.get("env", key) or ""

    def sub(m: re.Match) -> str:
        sec, _, k = m.group(1).rpartition(".")
        return _lookup(c, sec or section, k, seen)

    return re.sub(r"\$\{([^}]+)\}", sub, val).strip()


def first_value(text: str) -> str:
    """Primo valore utile di un'opzione multi-riga o separata da virgole (commenti ``;``/``#`` esclusi)."""
    for line in text.splitlines():
        line = re.split(r"\s[;#]", line.strip())[0].strip()
        if line and not line.startswith((";", "#")):
            return line.split(",")[0].strip()
    return ""


def read_project(root: str) -> ProjectInfo:
    """Legge ``root/platformio.ini``. Non solleva: gli errori finiscono in ``ProjectInfo.error``."""
    info = ProjectInfo(root=root)
    ini = os.path.join(root, INI_NAME)
    if not os.path.isfile(ini):
        info.error = f"Nessun {INI_NAME} in {root}"
        return info
    c = _cfg()
    try:
        with open(ini, encoding="utf-8-sig", errors="replace") as f:
            c.read_file(f)
    except (configparser.Error, OSError) as e:
        info.error = f"{INI_NAME} non leggibile: {e}"
        return info
    if c.has_section("platformio") and c.has_option("platformio", "default_envs"):
        raw = c.get("platformio", "default_envs") or ""
        info.default_envs = [x.strip() for x in re.split(r"[,\s]+", raw) if x.strip() and not x.strip().startswith((";", "#"))]
    for sec in c.sections():
        if not sec.startswith("env:"):
            continue
        name = sec[4:].strip()
        sp = first_value(_lookup(c, sec, "monitor_speed"))
        info.envs.append(Env(
            name=name,
            board=first_value(_lookup(c, sec, "board")),
            platform=first_value(_lookup(c, sec, "platform")),
            framework=first_value(_lookup(c, sec, "framework")),
            monitor_speed=int(sp) if sp.isdigit() else 0,
            upload_port=first_value(_lookup(c, sec, "upload_port")),
            monitor_port=first_value(_lookup(c, sec, "monitor_port")),
        ))
    if not info.envs:
        info.error = f"{INI_NAME} senza sezioni [env:...]"
    return info


def find_project_root(path: str) -> Optional[str]:
    """Risale da ``path`` (file o cartella) fino alla cartella con platformio.ini."""
    p = Path(path).resolve()
    if p.is_file():
        p = p.parent
    for d in (p, *p.parents):
        if (d / INI_NAME).is_file():
            return str(d)
    return None


# ------------------------------------------------------------------ nuovo progetto

@dataclass(frozen=True)
class BoardPreset:
    id: str
    nome: str
    platform: str
    framework: str = "arduino"
    monitor_speed: int = 115200
    extra: tuple[str, ...] = ()


ESP_EXTRA = ("monitor_filters = esp32_exception_decoder",)     # decodifica gli stack trace dei crash (pio device monitor)
PIN_ESP32_2X = "espressif32@6.9.0"                              # core Arduino-ESP32 2.x: le API del core 3.x sono incompatibili con molto codice esistente


# Schede di partenza sicure (id verificati nel registro PlatformIO). L'elenco completo arriva col gestore schede (passo 2).
PRESETS: tuple[BoardPreset, ...] = (
    BoardPreset("esp32dev", "ESP32 DevKit (ESP32-WROOM)", "espressif32", extra=ESP_EXTRA),
    BoardPreset("esp32-s3-devkitc-1", "ESP32-S3 DevKitC-1", "espressif32", extra=ESP_EXTRA),
    BoardPreset("esp32-c3-devkitm-1", "ESP32-C3 DevKitM-1", "espressif32", extra=ESP_EXTRA),
    BoardPreset("esp32-s2-saola-1", "ESP32-S2 Saola-1", "espressif32", extra=ESP_EXTRA),
    BoardPreset("uno", "Arduino Uno", "atmelavr", monitor_speed=9600),
    BoardPreset("nanoatmega328new", "Arduino Nano (nuovo bootloader)", "atmelavr", monitor_speed=9600),
    BoardPreset("nanoatmega328", "Arduino Nano (vecchio bootloader)", "atmelavr", monitor_speed=9600),
    BoardPreset("megaatmega2560", "Arduino Mega 2560", "atmelavr", monitor_speed=9600),
    BoardPreset("leonardo", "Arduino Leonardo", "atmelavr", monitor_speed=9600),
    BoardPreset("pro16MHzatmega328", "Arduino Pro Mini 5V 16 MHz", "atmelavr", monitor_speed=9600),
    BoardPreset("due", "Arduino Due (Programming Port)", "atmelsam", monitor_speed=9600),
    BoardPreset("d1_mini", "Wemos D1 Mini (ESP8266)", "espressif8266"),
    BoardPreset("nodemcuv2", "NodeMCU v2 (ESP8266)", "espressif8266"),
)


_NAME_OK = re.compile(r"^[A-Za-z0-9_.\- ]+$")


def valid_project_name(name: str) -> bool:
    return bool(name.strip()) and bool(_NAME_OK.match(name.strip())) and not name.strip().endswith((".", " "))


def render_ini(preset: BoardPreset, env_name: str = "") -> str:
    env = env_name or preset.id
    lines = [
        f"[env:{env}]",
        f"platform = {preset.platform}",
        f"board = {preset.id}",
        f"framework = {preset.framework}",
        f"monitor_speed = {preset.monitor_speed}",
        *preset.extra,
    ]
    return "\n".join(lines) + "\n"


MAIN_CPP = """#include <Arduino.h>

// Lampeggio del LED integrato (se la scheda lo definisce) e messaggio sulla seriale.
#ifdef LED_BUILTIN
static const int PIN_LED = LED_BUILTIN;
#endif

void setup() {{
  Serial.begin({baud});
#ifdef LED_BUILTIN
  pinMode(PIN_LED, OUTPUT);
#endif
}}

void loop() {{
#ifdef LED_BUILTIN
  digitalWrite(PIN_LED, !digitalRead(PIN_LED));
#endif
  Serial.println("Ciao da {board}");
  delay(1000);
}}
"""

GITIGNORE = ".pio/\n.tornado/\n.vscode/\n*.pyc\n"


def create_project(parent_dir: str, name: str, preset: BoardPreset, extra_envs: Iterable[BoardPreset] = ()) -> str:
    """Crea ``parent_dir/name`` con platformio.ini, src/main.cpp, include/, lib/, .gitignore. Ritorna la radice."""
    name = name.strip()
    if not valid_project_name(name):
        raise ValueError("Nome progetto non valido (lettere, cifre, spazio, - _ .)")
    root = Path(parent_dir) / name
    if (root / INI_NAME).exists():
        raise FileExistsError(f"{root} contiene già un {INI_NAME}")
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "include").mkdir(exist_ok=True)
    (root / "lib").mkdir(exist_ok=True)
    ini = "\n".join(render_ini(p) for p in (preset, *extra_envs))
    (root / INI_NAME).write_text(ini, encoding="utf-8", newline="\n")
    main = root / "src" / "main.cpp"
    if not main.exists():
        main.write_text(MAIN_CPP.format(baud=preset.monitor_speed, board=preset.id), encoding="utf-8", newline="\n")
    gi = root / ".gitignore"
    if not gi.exists():
        gi.write_text(GITIGNORE, encoding="utf-8", newline="\n")
    return str(root)


# ------------------------------------------------------------------ interpretare l'output

@dataclass(frozen=True)
class Diag:
    file: str            # percorso (come stampato dal compilatore)
    line: int
    col: int
    severity: str        # "error" | "warning" | "note"
    message: str
    span: tuple[int, int] = (0, 0)   # posizione di «file:riga:col» nella riga (per sottolinearlo)


# file:riga[:col]: severità: messaggio — il file può avere la lettera di unità di Windows
_GCC = re.compile(
    r"^(?P<loc>(?P<file>(?:[A-Za-z]:)?[^\s:*?\"<>|][^:*?\"<>|]*?):(?P<line>\d+)(?::(?P<col>\d+))?):\s*"
    r"(?P<sev>fatal error|error|warning|note)\s*:\s*(?P<msg>.*)$"
)
# riferimenti del linker: file.cpp:(.text+0x1a): undefined reference to `x'
_LINK = re.compile(r"^(?P<loc>(?P<file>(?:[A-Za-z]:)?[^\s:*?\"<>|][^:*?\"<>|]*?):(?P<line>\d+)):\s*(?P<msg>(?:undefined|multiple) (?:reference|definition).*)$", re.I)
_LINK2 = re.compile(r"^(?P<loc>(?P<file>(?:[A-Za-z]:)?[^\s:*?\"<>|][^:*?\"<>|]*?)):\(\.[\w.]+\+0x[0-9a-f]+\):\s*(?P<msg>.*)$", re.I)
_INCLUDED = re.compile(r"^(?:In file included from|\s+from)\s+(?P<loc>(?P<file>(?:[A-Za-z]:)?[^:\s][^:]*?):(?P<line>\d+)(?::(?P<col>\d+))?)[,:]?\s*$")
_PIO_ERR = re.compile(r"^(?:\*\*\*\s*)?(?:\[[^\]]*\]\s*)?(?:Error|Warning!?|\w+Error):?\s+(?P<msg>.+)$")
# pio check: «src/main.cpp:12: [medium:warning] messaggio [id]»  (gravità high|medium|low)
_CHECK = re.compile(r"^(?P<loc>(?P<file>(?:[A-Za-z]:)?[^\s:*?\"<>|][^:*?\"<>|]*?):(?P<line>\d+)):\s*\[(?P<sev>high|medium|low)(?::[\w-]+)?\]\s*(?P<msg>.*)$")
_INI_LINE = re.compile(r"(?P<loc>(?P<file>platformio\.ini)\W+(?:line\s*)?(?P<line>\d+))", re.I)


def strip_ansi(s: str) -> str:
    return ANSI.sub("", s)


def parse_diag(line: str) -> Optional[Diag]:
    """Interpreta una riga di output. None se non è un errore/avviso localizzabile."""
    s = strip_ansi(line).rstrip("\r\n")
    m = _GCC.match(s)
    if m:
        sev = "error" if "error" in m["sev"] else m["sev"]
        return Diag(m["file"], int(m["line"]), int(m["col"] or 1), sev, m["msg"].strip(), m.span("loc"))
    m = _LINK.match(s)
    if m:
        return Diag(m["file"], int(m["line"]), 1, "error", m["msg"].strip(), m.span("loc"))
    m = _LINK2.match(s)
    if m:
        return Diag(m["file"], 1, 1, "error", m["msg"].strip(), m.span("loc"))
    m = _CHECK.match(s)
    if m:
        return Diag(m["file"], int(m["line"]), 1, "error" if m["sev"] == "high" else "warning", m["msg"].strip(), m.span("loc"))
    m = _INCLUDED.match(s)
    if m:
        return Diag(m["file"], int(m["line"]), int(m["col"] or 1), "note", "incluso da qui", m.span("loc"))
    m = _INI_LINE.search(s)
    if m:
        return Diag(m["file"], int(m["line"]), 1, "error", s.strip(), m.span("loc"))
    return None


def line_kind(line: str) -> str:
    """Classe di colore per una riga di output: error | warning | ok | cmd | dim | text."""
    s = strip_ansi(line)
    d = parse_diag(s)
    if d:
        return {"error": "error", "warning": "warning", "note": "dim"}[d.severity]
    low = s.lower()
    if "[success]" in low or low.startswith("success"):
        return "ok"
    if "[failed]" in low or low.startswith(("error:", "*** [")) or "error:" in low[:12] or "undefined reference" in low:
        return "error"
    if " warning" in low[:40] or low.startswith("warning"):
        return "warning"
    if re.match(r"^(Processing|Uploading|Looking for upload port|Auto-detected|Found \d|Serial port|Connecting|Chip is|Hard resetting|Leaving)", s):
        return "cmd"
    if re.match(r"^(Compiling|Linking|Archiving|Indexing|Building|Retrieving|Checking|Verbose|Configuring|Generating|Creating|Writing|Reading|PLATFORM:|HARDWARE:|PACKAGES:|CONFIGURATION:|LDF:|Dependency Graph|\|--|Scanning dependencies|Compressed|Wrote|Hash of data|Detecting|Stub running|Changing baud)", s):
        return "dim"
    return "text"


_MEM = re.compile(r"^(?P<what>RAM|Flash):\s*\[[=\s]*\]\s*(?P<pct>[\d.]+)%\s*\(used\s*(?P<used>\d+)\s*bytes\s*from\s*(?P<tot>\d+)\s*bytes\)")


def parse_memory(line: str) -> Optional[tuple[str, float, int, int]]:
    """``RAM:   [==   ]  6.3% (used 20692 bytes from 327680 bytes)`` -> ("RAM", 6.3, 20692, 327680)."""
    m = _MEM.match(strip_ansi(line).strip())
    return (m["what"], float(m["pct"]), int(m["used"]), int(m["tot"])) if m else None


_SUMMARY = re.compile(r"^=+\s*\[(?P<res>SUCCESS|FAILED)\]\s*Took\s*(?P<t>[\d.]+)\s*seconds?\s*=+$")


def parse_summary(line: str) -> Optional[tuple[bool, float]]:
    m = _SUMMARY.match(strip_ansi(line).strip())
    return (m["res"] == "SUCCESS", float(m["t"])) if m else None


def resolve_path(root: str, file: str) -> str:
    """Percorso assoluto di un file citato dal compilatore (relativo alla cartella progetto)."""
    f = file.replace("\\", os.sep) if os.sep == "\\" else file
    if os.path.isabs(f):
        return os.path.normpath(f)
    return os.path.normpath(os.path.join(root, f))


# ------------------------------------------------------------------ modifica di platformio.ini (passo 2)
# Editing testuale: commenti, ordine e il resto del file restano intatti.

_SECTION = re.compile(r"^\s*\[(?P<name>[^\]]+)\]\s*(?:[;#].*)?$")
_KEY = re.compile(r"^(?P<key>[A-Za-z0-9_.\-]+)\s*[=:]\s*(?P<val>.*)$")


def _split(text: str) -> list[str]:
    return text.replace("\r\n", "\n").split("\n")


def _section_span(lines: list[str], section: str) -> Optional[tuple[int, int]]:
    """(indice intestazione, indice prima riga dopo la sezione)."""
    start = None
    for i, ln in enumerate(lines):
        m = _SECTION.match(ln)
        if m:
            if start is not None:
                return start, i
            if m["name"].strip() == section:
                start = i
    return (start, len(lines)) if start is not None else None


def _key_span(lines: list[str], a: int, b: int, key: str) -> Optional[tuple[int, int]]:
    """Righe della chiave (con le continuazioni indentate) dentro la sezione [a+1, b)."""
    for i in range(a + 1, b):
        m = _KEY.match(lines[i])
        if m and m["key"] == key:
            j = i + 1
            while j < b and lines[j].strip() != "" and lines[j][:1] in (" ", "\t"):
                j += 1
            return i, j
    return None


def _insert_at(lines: list[str], a: int, b: int) -> int:
    """Posizione dove aggiungere una chiave in fondo alla sezione (prima delle righe vuote finali)."""
    k = b
    while k > a + 1 and lines[k - 1].strip() == "":
        k -= 1
    return k


def get_list(text: str, section: str, key: str) -> list[str]:
    """Voci (una per riga/virgola) di un'opzione multi-valore come ``lib_deps``. Le righe di commento restano voci."""
    lines = _split(text)
    sp = _section_span(lines, section)
    if sp is None:
        return []
    ks = _key_span(lines, *sp, key)
    if ks is None:
        return []
    first = _KEY.match(lines[ks[0]])["val"].strip()
    raw = ([first] if first else []) + [ln.strip() for ln in lines[ks[0] + 1:ks[1]]]
    out: list[str] = []
    for r in raw:
        if not r:
            continue
        if r.startswith((";", "#")):
            out.append(r)
        else:
            out += [x.strip() for x in r.split(",") if x.strip()]
    return out


def set_list(text: str, section: str, key: str, items: list[str]) -> str:
    """Riscrive ``key`` della sezione come elenco a righe indentate; ``items`` vuoto rimuove la chiave."""
    lines = _split(text)
    sp = _section_span(lines, section)
    if sp is None:
        raise KeyError(section)
    block = [f"{key} ="] + [f"    {i}" for i in items] if items else []
    ks = _key_span(lines, *sp, key)
    if ks is not None:
        lines[ks[0]:ks[1]] = block
    elif block:
        k = _insert_at(lines, *sp)
        lines[k:k] = block
    return "\n".join(lines)


def set_option(text: str, section: str, key: str, value: str) -> str:
    """Imposta ``key = value`` nella sezione (sostituisce la chiave esistente, anche multi-riga)."""
    lines = _split(text)
    sp = _section_span(lines, section)
    if sp is None:
        raise KeyError(section)
    ks = _key_span(lines, *sp, key)
    if ks is not None:
        lines[ks[0]:ks[1]] = [f"{key} = {value}"]
    else:
        k = _insert_at(lines, *sp)
        lines.insert(k, f"{key} = {value}")
    return "\n".join(lines)


def lib_dep_name(entry: str) -> str:
    """Nome di una voce lib_deps senza la versione: «bblanchon/ArduinoJson @ ^7.0.4» -> «bblanchon/ArduinoJson»."""
    e = entry.strip()
    return re.split(r"\s*@\s*|\s*=\s*(?=\d)", e, maxsplit=1)[0].strip().lower()


def add_lib_dep(text: str, section: str, spec: str) -> tuple[str, bool]:
    """Aggiunge ``spec`` a lib_deps della sezione. (nuovo testo, True se aggiunta / False se già presente)."""
    items = get_list(text, section, "lib_deps")
    if any(lib_dep_name(x) == lib_dep_name(spec) for x in items if not x.startswith((";", "#"))):
        return text, False
    return set_list(text, section, "lib_deps", items + [spec]), True


def remove_lib_dep(text: str, section: str, entry: str) -> str:
    return set_list(text, section, "lib_deps", [x for x in get_list(text, section, "lib_deps") if x != entry])


@dataclass(frozen=True)
class BoardInfo:
    id: str
    name: str
    platform: str
    mcu: str = ""
    fcpu: int = 0
    ram: int = 0
    rom: int = 0
    frameworks: tuple[str, ...] = ()
    vendor: str = ""
    url: str = ""


def parse_boards_json(text: str) -> list[BoardInfo]:
    """Output di ``pio boards --json-output``. Solleva ValueError se non è JSON."""
    import json

    start = min([i for i in (text.find("["), text.find("{")) if i >= 0], default=-1)   # salta eventuali avvisi iniziali
    if start < 0:
        raise ValueError("nessun JSON nell'output")
    data = json.loads(text[start:])
    if isinstance(data, dict):
        data = list(data.values())
    out = []
    for b in data:
        if not isinstance(b, dict) or not b.get("id"):
            continue
        out.append(BoardInfo(
            id=str(b["id"]), name=str(b.get("name") or b["id"]), platform=str(b.get("platform") or ""),
            mcu=str(b.get("mcu") or ""), fcpu=int(b.get("fcpu") or 0), ram=int(b.get("ram") or 0), rom=int(b.get("rom") or 0),
            frameworks=tuple(b.get("frameworks") or ()), vendor=str(b.get("vendor") or ""), url=str(b.get("url") or ""),
        ))
    return out


def unique_env_name(text: str, base: str) -> str:
    names = {m["name"].strip() for ln in _split(text) if (m := _SECTION.match(ln))}
    name, n = base, 2
    while f"env:{name}" in names:
        name, n = f"{base}_{n}", n + 1
    return name


def pick_framework(board: BoardInfo, current: str = "") -> str:
    if current and current in board.frameworks:
        return current
    return "arduino" if "arduino" in board.frameworks else (board.frameworks[0] if board.frameworks else current or "arduino")


def add_env_for_board(text: str, board: BoardInfo, monitor_speed: int = 115200) -> tuple[str, str]:
    """Aggiunge ``[env:<id>]`` per la scheda. Ritorna (testo, nome ambiente)."""
    name = unique_env_name(text, board.id)
    body = "\n".join([f"[env:{name}]", f"platform = {board.platform}", f"board = {board.id}",
                      f"framework = {pick_framework(board)}", f"monitor_speed = {monitor_speed}"]) + "\n"
    return text.rstrip("\n") + "\n\n" + body, name


def change_env_board(text: str, env: str, board: BoardInfo) -> str:
    """Cambia scheda (e, se serve, piattaforma/framework) a un ambiente esistente. Le altre opzioni restano."""
    sec = f"env:{env}"
    c = _cfg()
    c.read_string(text)
    cur_fw = first_value(_lookup(c, sec, "framework"))
    text = set_option(text, sec, "board", board.id)
    if board.platform:
        text = set_option(text, sec, "platform", board.platform)
    return set_option(text, sec, "framework", pick_framework(board, cur_fw))


@dataclass(frozen=True)
class LibInfo:
    name: str            # «owner/Nome»
    kind: str = ""
    version: str = ""
    published: str = ""
    description: str = ""

    @property
    def spec(self) -> str:
        """Voce lib_deps con versione compatibile (^x.y.z)."""
        return f"{self.name} @ ^{self.version}" if re.match(r"^\d+\.\d+", self.version) else self.name


_PKG_HEAD = re.compile(r"^(?P<name>[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-+ ]+?)\s*$")
_PKG_META = re.compile(r"^(?P<kind>Library|Platform|Tool)\s*[•·\-|]\s*(?P<ver>\S+)(?:\s*[•·\-|]\s*(?P<pub>.*))?$", re.I)


def parse_pkg_search(text: str) -> list[LibInfo]:
    """Output testuale di ``pio pkg search``: «owner/nome», riga «Library • 7.0.4 • Published …», descrizione."""
    out: list[LibInfo] = []
    lines = [strip_ansi(x).rstrip() for x in text.replace("\r\n", "\n").split("\n")]
    i = 0
    while i < len(lines) - 1:
        h = _PKG_HEAD.match(lines[i].strip())
        m = _PKG_META.match(lines[i + 1].strip()) if h else None
        if h and m:
            j, desc = i + 2, []
            while j < len(lines) and lines[j].strip() and not (_PKG_HEAD.match(lines[j].strip()) and j + 1 < len(lines) and _PKG_META.match(lines[j + 1].strip())):
                desc.append(lines[j].strip())
                j += 1
            out.append(LibInfo(h["name"].strip(), m["kind"].title(), m["ver"], (m["pub"] or "").strip(), " ".join(desc)))
            i = j
        else:
            i += 1
    return out
