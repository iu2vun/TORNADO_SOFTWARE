"""Worker di acquisizione (thread separato, possiede il socket SCPI).

Comandi verificati sul sorgente LibreVNA v1.6.5:
  *IDN?
  :DEVice:LIST?                  → lista seriali separati da virgola
  :DEVice:CONNect [serial]       → connette; query → seriale o "Not connected"
  :DEVice:MODE VNA               → attiva modo VNA
  :VNA:SWEEP FREQUENCY
  :VNA:FREQuency:START <hz>
  :VNA:FREQuency:STOP <hz>
  :VNA:ACQuisition:POINTS <n>
  :VNA:ACQuisition:IFBW <hz>
  :VNA:ACQuisition:AVG <n>
  :VNA:STIMulus:LVL <dBm>
  :VNA:ACQuisition:FINished?     → TRUE / FALSE
  :VNA:ACQuisition:SINGLE TRUE
  :VNA:TRACE:DATA? <name>        → [f,re,im],[f,re,im],...
  :VNA:TRACE:LIST?               → nomi trace separati da virgola
  :VNA:CALibration:RESET / ADD / PORT / MEASure / ACTivate / ACTIVE? / SAVE / LOAD
"""

import time
import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal, Slot
from .scpi import ScpiClient


class VnaWorker(QObject):
    sweepReady       = Signal(object, object)   # freqs ndarray, {trace: complex ndarray}
    status           = Signal(str)
    connectedChanged = Signal(bool, str)         # ok, descrizione
    calStepDone      = Signal(int, bool)
    calFinished      = Signal(bool, str)

    TICK_MS = 120

    def __init__(self):
        super().__init__()
        self._cli: ScpiClient | None = None
        self._timer: QTimer | None   = None
        self._running = False
        self._demo    = False
        self._cfg     = dict(start=1e8, stop=6e9, points=501,
                             ifbw=1000, level=-10, avg=1,
                             traces=["S11", "S21"])
        self._dirty   = True

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
    def connectDevice(self, host: str, port: int, demo: bool):
        self._demo = demo
        if demo:
            self.connectedChanged.emit(False, "DEMO — nessun hardware")
            self._dirty = True
            return

        if self._cli:
            self._cli.close()
            self._cli = None

        try:
            cli = ScpiClient(host, port, timeout=4.0)
            cli.connect()

            # 1) identificazione
            idn = cli.query("*IDN?").strip()

            # 2) lista device USB disponibili
            dev_list = cli.query(":DEVice:LIST?").strip()

            # 3) query stato connessione attuale (CONNect è sia cmd che query)
            connected_serial = cli.query(":DEVice:CONNect?").strip()

            if connected_serial.lower() == "not connected":
                # prova a connettersi al primo device disponibile
                if dev_list and dev_list.lower() not in ("", "error"):
                    first = dev_list.split(",")[0].strip()
                    self.status.emit(f"Connessione a device {first}…")
                    cli.cmd(f":DEVice:CONNect {first}")
                    time.sleep(0.5)
                    connected_serial = cli.query(":DEVice:CONNect?").strip()

            if connected_serial.lower() == "not connected":
                self._cli  = cli
                self._demo = True
                self.connectedChanged.emit(
                    False,
                    f"LibreVNA-GUI {idn} raggiunto — device USB non trovato.\n"
                    "Collega il VNA via USB, poi premi Reconnect."
                )
                return

            # 4) attiva modo VNA
            cli.cmd(":DEVice:MODE VNA")
            self._cli   = cli
            self._dirty = True
            self.connectedChanged.emit(True, f"{idn}  |  S/N {connected_serial}")
            self.status.emit("Connesso")

        except OSError as e:
            self._cli  = None
            self._demo = True
            self.connectedChanged.emit(
                False,
                f"Impossibile connettersi a {host}:{port} — {e}\n"
                "Assicurati che LibreVNA-GUI sia aperto con SCPI Server abilitato."
            )
        except Exception as e:
            self._cli  = None
            self._demo = True
            self.connectedChanged.emit(False, f"Errore connessione: {e}")

    # ── comandi sweep ──────────────────────────────────────────────────────
    @Slot(dict)
    def applyConfig(self, cfg: dict):
        self._cfg.update(cfg)
        self._dirty = True

    @Slot(bool)
    def setRunning(self, run: bool):
        self._running = run
        self.status.emit("Continuous" if run else "Hold")

    @Slot()
    def triggerSingle(self):
        if self._demo or not self._cli:
            self._emit_demo()
            self.status.emit("Single (demo)")
            return
        try:
            if self._dirty:
                self._push_config()
            self._cli.cmd(":VNA:ACQuisition:SINGLE TRUE")
            t0 = time.monotonic()
            while self._cli.query(":VNA:ACQuisition:FINished?").strip().upper() == "FALSE":
                time.sleep(0.05)
                if time.monotonic() - t0 > 60:
                    break
            self._read_and_emit()
            self.status.emit("Single completato")
        except Exception as e:
            self._handle_error(e)

    # ── calibrazione ───────────────────────────────────────────────────────
    @Slot(list)
    def calSetup(self, steps: list):
        if self._demo or not self._cli:
            self.status.emit("Cal setup (demo)"); return
        try:
            self._cli.cmd(":VNA:CALibration:RESET")
            for i, (mtype, ports) in enumerate(steps):
                self._cli.cmd(f":VNA:CALibration:ADD {mtype}")
                self._cli.cmd(f":VNA:CALibration:PORT {i} " + " ".join(map(str, ports)))
            self.status.emit(f"Cal: {len(steps)} step pronti")
        except Exception as e:
            self.status.emit(f"Errore cal setup: {e}")

    @Slot(int)
    def calMeasureStep(self, idx: int):
        if self._demo or not self._cli:
            self.calStepDone.emit(idx, True); return
        try:
            self._cli.cmd(f":VNA:CALibration:MEASure {idx}")
            t0 = time.monotonic(); time.sleep(0.3)
            while self._cli.query(":VNA:ACQuisition:FINished?").strip().upper() == "FALSE":
                time.sleep(0.1)
                if time.monotonic() - t0 > 60:
                    break
            self.calStepDone.emit(idx, True)
        except Exception as e:
            self.status.emit(f"Errore step {idx}: {e}")
            self.calStepDone.emit(idx, False)

    @Slot(str)
    def calActivate(self, short: str):
        if self._demo or not self._cli:
            self.calFinished.emit(True, f"{short} (demo)"); return
        try:
            self._cli.cmd(f":VNA:CALibration:ACTivate {short}")
            active = self._cli.query(":VNA:CALibration:ACTIVE?").strip()
            self.calFinished.emit(active.upper() == short.upper(), active)
        except Exception as e:
            self.calFinished.emit(False, str(e))

    @Slot(str)
    def calSave(self, path: str):
        if self._demo or not self._cli:
            self.status.emit("Cal save (demo)"); return
        try:
            self._cli.cmd(f":VNA:CALibration:SAVE {path}")
            self.status.emit(f"Cal salvata: {path}")
        except Exception as e:
            self.status.emit(f"Errore save cal: {e}")

    @Slot(str)
    def calLoad(self, path: str):
        if self._demo or not self._cli:
            self.calFinished.emit(True, "loaded (demo)"); return
        try:
            ok = self._cli.query(f":VNA:CALibration:LOAD {path}").strip().upper() == "TRUE"
            active = self._cli.query(":VNA:CALibration:ACTIVE?").strip() if ok else ""
            self.calFinished.emit(ok, active if ok else "load fallita")
        except Exception as e:
            self.calFinished.emit(False, str(e))

    # ── ciclo interno ──────────────────────────────────────────────────────
    def _push_config(self):
        c, cli = self._cfg, self._cli
        cli.cmd(":VNA:SWEEP FREQUENCY")
        cli.cmd(f":VNA:STIMulus:LVL {c['level']}")
        cli.cmd(f":VNA:ACQuisition:IFBW {int(c['ifbw'])}")
        cli.cmd(f":VNA:ACQuisition:AVG {int(c['avg'])}")
        cli.cmd(f":VNA:ACQuisition:POINTS {int(c['points'])}")
        cli.cmd(f":VNA:FREQuency:START {int(c['start'])}")
        cli.cmd(f":VNA:FREQuency:STOP {int(c['stop'])}")
        self._dirty = False

    def _read_and_emit(self):
        """Legge tutte le tracce configurate e le emette solo se coerenti.

        Il VNA sweepa in continuo: tra una query e l'altra può aver completato
        qualche punto in più. Allineiamo tutti gli array alla lunghezza minima
        comune prima di emettere, così freqs e ogni s hanno sempre la stessa shape.
        """
        out: dict = {}
        all_freqs: list = []

        for tr in self._cfg["traces"]:
            raw = self._cli.query(f":VNA:TRACE:DATA? {tr}")
            if raw.strip().upper() in ("ERROR", "EMPTY", ""):
                continue
            try:
                f, s = ScpiClient.parse_trace(raw)
            except Exception:
                continue
            all_freqs.append(f)
            out[tr] = s

        if not out or not all_freqs:
            return

        # allinea alla lunghezza minima (race condition sweep in transizione)
        n_min = min(len(f) for f in all_freqs)
        freqs = all_freqs[0][:n_min]
        for tr in list(out):
            out[tr] = out[tr][:n_min]

        if n_min > 0:
            self.sweepReady.emit(freqs, out)

    def _tick(self):
        if not self._running:
            return
        if self._demo or not self._cli:
            self._emit_demo(); return
        try:
            if self._dirty:
                self._push_config()
            self._read_and_emit()
        except Exception as e:
            self._handle_error(e)

    def _handle_error(self, e: Exception):
        self._cli = None; self._demo = True
        self.status.emit(f"Errore: {e} → demo")
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
