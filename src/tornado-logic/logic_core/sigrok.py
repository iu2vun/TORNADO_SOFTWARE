"""Backend hardware: analizzatore fx2lafw (cloni Saleae Logic 8 canali) tramite ``sigrok-cli``.

Ogni acquisizione lancia ``sigrok-cli --driver fx2lafw --config samplerate=... --samples N -O binary -o file``.
Con 8 canali l'uscita binaria e' 1 byte per campione, bit i = canale Di: coincide con il formato di Capture.
Trigger, due modi:
- software (predefinito): si acquisisce una finestra di 2*n campioni e si cerca l'evento (come nel simulatore);
  se l'evento non cade nella finestra l'acquisizione fallisce con TriggerTimeout.
- sigrok (``hw_trigger``): sigrok-cli aspetta il trigger (``--triggers Dn=r --wait-trigger``) e fornisce n campioni con
  il pre-trigger richiesto (``captureratio``); si aspetta fino al timeout invece di una finestra fissa e la
  memoria resta n campioni. fx2lafw non ha trigger nel chip: lo fa libsigrok sul PC. Se sigrok-cli rifiuta il
  trigger si ripiega sul trigger software.

Richiede sigrok-cli (installer di PulseView/sigrok), il Visual C++ 2010 Redistributable e il driver WinUSB
sul dispositivo (Zadig). Il percorso si puo' forzare con la variabile d'ambiente TORNADO_SIGROK_CLI.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np

from .capture import Capture
from .device import (MAX_SAMPLES, AcquisitionConfig, DeviceError, LogicDevice, Trigger, TriggerTimeout)
from .trigger import find_trigger, find_triggers

DRIVER = "fx2lafw"
MAX_RATE = 24e6                       # 8 canali: oltre i 24 MHz il dispositivo non regge
SAMPLERATES = (20e3, 25e3, 50e3, 100e3, 200e3, 250e3, 500e3, 1e6, 2e6, 3e6, 4e6, 6e6, 8e6, 12e6, 16e6, 24e6)
_NOISE = ("asix-omega",)              # messaggi innocui di driver non usati
# fx2lafw/WinUSB: dopo una cattura o un caricamento del firmware il dispositivo si ri-enumera e per un attimo
# sigrok-cli non lo apre. Si riprova con attese brevi crescenti invece di una sola attesa lunga.
SCAN_TRIES, SCAN_GAP = 3, 1.0
RETRY_DELAYS = (0.3, 0.7, 1.5)
HW_TRIGGER_CODE = {"rising": "r", "falling": "f", "either": "e", "high": "1", "low": "0"}   # sintassi di sigrok-cli


class _Aborted(DeviceError):
    """Acquisizione interrotta dall'utente (non si ripete e non si ripiega sul software)."""

_WIN_PATHS = (
    r"%ProgramFiles%\sigrok\sigrok-cli\sigrok-cli.exe",
    r"%ProgramFiles(x86)%\sigrok\sigrok-cli\sigrok-cli.exe",
    r"%ProgramFiles%\sigrok\PulseView\sigrok-cli.exe",
    r"%LocalAppData%\Programs\sigrok\sigrok-cli\sigrok-cli.exe",
)


def find_sigrok_cli() -> str | None:
    env = os.environ.get("TORNADO_SIGROK_CLI")
    if env and os.path.isfile(env):
        return env
    found = shutil.which("sigrok-cli")
    if found:
        return found
    if sys.platform == "win32":
        for p in _WIN_PATHS:
            p = os.path.expandvars(p)
            if os.path.isfile(p):
                return p
    return None


def _popen_kwargs() -> dict:
    if sys.platform == "win32":                         # niente finestra console che lampeggia
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def _clean(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if l.strip() and not any(n in l for n in _NOISE))


