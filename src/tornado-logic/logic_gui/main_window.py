"""Finestra principale di Tornado Logic."""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDoubleSpinBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu,
    QMessageBox, QScrollArea, QSplitter, QStatusBar, QTabWidget, QVBoxLayout, QWidget,
)

from logic_core import (FX2_SAMPLERATES, AcquisitionConfig, Capture, DecoderConfig, LogicDevice, RunLog, TriggerTimeout, export_csv,
                        export_vcd, load_npz, run_decoders, run_measures, save_npz, verify_walk)
from logic_core.analysis import DecodeResult, MeasureResult
from tornado_ui import Card, Ribbon, dim, settings, shortcut

from .devices import DEVICE_KINDS, create_device
from .fmt import fmt_si
from .pinout import PinoutPanel
from .panels import (AcquirePanel, DecodeTable, DecoderPanel, GlitchTable, MeasureTable, MessageTable, SerialPanel,
                     note, serial_summary)
from .waveform import WaveformView
from .tester_panel import TesterPanel
from .worker import Worker


@dataclass
class _Miss:
    """Giro senza cattura: il trigger non è avvenuto nella finestra (in modo continuo non ferma il ciclo)."""
    message: str


@dataclass
class _Run:
    """Stato di una sessione di acquisizione (singola o continua)."""
    kind: str
    cfg: object
    continuous: bool
    pause_s: float
    log: RunLog | None = None
    cycle: int = 0
    misses: int = 0
    stopping: bool = False
    t_start: datetime | None = None
    wait_since: float | None = None   # inizio dell'attesa che le analisi del ciclo precedente finiscano


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Tornado Logic")
        self.resize(1500, 900)

        self.cap: Capture | None = None
        self._device: LogicDevice | None = None
        self._device_kind: str | None = None
        self._workers: set[Worker] = set()
        self._acquiring = False
        self._run: _Run | None = None
        self._open_logs: set[RunLog] = set()
        self._tok = {"dec": 0, "meas": 0}

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.acq = AcquirePanel(DEVICE_KINDS, FX2_SAMPLERATES)
        self.dec = DecoderPanel()
        self.ser = SerialPanel()
        self.pinout = PinoutPanel(lambda: settings())
        self.wave = WaveformView()
        self._build_ribbon(root)

        body = QWidget()
        bl = QHBoxLayout(body)
        bl.setContentsMargins(12, 12, 12, 8)
        bl.setSpacing(10)
        root.addWidget(body, 1)
        split = QSplitter(Qt.Orientation.Horizontal)
        bl.addWidget(split)

        left = QTabWidget()
        acq_scroll = QScrollArea()          # con il riquadro Log il pannello supera l'altezza di una finestra bassa
        acq_scroll.setWidgetResizable(True)
        acq_scroll.setFrameShape(QFrame.Shape.NoFrame)
        acq_scroll.setWidget(self.acq)
        left.addTab(acq_scroll, "Acquisizione")
        left.addTab(self.dec, "Decoder")
        left.addTab(self.ser, "Seriale")
        left.addTab(self.pinout, "Pinout")
        left.setMinimumWidth(320)
        left.setMaximumWidth(420)
        split.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)
        head = QHBoxLayout()
        self.lbl_info = dim("Nessuna cattura")
        self.lbl_info.setWordWrap(False)
        self.lbl_cur = QLabel("")
        mono = QFont(QApplication.font())
        mono.setFamilies(["Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono", "monospace"])
        self.lbl_cur.setFont(mono)
        head.addWidget(self.lbl_info, 1)
        head.addWidget(self.lbl_cur, 0, Qt.AlignmentFlag.AlignRight)
        rv.addLayout(head)

        self.vsplit = vsplit = QSplitter(Qt.Orientation.Vertical)
        wave_card = Card()
        wl = QVBoxLayout(wave_card)
        wl.setContentsMargins(6, 6, 6, 6)
        wl.addWidget(self.wave)
        vsplit.addWidget(wave_card)
        vsplit.addWidget(self._build_bottom())
        vsplit.setStretchFactor(0, 3)
        vsplit.setStretchFactor(1, 1)
        vsplit.setSizes([620, 240])
        rv.addWidget(vsplit, 1)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([340, 1160])

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Pronto")
        self.acq.log_dir.setText(str(settings().value("logic/logdir", self.acq.log_dir.text())))

        # passaggio al ciclo successivo (con la pausa scelta) e attesa che le analisi in corso finiscano
        self._t_next = QTimer(self)
        self._t_next.setSingleShot(True)
        self._t_next.timeout.connect(self._advance)

        # debounce delle analisi
        self._t_dec = QTimer(self)
        self._t_dec.setSingleShot(True)
        self._t_dec.setInterval(300)
        self._t_dec.timeout.connect(self._run_decoders)
        self._t_meas = QTimer(self)
        self._t_meas.setSingleShot(True)
        self._t_meas.setInterval(150)
        self._t_meas.timeout.connect(self._run_measures)

        self.acq.acquireRequested.connect(self.on_acquire)
        self.acq.stopRequested.connect(self.on_stop)
        self.pinout.verifyRequested.connect(self.on_verify)
        self.acq.channelsChanged.connect(self._on_channels)
        self.dec.changed.connect(self._t_dec.start)
        self.ser.changed.connect(self._t_dec.start)
        self.msg_table.activatedRange.connect(lambda a, b: self._goto_samples(a, b))
        self.wave.cursorsMoved.connect(self._on_cursors)
        self.dec_table.activatedRange.connect(lambda a, b: self._goto_samples(a, b))
        self.glitch_table.activatedSample.connect(lambda i: self._goto_samples(i, i))
        self.chk_range.toggled.connect(self._t_meas.start)
        self.spin_glitch.valueChanged.connect(self._t_meas.start)

        shortcut(self, "F5", self.on_acquire)
        shortcut(self, "Ctrl+O", self.on_open)
        shortcut(self, "Ctrl+S", self.on_save)
        self._on_channels()
        self.dec.set_channel_names(self.acq.names())
        self.ser.set_channel_names(self.acq.names())

    # ------------------------------------------------------------------ UI
    def _build_ribbon(self, root: QVBoxLayout) -> None:
        rib = Ribbon()
        page = rib.add_tab("Cattura")
        g = page.add_group("Acquisizione")
        self.act_acquire = g.add_large("Acquisisci", "play", self.on_acquire, tip="Acquisisci (F5)")
        self.act_stop = g.add_large("Stop", "close", self.on_stop)
        self.act_stop.setEnabled(False)
        g = page.add_group("File")
        g.add_large("Apri", "open", self.on_open, tip="Apri cattura (Ctrl+O)")
        g.add_large("Salva", "save", self.on_save, tip="Salva cattura (Ctrl+S)")
        menu = QMenu(self)
        menu.addAction("CSV (cambi di livello)…", self.on_export_csv)
        menu.addAction("VCD (GTKWave, PulseView)…", self.on_export_vcd)
        g.add_large("Esporta", "export", None, menu=menu)

        page = rib.add_tab("Vista")
        g = page.add_group("Zoom")
        g.add_large("Adatta", "grow", self.wave.fit)
        g.add_large("Zoom +", "zoom_in", lambda: self.wave.zoom_by(0.5))
        g.add_large("Zoom −", "zoom_out", lambda: self.wave.zoom_by(2.0))
        g = page.add_group("Navigazione")
        g.add_large("Al trigger", "flag", self._goto_trigger)
        root.addWidget(rib)

    def _build_bottom(self) -> QTabWidget:
        tabs = QTabWidget()

        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(8, 8, 8, 8)
        self.dec_table = DecodeTable()
        self.lbl_dec_note = note()
        v.addWidget(self.dec_table, 1)
        v.addWidget(self.lbl_dec_note)
        tabs.addTab(page, "Decoder")

        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(8, 8, 8, 8)
        self.lbl_ser = note()
        self.lbl_ser.setWordWrap(True)
        self.msg_table = MessageTable()
        v.addWidget(self.lbl_ser)
        v.addWidget(self.msg_table, 1)
        tabs.addTab(page, "Seriale")

        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(8, 8, 8, 8)
        self.chk_range = QCheckBox("Solo tra i cursori A e B")
        self.meas_table = MeasureTable()
        v.addWidget(self.chk_range)
        v.addWidget(self.meas_table, 1)
        tabs.addTab(page, "Misure")

        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(8, 8, 8, 8)
        row = QHBoxLayout()
        row.addWidget(QLabel("Impulsi più brevi di"))
        self.spin_glitch = QDoubleSpinBox()
        self.spin_glitch.setRange(1.0, 100000.0)
        self.spin_glitch.setDecimals(0)
        self.spin_glitch.setValue(100.0)
        self.spin_glitch.setSuffix(" ns")
        row.addWidget(self.spin_glitch)
        row.addStretch(1)
        self.glitch_table = GlitchTable()
        self.lbl_glitch_note = note()
        v.addLayout(row)
        v.addWidget(self.glitch_table, 1)
        v.addWidget(self.lbl_glitch_note)
        tabs.addTab(page, "Glitch")

        self.tester = TesterPanel(lambda: settings())
        tabs.addTab(self.tester, "Tester")
        self._bottom_tabs = tabs
        tabs.currentChanged.connect(self._on_bottom_tab)
        return tabs

    def _on_bottom_tab(self, i: int) -> None:
        """Il pannello Tester ha bisogno di più altezza della tabella dei decoder."""
        if self._bottom_tabs.widget(i) is self.tester:
            sizes = self.vsplit.sizes()
            need = 330
            if len(sizes) == 2 and sizes[1] < need:
                tot = sum(sizes)
                self.vsplit.setSizes([max(150, tot - need), need])

    # ------------------------------------------------------ stato / errori
    def status(self, msg: str) -> None:
        self.statusBar().showMessage(msg)

    def notify_error(self, msg: str) -> None:  # sovrascrivibile nei test
        QMessageBox.warning(self, "Tornado Logic", msg)

    def _error(self, msg: str) -> None:
        self.status(msg)
        self.notify_error(msg)

    def _set_busy(self, busy: bool) -> None:
        self._acquiring = busy
        self.pinout.btn_verify.setEnabled(not busy)
        self.acq.set_busy(busy)
        self.act_acquire.setEnabled(not busy)
        self.act_stop.setEnabled(busy)

    def _start(self, w: Worker) -> None:
        self._workers = {x for x in self._workers if not x.isFinished()}
        self._workers.add(w)
        w.done.connect(self._on_done)
        w.failed.connect(self._on_failed)
        w.start()

    def closeEvent(self, e) -> None:  # noqa: N802
        self.tester.shutdown()
        self._t_next.stop()
        if self._run is not None:
            self._run.stopping = True
        if self._device is not None:
            self._device.abort()
        for w in list(self._workers):
            w.wait(5000)
        if self._run is not None:
            self._finish_run()
        for log in list(self._open_logs):
            log.close()
        self._open_logs.clear()
        if self._device is not None:
            self._device.close()
        super().closeEvent(e)

    # ---------------------------------------------------------- acquisizione
    def _acquire_job(self, kind: str, cfg, tolerant: bool = False):
        if self._device is None or self._device_kind != kind:
            if self._device is not None:
                self._device.close()
            dev = create_device(kind)
            dev.open()
            self._device, self._device_kind = dev, kind
        try:
            return self._device.acquire(cfg)
        except TriggerTimeout as e:
            if tolerant:
                return _Miss(str(e))
            raise

    def _log_job(self, log: RunLog, cycle: int, t0: datetime, cap: Capture, dcfg: DecoderConfig,
                 channels: tuple[int, ...], glitch_s: float) -> None:
        log.log_capture(cycle, t0, cap, dcfg, channels, glitch_s)

    def _workers_idle(self) -> bool:
        return all(w.isFinished() for w in self._workers)

    def on_acquire(self) -> None:
        if self._acquiring:
            return
        try:
            cfg = self.acq.config()
            opts = self.acq.log_options()
        except Exception as e:  # noqa: BLE001 - ConfigError e valori non validi
            self._error(str(e))
            return
        log = None
        if opts is not None:
            try:
                log = RunLog(opts)
            except (OSError, ValueError) as e:
                self._error(f"Impossibile creare il log: {e}")
                return
            self._open_logs.add(log)
            settings().setValue("logic/logdir", opts.folder)
        run = _Run(self.acq.device_kind(), cfg, self.acq.continuous(), self.acq.pause_s(), log)
        self._run = run
        self._set_busy(True)
        if log is not None:
            t = cfg.trigger
            trig = "nessuno" if t is None else f"{self.acq.names()[t.channel]} {t.mode} (pre {t.pretrigger:.0%})" + (" [sigrok]" if cfg.hw_trigger else "")
            log.event("INFO", f"Avvio: {run.kind} · {fmt_si(cfg.samplerate, 'S/s')} · {cfg.n_samples} campioni · "
                              f"trigger {trig} · {'continua' if run.continuous else 'singola'}")
        self._next_cycle()

    def _next_cycle(self) -> None:
        run = self._run
        if run is None or run.stopping:
            return
        run.cycle += 1
        run.t_start = datetime.now()
        self.status(f"Acquisizione in corso… (ciclo {run.cycle})" if run.continuous else "Acquisizione in corso…")
        self._start(Worker(("acq", run.cycle), self._acquire_job, run.kind, run.cfg, run.continuous))

    # --------------------------------------------- verifica collegamenti (walk)
    VERIFY_RATE, VERIFY_SAMPLES = 20e3, 130_000       # 6,5 s: un giro e mezzo del modo walk (4 s) + margine

    def _verify_job(self, kind: str):
        cap = self._acquire_job(kind, AcquisitionConfig(self.VERIFY_RATE, self.VERIFY_SAMPLES, timeout_s=15.0))
        return cap, verify_walk(cap)

    def on_verify(self) -> None:
        if self._acquiring:
            return
        self._set_busy(True)
        self.pinout.set_verify_busy(True)
        self._verify_prev = self.tester.ensure_walk()      # tester collegato: lo mette in walk da solo
        self.status("Verifica collegamenti in corso (circa 7 s)…" if self._verify_prev is None else
                    "Tester in modo walk, verifica collegamenti in corso (circa 7 s)…")
        job = Worker(("verify",), self._verify_job, self.acq.device_kind())
        if self._verify_prev is None:
            self._start(job)
        else:
            QTimer.singleShot(400, lambda: self._start(job))   # il tempo di far partire il walk

    _verify_prev: str | None = None

    def _verify_finished(self) -> None:
        prev, self._verify_prev = self._verify_prev, None
        self.tester.restore_mode(prev)

    def on_stop(self) -> None:
        run = self._run
        if run is not None:
            if not run.stopping and run.log is not None:
                run.log.event("INFO", "Stop richiesto dall'utente")
            run.stopping = True
            self._t_next.start(0)       # se si è in pausa tra due catture, chiude subito
        if self._device is not None:
            self._device.abort()
        self.status("Interruzione richiesta…")

    def _after_cycle(self) -> None:
        run = self._run
        if run is None:
            return
        if not run.continuous or run.stopping:
            self._finish_run()
        else:
            self._t_next.start(max(50, int(run.pause_s * 1000)))

    def _advance(self) -> None:
        run = self._run
        if run is None:
            return
        if run.stopping:
            if self._workers_idle():
                self._finish_run()
            else:
                self._t_next.start(50)
            return
        if not self._workers_idle():     # contropressione: analisi e log del ciclo precedente ancora in corso
            if run.wait_since is None:
                run.wait_since = time.monotonic()
            self._t_next.start(50)
            return
        if run.wait_since is not None:
            waited, run.wait_since = time.monotonic() - run.wait_since, None
            if waited >= 1.0:
                self.status(f"Ciclo {run.cycle}: attese le analisi per {waited:.1f} s")
                if run.log is not None:
                    run.log.note(run.cycle, f"il ciclo successivo è partito {waited:.1f} s dopo (analisi e log in corso)")
        self._next_cycle()

    def _finish_run(self) -> None:
        run, self._run = self._run, None
        self._t_next.stop()
        self._set_busy(False)
        if run is not None and run.log is not None:
            self._close_log(run.log, run)

    def _close_log(self, log: RunLog, run: _Run) -> None:
        if not self._workers_idle():       # i job di log in volo devono finire prima di chiudere i file
            QTimer.singleShot(50, lambda: self._close_log(log, run))
            return
        log.event("INFO", f"Fine: {log.cycles} catture registrate, {log.misses} trigger mancati · cartella {log.dir}")
        log.close()
        self._open_logs.discard(log)
        self.status(f"Log salvato in {log.dir}")

    # ---------------------------------------------------------- risultati job
    def _on_done(self, tag, res) -> None:
        kind = tag[0]
        if kind == "acq":
            run = self._run
            if isinstance(res, _Miss):
                run.misses += 1
                self.status(f"Ciclo {run.cycle}: trigger non avvenuto (mancati: {run.misses})")
                if run.log is not None:
                    run.log.miss(run.cycle, run.t_start, res.message)
                self._after_cycle()
                return
            cap: Capture = res
            self.load_capture(cap)
            if run.continuous:
                tries = cap.meta.get("attempts", 1)
                self.status(f"Ciclo {run.cycle}: {len(cap)} campioni a {fmt_si(cap.samplerate, 'S/s')}"
                            + (f" · {tries} tentativi" if tries > 1 else "")
                            + (f" · trigger mancati: {run.misses}" if run.misses else ""))
            else:
                self.status(f"Acquisizione completata: {len(cap)} campioni a {fmt_si(cap.samplerate, 'S/s')}"
                            + (" · trigger software (sigrok non l'ha accettato)"
                               if str(cap.meta.get("trigger", "")).startswith("software (ripiego") else ""))
            if run.log is not None:
                dcfg: DecoderConfig = self.dec.config()
                dcfg.serial = self.ser.config()
                logged = replace(cap, channel_names=self.acq.names())
                self._start(Worker(("log", run.cycle), self._log_job, run.log, run.cycle, run.t_start, logged,
                                   dcfg, tuple(self.acq.enabled_channels()), self.spin_glitch.value() * 1e-9))
            self._after_cycle()
        elif kind == "log":
            pass
        elif kind == "verify":
            self._verify_finished()
            self._set_busy(False)
            cap, results = res
            self.pinout.set_verify_busy(False)
            ok = self.pinout.show_verify(results)
            self.load_capture(cap)
            self.status("Verifica collegamenti: tutto a posto" if ok else "Verifica collegamenti: ci sono canali da controllare")
        elif kind == "dec":
            if tag[1] == self._tok["dec"]:
                self._show_decode(res)
        elif kind == "meas":
            if tag[1] == self._tok["meas"]:
                self._show_measure(res)
        elif kind == "export":
            self.status(f"Esportazione completata: {tag[1]} ({res} cambi di livello)")

    def _on_failed(self, tag, msg: str) -> None:
        kind = tag[0]
        if kind == "acq":
            run = self._run
            stopped = run is not None and run.stopping
            if run is not None:
                run.stopping = True
                if run.log is not None and not stopped:
                    run.log.event("ERRORE", f"Acquisizione non riuscita: {msg}")
            self._finish_run()
            if stopped:
                self.status("Acquisizione interrotta")
            else:
                self._error(f"Acquisizione non riuscita: {msg}")
        elif kind == "verify":
            self._verify_finished()
            self._set_busy(False)
            self.pinout.set_verify_busy(False)
            self.pinout.lbl_verify.setVisible(False)
            if "interrotta" in msg:
                self.status("Verifica interrotta")
            else:
                self._error(f"Verifica non riuscita: {msg}")
        elif kind == "log":
            run = self._run
            if run is not None:
                run.stopping = True
                if run.log is not None:
                    run.log.event("ERRORE", f"Log non riuscito: {msg}")
                self._t_next.start(0)
            self._error(f"Scrittura del log non riuscita: {msg}")
        elif kind == "export":
            self._error(f"Esportazione non riuscita: {msg}")
        else:
            self.status(f"Analisi non riuscita: {msg}")

    def load_capture(self, cap: Capture) -> None:
        self.cap = cap
        self.wave.set_capture(cap)
        trig = f" · trigger a {fmt_si(cap.trigger_time, 's')}" if cap.trigger_index is not None else ""
        self.lbl_info.setText(f"{fmt_si(cap.samplerate, 'S/s')} · {fmt_si(len(cap), 'campioni')} · "
                              f"{fmt_si(cap.duration, 's')}{trig}")
        self._run_decoders()
        self._run_measures()

    # --------------------------------------------------------------- analisi
    def _run_decoders(self) -> None:
        if self.cap is None:
            return
        self._tok["dec"] += 1
        cfg: DecoderConfig = self.dec.config()
        cfg.serial = self.ser.config()
        self._start(Worker(("dec", self._tok["dec"]), run_decoders, self.cap, cfg))

    def _show_decode(self, res: DecodeResult) -> None:
        self.wave.set_lanes(res.lanes)
        truncated = self.dec_table.set_data(res.lanes, self.cap.samplerate)
        total = sum(len(l.annotations) for l in res.lanes)
        parts = []
        if truncated:
            parts.append(f"Elenco limitato alle prime {DecodeTable.MAX_ROWS} voci su {total}.")
        parts += res.errors
        self.lbl_dec_note.setText("  ".join(parts))
        self.lbl_dec_note.setVisible(bool(parts))
        ser = res.serial
        self.ser.set_detected(ser)
        if ser is None:
            self.msg_table.set_data([], self.cap.samplerate)
            errs = [e for e in res.errors if e.startswith("Seriale:")]
            self.lbl_ser.setText("\n".join(errs) if errs else
                                 "" if self.ser.g.isChecked() else "Analizzatore seriale disattivato: spuntare «Abilita analizzatore seriale» nella scheda Seriale (pannello a sinistra).")
        else:
            cut = self.msg_table.set_data(ser.messages, self.cap.samplerate, ser.modbus)
            self.lbl_ser.setText(serial_summary(ser) + (f"\nElenco limitato ai primi {MessageTable.MAX_ROWS} messaggi."
                                                         if cut else ""))
        self.lbl_ser.setVisible(bool(self.lbl_ser.text()))

    def _run_measures(self) -> None:
        if self.cap is None:
            return
        self._tok["meas"] += 1
        rng = None
        if self.chk_range.isChecked():
            a, b = sorted(self.wave.cursors())
            fs = self.cap.samplerate
            rng = (int(a * fs), int(b * fs))
        self._start(Worker(("meas", self._tok["meas"]), run_measures, self.cap,
                           tuple(self.acq.enabled_channels()), self.spin_glitch.value() * 1e-9, rng))

    def _show_measure(self, res: MeasureResult) -> None:
        names = self.acq.names()
        self.meas_table.set_data(res.stats, names)
        self.glitch_table.set_data(res.glitches, names, self.cap.samplerate)
        self.lbl_glitch_note.setText("Elenco limitato alle prime 10000 voci." if res.glitches_truncated else "")
        self.lbl_glitch_note.setVisible(res.glitches_truncated)

    # ----------------------------------------------------------- interazione
    def _on_channels(self) -> None:
        names = self.acq.names()
        self.wave.set_channels(self.acq.enabled_channels(), names)
        self.dec.set_channel_names(names)
        self.ser.set_channel_names(names)
        if self.cap is not None:
            self._t_meas.start()

    def _on_cursors(self, a: float, b: float) -> None:
        d = abs(b - a)
        self.lbl_cur.setText(f"A {fmt_si(a, 's')}   B {fmt_si(b, 's')}   Δ {fmt_si(d, 's')}   "
                             f"1/Δ {fmt_si(1 / d, 'Hz') if d > 0 else '—'}")
        if self.chk_range.isChecked():
            self._t_meas.start()

    def _goto_samples(self, start: int, end: int) -> None:
        if self.cap is not None:
            fs = self.cap.samplerate
            self.wave.center_on(start / fs, end / fs)

    def _goto_trigger(self) -> None:
        if self.cap is None or self.cap.trigger_time is None:
            self.status("La cattura non ha un trigger")
            return
        t, span = self.cap.trigger_time, self.cap.duration / 10
        self.wave.set_view(t - span / 2, t + span / 2)

    # ------------------------------------------------------------------- file
    def _dir(self) -> str:
        return str(settings().value("logic/dir", str(Path.home())))

    def _remember(self, path: str) -> None:
        settings().setValue("logic/dir", str(Path(path).parent))

    def _need_capture(self) -> bool:
        if self.cap is None:
            self.status("Nessuna cattura: acquisire o aprire un file")
            return False
        return True

    def on_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Apri cattura", self._dir(), "Catture (*.npz)")
        if path:
            self.open_path(path)

    def open_path(self, path: str) -> None:
        try:
            cap = load_npz(path)
        except Exception as e:  # noqa: BLE001
            self._error(f"Impossibile aprire il file: {e}")
            return
        self._remember(path)
        self.acq.set_names(cap.channel_names)
        self.load_capture(cap)
        self.status(f"Aperto {Path(path).name}")

    def on_save(self) -> None:
        if not self._need_capture():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Salva cattura", str(Path(self._dir()) / "cattura.npz"),
                                              "Catture (*.npz)")
        if path:
            self.save_path(path)

    def save_path(self, path: str) -> None:
        cap = self.cap
        cap.channel_names = self.acq.names()
        try:
            save_npz(cap, path)
        except Exception as e:  # noqa: BLE001
            self._error(f"Impossibile salvare il file: {e}")
            return
        self._remember(path)
        self.status(f"Salvato {Path(path).name}")

    def _export(self, kind: str, filt: str, fn) -> None:
        if not self._need_capture():
            return
        path, _ = QFileDialog.getSaveFileName(self, f"Esporta {kind.upper()}",
                                              str(Path(self._dir()) / f"cattura.{kind}"), filt)
        if path:
            self.export_path(path, fn)

    def export_path(self, path: str, fn) -> None:
        cap = self.cap
        cap.channel_names = self.acq.names()
        self._remember(path)
        self.status("Esportazione in corso…")
        self._start(Worker(("export", Path(path).name), fn, cap, path, tuple(self.acq.enabled_channels())))

    def on_export_csv(self) -> None:
        self._export("csv", "CSV (*.csv)", export_csv)

    def on_export_vcd(self) -> None:
        self._export("vcd", "VCD (*.vcd)", export_vcd)
