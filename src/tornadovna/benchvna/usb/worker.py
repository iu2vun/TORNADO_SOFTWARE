"""
VnaWorkerUSB — worker di acquisizione con driver USB diretto.

Stessa interfaccia segnali/slot di worker.py (SCPI), sostituibile
senza toccare la UI. Calibrazione SOLT a 12 termini integrata (Fase 2).
"""

import dataclasses
import logging
import math
import time
import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal, Slot

from .driver import LibreVNAUSBDriver, LibreVNAUSBError
from .protocol import SweepSettings, PacketType

log = logging.getLogger(__name__)


class VnaWorkerUSB(QObject):
    # stessi segnali di VnaWorker (SCPI) → UI invariata
    sweepReady       = Signal(object, object)   # freqs ndarray, {trace: complex ndarray}
    status           = Signal(str)
    connectedChanged = Signal(bool, str)
    calStepDone      = Signal(int, bool)         # placeholder Fase 2
    calFinished      = Signal(bool, str)         # placeholder Fase 2
    sweepRate        = Signal(float)             # sweep completi al secondo
    deviceStatus     = Signal(object)            # dict: lock PLL, ADC, temperature, ...

    TICK_MS = 16   # ~60 Hz: poll dello sweep pubblicato dal thread RX

    def __init__(self):
        super().__init__()
        self._drv: LibreVNAUSBDriver | None = None
        self._timer: QTimer | None = None
        self._running  = False
        self._demo     = False
        self._cfg = dict(start=1e8, stop=6e9, points=501,
                         ifbw=10000, level=-10, avg=1,
                         traces=["S11", "S21"])
        self._dirty = True
        self._sweep_buf: dict[int, dict] = {}
        self._expected_points = 501
        self._last_serial   = 0       # ultimo sweep serial processato
        self._active_engine = None    # CalEngine attivo, None = no cal
        self._cal_engine    = None
        self._cal_proc      = None
        self._cal_mode      = "solt"
        self._cal_steps     = []
        # frame di visualizzazione: aggiornato punto per punto durante lo sweep
        self._frame_n       = 0
        self._frame_f       = None
        self._frame: dict[str, np.ndarray] = {}
        self._applied_id    = None
        self._applied_n     = 0
        self._frame_dirty   = False
        self._last_emit     = 0.0
        self._rate_t0       = time.monotonic()
        self._rate_n        = 0
        # monitor di stato dello strumento: off | periodic | continuous
        self._st_mode       = "periodic"
        self._st_period     = 3.0
        self._st_active     = False
        self._st_ser        = 0
        self._st_start_ser  = 0
        self._st_t0         = 0.0
        self._st_last       = 0.0

    # ── lifecycle ──────────────────────────────────────────────────────────
    @Slot()
    def init(self):
        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._timer.start(self.TICK_MS)

    @Slot()
    def stop(self):
        if self._timer:
            self._timer.stop()

    # ── connessione ────────────────────────────────────────────────────────
    @Slot(str, int, bool)
    def connectDevice(self, _host: str, _port: int, demo: bool):
        """_host/_port ignorati: usiamo USB diretto."""
        self._demo = demo
        if demo:
            self.connectedChanged.emit(False, "DEMO — nessun hardware (modalità USB)")
            return

        if self._drv:
            try:
                self._drv.disconnect()
            except Exception:
                pass
            self._drv = None

        try:
            drv = LibreVNAUSBDriver()

            self.status.emit("Ricerca device USB…")
            serials = drv.list_devices()
            if not serials:
                raise LibreVNAUSBError(
                    "Nessun LibreVNA trovato via USB.\n"
                    "Installa WinUSB tramite Zadig e copia libusb-1.0.dll in VNA_Suite\\."
                )

            self.status.emit(f"Device trovato: {serials[0]} — connessione…")
            drv.connect(serials[0])          # può sollevare LibreVNAUSBError
            self._drv = drv
            info = drv.device_info
            self._dirty = True
            self._sweep_buf.clear()
            self._st_active, self._st_ser, self._st_last = False, 0, 0.0
            desc = str(info) if info else "device connesso"
            self.connectedChanged.emit(True, f"USB  |  S/N {serials[0]}  |  {desc}")
            self.status.emit("Connesso via USB — sweep attivo")

        except LibreVNAUSBError as e:
            self._drv = None; self._demo = True
            self.connectedChanged.emit(False, f"Errore USB: {e}")
            self.status.emit(f"USB fallito: {e}")
        except Exception as e:
            self._drv = None; self._demo = True
            self.connectedChanged.emit(False, f"Errore inatteso USB: {e}")
            self.status.emit(f"Errore inatteso: {e}")
            self.connectedChanged.emit(False, str(e))
            self.status.emit("Errore USB → demo")
        except Exception as e:
            self._drv = None; self._demo = True
            self.connectedChanged.emit(False, f"Errore inatteso: {e}")

    # ── config sweep ───────────────────────────────────────────────────────
    @Slot(dict)
    def applyConfig(self, cfg: dict):
        self._cfg.update(cfg)
        self._dirty = True

    @Slot(bool)
    def setRunning(self, run: bool):
        self._running = run
        self.status.emit("Continuous" if run else "Hold")
        if not self._drv:
            return
        if not run:
            self._drv.set_idle()
        else:
            # ripresa da Hold: il device era in idle, ripartiamo con un
            # SweepSettings+INITIATE (via _push_config) che riavvia il
            # flusso continuo. Un solo trigger basta: poi sweeppa da solo.
            self._sweep_buf.clear()
            try:
                self._push_config()   # set_sweep() include già initiate_sweep()
            except Exception as e:
                self.status.emit(f"Errore ripresa sweep: {e}")

    @Slot()
    def triggerSingle(self):
        """Single: riavvia lo sweep, acquisisce UNA scansione completa, poi Hold."""
        if self._demo or not self._drv:
            self._emit_demo(); self.status.emit("Single (demo)"); return
        try:
            self._running = False
            self._push_config()   # riavvia il flusso (set_sweep + initiate)
            pts = int(self._cfg["points"])
            data = self._drv.read_sweep_progress(pts, timeout=60.0,
                                                 on_idle=self._poll_frame)
            if data:
                self._emit_from_list(data)
            self._drv.set_idle()
            self.status.emit(f"Single: {len(data)} punti — Hold")
        except Exception as e:
            self._handle_error(e)

    @Slot(dict)
    def calSetKit(self, kit: dict):
        """Imposta la definizione del kit usata dalla prossima calibrazione."""
        self._cal_kit_dict = dict(kit) if kit else None
        self.status.emit("Kit di calibrazione aggiornato")

    @Slot(list)
    def calSetup(self, steps: list):
        """
        Riceve gli step dal CalibrationDialog:
          steps = [("Open",[1]), ("Short",[1]), ("Load",[1]), ("Through",[1,2]), ...]
        Costruisce il CalEngine/CalProcedure e traduce gli step nel formato interno.
        """
        try:
            if not self._drv:
                self.status.emit("Cal: device non connesso")
                return

            # determina le porte coinvolte e se c'è un through (→ SOLT altrimenti OSL)
            ports_set = set()
            has_through = False
            for mtype, p in steps:
                ports_set.update(p)
                if mtype.lower() == "through":
                    has_through = True
            ports = sorted(ports_set)
            mode = "solt" if has_through else "osl"

            from benchvna.cal.engine import CalEngine
            from benchvna.cal.standards import CalKit
            from benchvna.cal.procedure import CalProcedure

            kd = getattr(self, "_cal_kit_dict", None)
            kit = CalKit.from_dict(kd) if kd else CalKit.ideal()
            self._cal_engine = CalEngine(kit=kit, ports=ports)
            self._cal_proc   = CalProcedure(self._drv, self._cal_engine,
                                            ports=ports,
                                            points=int(self._cfg["points"]))
            self._cal_proc.set_status_callback(lambda m: self.status.emit(m))
            self._cal_mode = mode

            # traduce gli step nel formato interno ("open", port) / ("through", p1, p2)
            internal_steps = []
            for mtype, p in steps:
                m = mtype.lower()
                if m == "open":
                    internal_steps.append(("open", p[0]))
                elif m == "short":
                    internal_steps.append(("short", p[0]))
                elif m == "load":
                    internal_steps.append(("load", p[0]))
                elif m == "through":
                    internal_steps.append(("through", p[0], p[1]))
                else:
                    log.warning("Tipo standard sconosciuto: %s", mtype)
            self._cal_steps = internal_steps

            # ── riavvia lo sweep sul device ──────────────────────────────
            # Il dialog di cal mette in pausa il continuo (setRunning(False)
            # → SET_IDLE): il device è fermo. Senza rilanciare uno sweep,
            # 'measure' resterebbe bloccato in attesa di dati che non arrivano.
            # Forziamo stages=1 (full 2-port) per avere tutti gli Sij.
            from .protocol import SweepSettings
            c = self._cfg
            s = SweepSettings(
                f_start               = int(c["start"]),
                f_stop                = int(c["stop"]),
                points                = int(c["points"]),
                if_bandwidth          = int(c["ifbw"]),
                cdbm_excitation_start = int(c["level"] * 100),
                cdbm_excitation_stop  = int(c["level"] * 100),
                stages                = 1,
                port1_stage           = 0,
                port2_stage           = 1,
                suppress_peaks        = True,
                fixed_power           = True,
            )
            self._drv.set_sweep(s)
            self._cal_proc.idle_cb = self._poll_frame
            self._reset_frame()
            self._running = True     # lo sweep resta visibile durante la cal
            self.status.emit(
                f"Cal pronta: {len(internal_steps)} step ({mode.upper()}) — sweep avviato")

        except Exception as e:
            log.exception("calSetup fallito")
            self.status.emit(f"Errore preparazione cal: {e!r}")

    @Slot(int)
    def calMeasureStep(self, idx: int):
        if not hasattr(self, "_cal_proc") or self._cal_proc is None:
            self.calStepDone.emit(idx, False); return
        try:
            # forza la cattura di sweep freschi: azzera il riferimento allo
            # sweep pubblicato così _collect_one_sweep aspetta dati nuovi
            # acquisiti DOPO che l'utente ha collegato lo standard corrente
            with self._drv._sweep_lock:
                self._drv._latest_sweep = None

            step = self._cal_steps[idx]
            kind = step[0]
            if kind == "open":
                self._cal_proc.measure_open(step[1])
            elif kind == "short":
                self._cal_proc.measure_short(step[1])
            elif kind == "load":
                self._cal_proc.measure_load(step[1])
            elif kind == "through":
                self._cal_proc.measure_through(step[1], step[2])
            self.calStepDone.emit(idx, True)
        except Exception as e:
            self.status.emit(f"Errore step {idx}: {e}")
            self.calStepDone.emit(idx, False)

    @Slot(str)
    def calActivate(self, _short: str):
        if not hasattr(self, "_cal_proc") or self._cal_proc is None:
            self.calFinished.emit(False, "nessuna procedura attiva"); return
        # ferma il polling durante il calcolo (può durare, evita conflitti timer)
        was_running = self._running
        self._running = False
        try:
            ok = self._cal_proc.compute(mode=getattr(self, "_cal_mode", "solt"))
            if ok:
                self._active_engine = self._cal_engine
                self.calFinished.emit(True, f"SOLT — {len(self._cal_engine.cal_points)} punti")
            else:
                self.calFinished.emit(False, "calcolo fallito: misure insufficienti")
        except Exception as e:
            import traceback; traceback.print_exc()
            self.calFinished.emit(False, str(e))
        finally:
            self._dirty = True
            self._running = was_running

    @Slot(str)
    def calSave(self, path: str):
        eng = getattr(self, "_active_engine", None)
        if eng is None or not eng.cal_points:
            self.status.emit("Nessuna cal da salvare"); return
        try:
            eng.save(path); self.status.emit(f"Cal salvata: {path}")
        except Exception as e:
            self.status.emit(f"Errore save: {e}")

    @Slot(str)
    def calLoad(self, path: str):
        from benchvna.cal.engine import CalEngine
        try:
            eng = CalEngine()
            eng.load(path)
            self._active_engine = eng
            self.calFinished.emit(True, f"Cal caricata: {len(eng.cal_points)} punti")
            self.status.emit(f"Cal caricata: {path}")
        except Exception as e:
            self.calFinished.emit(False, str(e))

    # ── ciclo interno ──────────────────────────────────────────────────────
    def _push_config(self):
        c = self._cfg
        # stages=1 → eccita port1 (stage0) e port2 (stage1) → S11,S21,S12,S22
        # stages=0 → solo port1 → S11
        # stage 0 (porta 1 eccitata) misura GIA' S11 e S21: serve il secondo
        # stage (porta 2 eccitata, tempo di sweep x2) solo per S12/S22.
        want_stage1 = any(t in ("S12", "S22") for t in c["traces"])
        stages = 1 if want_stage1 else 0
        # se c'è una cal SOLT attiva, il device DEVE misurare in full 2-port
        # (tutti gli Sij) perché la correzione a 12 termini li usa tutti:
        # forziamo stages=1 anche se in schermata è attivo solo S11.
        if self._active_engine is not None and getattr(self._active_engine, "cal_points", None):
            if len(self._active_engine.ports) >= 2:
                stages = 1
        s = SweepSettings(
            f_start               = int(c["start"]),
            f_stop                = int(c["stop"]),
            points                = int(c["points"]),
            if_bandwidth          = int(c["ifbw"]),
            cdbm_excitation_start = int(c["level"] * 100),
            cdbm_excitation_stop  = int(c["level"] * 100),
            stages                = stages,
            port1_stage           = 0,
            port2_stage           = 1,
            suppress_peaks        = True,
            fixed_power           = True,
        )
        self._drv.set_sweep(s)
        self._expected_points = int(c["points"])
        self._sweep_buf.clear()
        self._reset_frame()
        self._dirty = False

    @Slot(str)
    def setStatusMode(self, mode: str):
        """off | periodic (3 s, a tratti) | continuous (a ogni sweep: puo' rallentare lo sweep)."""
        self._st_mode = mode

    def _status_dict(self, st, ts):
        d = dataclasses.asdict(st)
        d["raw_hex"] = d.pop("raw", b"").hex(" ")
        d["t"] = ts
        return d

    def _status_tick(self):
        now = time.monotonic()
        if self._demo or not self._drv:
            if now - self._st_last >= 1.0:
                self._st_last = now
                tt = time.time()
                self.deviceStatus.emit(dict(
                    t=tt, hw_version=1, ext_ref_avail=False, ext_ref_use=False, fpga_ok=True,
                    source_locked=True, lo_locked=True, adc_overload=False, unlevel=False,
                    temp_source=41 + 2.5 * math.sin(tt / 40), temp_lo=43 + 2.0 * math.sin(tt / 55 + 1),
                    temp_mcu=39 + 1.5 * math.sin(tt / 70), supply_v=None, supply_i=None))
            return
        drv, mode = self._drv, self._st_mode
        ser, st, ts = drv.get_status()
        if ser != self._st_ser and st is not None:
            self._st_ser = ser
            self.deviceStatus.emit(self._status_dict(st, ts))
        if mode == "off":
            if self._st_active:
                drv.stop_status_updates(); self._st_active = False
        elif mode == "continuous":
            if not self._st_active:
                drv.start_status_updates(); self._st_active = True
        else:                                        # periodic: accende il flag finche' arriva 1 stato
            if self._st_active:
                if ser != self._st_start_ser or now - self._st_t0 > 2.5:
                    drv.stop_status_updates(); self._st_active = False; self._st_last = now
            elif now - self._st_last >= self._st_period:
                self._st_start_ser, self._st_t0 = ser, now
                drv.start_status_updates(); self._st_active = True

    def _tick(self):
        try:
            self._status_tick()
        except Exception as e:
            log.debug("status tick: %s", e)
        if not self._running:
            return
        if self._demo or not self._drv:
            self._emit_demo(); return
        try:
            if self._dirty:
                self._push_config()
            self._poll_frame()
        except Exception as e:
            self._handle_error(e)

    # ── frame progressivo ──────────────────────────────────────────────────
    def _wanted_names(self) -> list[str]:
        eng = self._active_engine
        if eng is not None and getattr(eng, "cal_points", None) and len(eng.ports) >= 2:
            return ["S11", "S21", "S12", "S22"]   # la correzione 2-port li usa tutti
        return list(self._cfg["traces"])

    def _reset_frame(self):
        c = self._cfg
        n = int(c["points"])
        self._frame_n = n
        self._frame_f = np.linspace(float(c["start"]), float(c["stop"]), n)
        self._frame = {}
        self._applied_id = None
        self._applied_n = 0
        self._frame_dirty = False
        if self._drv:
            self._last_serial = self._drv.get_progress()[0]

    def _apply_points(self, items):
        n = self._frame_n
        if n <= 1 or self._frame_f is None:
            return
        f = self._frame_f
        tol = max(1.0, 0.5 * (f[-1] - f[0]) / (n - 1))
        names = self._wanted_names()
        for pn, sp in items:
            if pn >= n or abs(sp["frequency"] - f[pn]) > tol:
                continue   # punto di una configurazione precedente
            for name in names:
                v = sp.get(name)
                if v is None:
                    continue
                a = self._frame.get(name)
                if a is None:
                    a = self._frame[name] = np.full(n, complex(np.nan, np.nan))
                a[pn] = v
            self._frame_dirty = True

    def _poll_frame(self):
        """Aggiorna il frame con i punti arrivati e lo emette (max ~25 fps).
        Durante uno sweep lento la traccia si aggiorna progressivamente."""
        drv = self._drv
        if drv is None or self._frame_f is None:
            return
        serial, full, cur_id, cur_items = drv.get_progress()
        completed = False
        if serial != self._last_serial:
            self._rate_n += serial - self._last_serial
            self._last_serial = serial
            if full is not None:
                items = list(full.items())
                if id(full) == self._applied_id:
                    items = items[self._applied_n:]
                self._apply_points(items)
                completed = True
            self._applied_id = None
            self._applied_n = 0
        if cur_id != self._applied_id:
            self._applied_id = cur_id
            self._applied_n = 0
        if len(cur_items) > self._applied_n:
            self._apply_points(cur_items[self._applied_n:])
            self._applied_n = len(cur_items)

        now = time.monotonic()
        if self._frame_dirty and (completed or now - self._last_emit >= 0.04):
            self._emit_frame()
            self._last_emit = now
        if now - self._rate_t0 >= 1.0:
            self.sweepRate.emit(self._rate_n / (now - self._rate_t0))
            self._rate_t0, self._rate_n = now, 0

    def _emit_frame(self):
        self._frame_dirty = False
        names = [k for k, a in self._frame.items() if not np.all(np.isnan(a))]
        if not names:
            return
        mask = np.ones(self._frame_n, dtype=bool)
        for k in names:
            mask &= np.isfinite(self._frame[k])
        if not mask.any():
            return
        freqs = self._frame_f[mask].copy()
        out = {k: self._frame[k][mask].copy() for k in names}
        eng = self._active_engine
        if eng is not None and eng.cal_points:
            try:
                out = eng.correct(freqs, out)
            except Exception as e:
                log.warning("Errore applicazione cal: %s", e)
        self.sweepReady.emit(freqs, out)

    def _emit_from_list(self, data: list[dict]):
        if not data:
            return
        freqs = np.array([d["frequency"] for d in data], dtype=float)
        traces_out: dict[str, np.ndarray] = {}
        for name in self._cfg["traces"]:
            vals = [d.get(name, complex(float("nan"))) for d in data]
            arr = np.array(vals, dtype=complex)
            if not np.all(np.isnan(arr)):
                traces_out[name] = arr
        if not traces_out:
            return
        # applica calibrazione se disponibile
        if self._active_engine is not None and self._active_engine.cal_points:
            try:
                traces_out = self._active_engine.correct(freqs, traces_out)
            except Exception as e:
                log.warning("Errore applicazione cal: %s", e)
        self.sweepReady.emit(freqs, traces_out)

    def _handle_error(self, e: Exception):
        log.error("Errore USB driver: %s", e)
        self._drv = None; self._demo = True
        self.status.emit(f"Errore USB: {e} → demo")
        self.connectedChanged.emit(False, f"Connessione persa: {e}")

    def _emit_demo(self):
        c = self._cfg
        f = np.linspace(c["start"], c["stop"], int(c["points"]))
        fc = 0.5 * (c["start"] + c["stop"])
        bw = 0.15 * (c["stop"] - c["start"]) + 1.0
        s21 = np.exp(-0.5 * ((f - fc) / bw) ** 2) * np.exp(1j * 6 * f / fc)
        s21 *= (1 + 0.02 * np.random.randn(len(f)))
        x   = (f - fc) / (0.5 * bw)
        s11 = (1j * x) / (1 + 1j * x) * 0.8 - 0.1
        out = {}
        for tr in c["traces"]:
            out[tr] = s21 if tr in ("S21", "S12") else s11
        self.sweepReady.emit(f, out)