class SigrokLogicDevice(LogicDevice):
    name = "sigrok-fx2lafw"
    SAMPLERATES = SAMPLERATES

    def __init__(self, exe: str | None = None) -> None:
        super().__init__()
        self._exe = exe
        self._proc: subprocess.Popen | None = None
        self._aborted = False
        self._desc = ""
        self.stats: dict = {}             # ultima acquisizione: secondi, tentativi, motivi dei tentativi falliti

    # ------------------------------------------------------------ ciclo di vita
    def open(self) -> None:
        exe = self._exe or find_sigrok_cli()
        if not exe:
            raise DeviceError("sigrok-cli non trovato: installa PulseView/sigrok "
                              "(o imposta TORNADO_SIGROK_CLI con il percorso di sigrok-cli.exe)")
        self._exe = exe
        line, out = "", ""
        for attempt in range(1, SCAN_TRIES + 1):
            try:
                r = subprocess.run([exe, "--driver", DRIVER, "--scan"], capture_output=True, text=True,
                                   timeout=30, **_popen_kwargs())
            except (OSError, subprocess.TimeoutExpired) as e:
                raise DeviceError(f"impossibile eseguire sigrok-cli: {e}") from e
            out = (r.stdout or "") + "\n" + (r.stderr or "")
            line = next((l.strip() for l in out.splitlines() if l.strip().startswith(DRIVER)), "")
            if line or attempt == SCAN_TRIES:
                break
            time.sleep(SCAN_GAP)
        if not line:
            raise DeviceError("analizzatore non trovato (fx2lafw): controlla il cavo USB e il driver WinUSB "
                              "(Zadig). " + _clean(out)[-300:])
        self._desc = line
        super().open()

    def close(self) -> None:
        self.abort()
        super().close()

    def info(self) -> dict:
        return {"name": self.name, "channels": 8, "max_samplerate": MAX_RATE, "backend": "sigrok-cli",
                "exe": self._exe or "", "device": self._desc}

    def abort(self) -> None:
        self._aborted = True
        p = self._proc
        if p is not None and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass

    # ------------------------------------------------------------ acquisizione
    def _run_capture(self, fs: float, n: int, timeout: float, trigger: Trigger | None = None) -> np.ndarray:
        fd, path = tempfile.mkstemp(suffix=".bin", prefix="tlogic_")
        os.close(fd)
        conf = f"samplerate={int(round(fs))}"
        extra: list[str] = []
        if trigger is not None:
            conf += f":captureratio={int(round(trigger.pretrigger * 100))}"
            extra = ["--triggers", f"D{trigger.channel}={HW_TRIGGER_CODE[trigger.mode]}", "--wait-trigger"]
        cmd = [self._exe, "--driver", DRIVER, "--config", conf, *extra,
               "--samples", str(n), "-O", "binary", "-o", path]
        t0 = time.perf_counter()
        notes: list[str] = []
        self.stats = {"attempts": 0, "notes": notes, "seconds": 0.0}
        try:
            for attempt in range(len(RETRY_DELAYS) + 1):
                self.stats["attempts"] = attempt + 1
                self._aborted = False
                self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                              text=True, **_popen_kwargs())
                try:
                    _, err = self._proc.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.communicate()
                    if trigger is not None:
                        raise TriggerTimeout("trigger non verificatosi entro il tempo massimo di attesa") from None
                    raise DeviceError("timeout dell'acquisizione (sigrok-cli non risponde)") from None
                rc = self._proc.returncode
                if self._aborted:
                    raise _Aborted("acquisizione interrotta")
                if rc == 0 and os.path.getsize(path) >= n:
                    break
                why = _clean(err or "").splitlines()
                why = why[-1][:160] if why else f"codice {rc}, {os.path.getsize(path)} byte su {n}"
                notes.append(why)
                if attempt == len(RETRY_DELAYS):
                    raise DeviceError(f"sigrok-cli ha fallito dopo {attempt + 1} tentativi (codice {rc}): "
                                      f"{_clean(err or '')[-400:] or 'nessun messaggio'}")
                time.sleep(RETRY_DELAYS[attempt])
                if self._aborted:
                    raise _Aborted("acquisizione interrotta")
            raw = np.fromfile(path, dtype=np.uint8)
        finally:
            self._proc = None
            self.stats["seconds"] = time.perf_counter() - t0
            try:
                os.remove(path)
            except OSError:
                pass
        if raw.size < n:
            raise DeviceError(f"dati incompleti: {raw.size} campioni su {n}")
        return raw if trigger is not None else raw[:n]

    def _acquire_hw_trigger(self, cfg: AcquisitionConfig) -> Capture:
        n, fs, t = cfg.n_samples, cfg.samplerate, cfg.trigger
        # attesa del trigger: n/fs per i dati, il resto e' il tempo concesso all'evento (timeout_s)
        stream = self._run_capture(fs, n, cfg.timeout_s + n / fs + 10.0, trigger=t)
        pre = int(t.pretrigger * n)
        # posizione reale: l'evento piu' vicino a quella attesa (il pre-trigger puo' contenere altri fronti)
        w = max(64, n // 50)
        cands = find_triggers(stream, t, max(1, pre - w), min(n, pre + w + 1))
        trig_idx = int(cands[np.argmin(np.abs(cands - pre))]) if cands.size else pre
        self.stats["trigger"] = "sigrok" + ("" if cands.size else " (posizione assunta: evento non visibile nei dati)")
        data = stream[:n] & np.uint8(cfg.channel_mask())
        return Capture(fs, data, trigger_index=trig_idx, source=self.name, trigger_channel=t.channel,
                       trigger_mode=t.mode, meta=dict(self.stats))

    def acquire(self, cfg: AcquisitionConfig) -> Capture:
        self.validate(cfg)
        fallback = None
        if cfg.trigger is not None and cfg.hw_trigger:
            try:
                return self._acquire_hw_trigger(cfg)
            except (TriggerTimeout, _Aborted):
                raise
            except DeviceError as e:                       # sigrok-cli rifiuta il trigger: si ripiega sul software
                fallback = f"trigger sigrok non riuscito ({str(e)[:160]}): uso il trigger software"
        n, fs = cfg.n_samples, cfg.samplerate
        total = n if cfg.trigger is None else 2 * n
        if total > MAX_SAMPLES:
            raise DeviceError("con il trigger la finestra (2 x campioni) supera il massimo consentito")
        timeout = cfg.timeout_s + total / fs + 10.0
        stream = self._run_capture(fs, total, timeout)

        if cfg.trigger is None:
            start, trig_idx = 0, None
        else:
            pre = int(cfg.trigger.pretrigger * n)
            hit = find_trigger(stream, cfg.trigger, start=pre, stop=n + pre)
            if hit is None:
                raise TriggerTimeout("trigger non verificatosi nella finestra di acquisizione")
            start, trig_idx = hit - pre, pre

        data = stream[start:start + n] & np.uint8(cfg.channel_mask())
        t = cfg.trigger
        meta = dict(self.stats)
        if fallback:
            meta["notes"] = [fallback] + list(meta.get("notes", []))
            meta["trigger"] = "software (ripiego)"
        return Capture(fs, data, trigger_index=trig_idx, source=self.name,
                       trigger_channel=t.channel if t is not None else None,
                       trigger_mode=t.mode if t is not None else None, meta=meta)
