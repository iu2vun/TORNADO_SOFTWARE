"""
Procedura di calibrazione USB: coordina l'acquisizione delle misure
degli standard e popola il CalEngine.

Uso (da UI o da script):
    proc = CalProcedure(driver, engine, ports=[1,2])
    proc.measure_open(port=1)   # blocca fino al completamento
    proc.measure_short(port=1)
    proc.measure_load(port=1)
    proc.measure_open(port=2)
    proc.measure_short(port=2)
    proc.measure_load(port=2)
    proc.measure_through(port1=1, port2=2)
    ok = proc.compute()          # calcola i termini SOLT
"""

import numpy as np
import logging
from typing import Optional
from .engine import CalEngine
from .standards import CalKit

log = logging.getLogger(__name__)


class CalProcedure:
    """
    Acquisisce le misure degli standard dal driver USB e le passa al CalEngine.
    Il driver deve avere già configurato lo sweep (set_sweep chiamato).
    """

    def __init__(self, driver, engine: Optional[CalEngine] = None,
                 ports: list[int] = None, points: int = 501):
        self._drv    = driver
        self._ports  = ports or [1, 2]
        self._points = points
        self.engine  = engine or CalEngine(kit=CalKit.ideal(), ports=self._ports)
        self._status_cb = None
        self.idle_cb = None      # chiamata durante l'attesa (aggiorna il display)

    def set_status_callback(self, cb):
        """cb(message: str) — chiamata ad ogni passo."""
        self._status_cb = cb

    def _log(self, msg: str):
        log.info(msg)
        if self._status_cb:
            self._status_cb(msg)

    def _acquire(self, label: str) -> tuple[np.ndarray, dict]:
        """Legge uno sweep completo dal driver. Ritorna (freqs, {Sij: array})."""
        self._log(f"Acquisizione {label}…")

        last_reported = [0]
        def progress(count, total):
            # aggiorna lo status ogni ~10% o ogni 50 punti, non ad ogni singolo punto
            if count - last_reported[0] >= max(1, total // 20) or count == total:
                last_reported[0] = count
                self._log(f"{label}: {count}/{total} punti…")

        data = self._drv.read_sweep_progress(
            self._points, timeout=60.0, stall_timeout=4.0, on_progress=progress,
            on_idle=self.idle_cb)

        if not data:
            raise RuntimeError(f"Nessun dato ricevuto per {label}")
        freqs = np.array([d["frequency"] for d in data], dtype=float)
        traces: dict[str, np.ndarray] = {}
        for key in ("S11", "S21", "S12", "S22"):
            vals = [d.get(key, complex(float("nan"))) for d in data]
            traces[key] = np.array(vals, dtype=complex)
        self._log(f"{label}: {len(freqs)} punti acquisiti — completato")
        return freqs, traces

    # ── misure standard 1-porta ─────────────────────────────────────────────
    def measure_open(self, port: int):
        """Misura open su 'port'. Connetti lo standard prima di chiamare."""
        # S11 se port==1, S22 se port==2
        s_key = f"S{port}{port}"
        freqs, traces = self._acquire(f"Open porta {port}")
        s11 = traces.get(s_key, traces.get("S11"))
        self.engine.add_open(port, freqs, s11)

    def measure_short(self, port: int):
        s_key = f"S{port}{port}"
        freqs, traces = self._acquire(f"Short porta {port}")
        s11 = traces.get(s_key, traces.get("S11"))
        self.engine.add_short(port, freqs, s11)

    def measure_load(self, port: int):
        s_key = f"S{port}{port}"
        freqs, traces = self._acquire(f"Load porta {port}")
        s11 = traces.get(s_key, traces.get("S11"))
        self.engine.add_load(port, freqs, s11)

    # ── misura through ──────────────────────────────────────────────────────
    def measure_through(self, port1: int, port2: int):
        """Misura through tra port1 (eccitato) e port2."""
        freqs, traces = self._acquire(f"Through {port1}→{port2}")
        s11 = traces.get(f"S{port1}{port1}", traces.get("S11"))
        s21 = traces.get(f"S{port2}{port1}", traces.get("S21"))
        s22 = traces.get(f"S{port2}{port2}", traces.get("S22"))
        s12 = traces.get(f"S{port1}{port2}", traces.get("S12"))
        self.engine.add_through(port1, port2, freqs, s11, s21, s22, s12)

    # ── calcolo ─────────────────────────────────────────────────────────────
    def compute(self, mode: str = "solt") -> bool:
        """
        Calcola i termini di errore.
        mode: 'osl' (solo riflessione) o 'solt' (full 2-port)
        """
        self._log(f"Calcolo termini {mode.upper()}…")
        if mode == "osl":
            ok = self.engine.compute_osl()
        else:
            ok = self.engine.compute_solt()
        if ok:
            self._log(f"Calibrazione {mode.upper()} completata — "
                      f"{len(self.engine.cal_points)} punti")
        else:
            self._log("Calcolo fallito: misure insufficienti")
        return ok
