"""Tornado-Term - terminale seriale ANSI/VT100 con due sessioni indipendenti (due COM dello stesso dispositivo)."""
from __future__ import annotations

import sys

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QLabel, QMainWindow, QScrollArea, QSplitter, QTabWidget,
    QToolBar, QVBoxLayout, QWidget,
)

from ...core.theme import P, apply_theme
from .macros import PORTABLE, MacroEditor, load_macros, save_macros
from .serial_core import PortWatcher
from .session import Session

N_SESSIONS = 2


class TerminalFrame(QWidget):
    """Terminale con intestazione che evidenzia la sessione attiva."""

    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self.header = QLabel()
        self.header.setContentsMargins(6, 2, 6, 2)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.header)
        lay.addWidget(session.term, 1)
        self.set_active(False)

    def set_active(self, on: bool):
        bg, fg = (P.accent, P.on_accent) if on else (P.raised, P.text_dim)
        self.header.setStyleSheet(f"background:{bg};color:{fg};font-weight:bold;border-bottom:1px solid {P.border};")
        self.header.setText(self.session.title)

    def refresh_title(self):
        self.header.setText(self.session.title)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tornado-Term")
        self.settings = QSettings("TornadoLaboratori", "Tornado-Term")
        self.watcher = PortWatcher(1000, self)
        self.sessions = [Session(i + 1, self.settings, self._port_busy, self) for i in range(N_SESSIONS)]
        self.active = self.sessions[0]
        self.macros = load_macros(self.settings)

        self.frames = [TerminalFrame(s) for s in self.sessions]
        self.split = QSplitter(Qt.Orientation.Horizontal)
        for f in self.frames:
            self.split.addWidget(f)
        self.split.setChildrenCollapsible(False)
        self.setCentralWidget(self.split)

        self.tabs = QTabWidget()
        for s in self.sessions:
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setFrameShape(QScrollArea.Shape.NoFrame)
            sc.setWidget(s.panel)
            self.tabs.addTab(sc, f"T{s.index}")
        dock = QDockWidget("Connessione", self)
        dock.setWidget(self.tabs)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

        self._build_status()
        self._build_menu()
        self._apply_macros()

        for s in self.sessions:
            s.summary_changed.connect(self._refresh_summary)
            s.message.connect(lambda t, ms: self.statusBar().showMessage(t, ms))
            s.focused.connect(lambda s=s: self.set_active(s))
            s.scan_requested.connect(lambda: self.watcher.refresh(force=True))
            s.term_stats.connect(lambda f, b, q, s=s: self._on_term_stats(s, f, b, q))
            self.watcher.ports_changed.connect(s.set_ports)
            self.watcher.ports_changed.connect(s.link.on_ports_changed)
        self.tabs.currentChanged.connect(self._tab_changed)

        self._load_settings()
        self.watcher.start()
        self.set_active(self.sessions[0])
        self._refresh_summary()

    # --- sessioni -----------------------------------------------------------
    def _port_busy(self, port: str, me: Session) -> bool:
        return any(s is not me and s.port_in_use() == port for s in self.sessions)

    def set_active(self, s: Session):
        self.active = s
        for f in self.frames:
            f.set_active(f.session is s)
        if self.tabs.currentIndex() != s.index - 1:
            self.tabs.blockSignals(True)
            self.tabs.setCurrentIndex(s.index - 1)
            self.tabs.blockSignals(False)
        self.setWindowTitle(f"Tornado-Term - {s.title}")

    def _tab_changed(self, i: int):
        if 0 <= i < len(self.sessions):
            self.set_active(self.sessions[i])
            self.sessions[i].term.setFocus()

    def _show_second(self, on: bool):
        self.frames[1].setVisible(on)
        self.tabs.setTabVisible(1, on)
        if not on:
            self.set_active(self.sessions[0])
        else:
            self.split.setSizes([1, 1])

    def _set_orientation(self, horizontal: bool):
        self.split.setOrientation(Qt.Orientation.Horizontal if horizontal else Qt.Orientation.Vertical)
        self.split.setSizes([1, 1])

    # --- menu, macro ----------------------------------------------------------
    def _build_menu(self):
        mt = self.menuBar().addMenu("&Trasferimento")
        for kind, text in (("ascii", "Invia file ASCII..."), ("xsend", "Invia XMODEM/YMODEM..."),
                           ("xrecv", "Ricevi XMODEM/YMODEM...")):
            mt.addAction(text).triggered.connect(lambda _=False, k=kind: self.active.open_transfer(k, self))
        mt.addSeparator()
        mt.addAction("Annulla trasferimento in corso").triggered.connect(lambda: self.active.abort_transfers())

        mm = self.menuBar().addMenu("&Macro")
        mm.addAction("Modifica macro...").triggered.connect(self._edit_macros)
        mm.addAction("Interrompi macro").triggered.connect(lambda: self.active.macro_runner.cancel())
        self.macro_bar = QToolBar("Macro")
        self.macro_bar.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.macro_bar)

        ml = self.menuBar().addMenu("&Log")
        ml.addAction("Avvia log con ora (testo)...").triggered.connect(lambda: self.active.start_log(self, False))
        ml.addAction("Avvia log esadecimale...").triggered.connect(lambda: self.active.start_log(self, True))
        ml.addAction("Ferma log").triggered.connect(lambda: self.active.stop_log())
        ml.addSeparator()
        ml.addAction("Vista esadecimale in tempo reale...").triggered.connect(lambda: self.active.show_hex(self))

        mv = self.menuBar().addMenu("&Vista")
        self.act_second = QAction("Secondo terminale", self, checkable=True)
        self.act_second.toggled.connect(self._show_second)
        mv.addAction(self.act_second)
        grp = QActionGroup(self)
        self.act_side = QAction("Affiancati", self, checkable=True)
        self.act_stack = QAction("Sovrapposti", self, checkable=True)
        for a in (self.act_side, self.act_stack):
            grp.addAction(a)
            mv.addAction(a)
        self.act_side.toggled.connect(self._set_orientation)
        sw = mv.addAction("Passa all'altro terminale")
        sw.setShortcut(QKeySequence("F6"))
        sw.triggered.connect(self._switch_terminal)

    def _switch_terminal(self):
        if not self.act_second.isChecked():
            return
        other = self.sessions[1] if self.active is self.sessions[0] else self.sessions[0]
        self.set_active(other)
        other.term.setFocus()

    def _apply_macros(self):
        self.macro_bar.clear()
        for s in self.sessions:
            s.term.hotkeys.clear()
        for m in self.macros:
            act = QAction(m.name, self.macro_bar)
            act.setToolTip(m.name + (f" ({m.hotkey})" if m.hotkey else ""))
            act.triggered.connect(lambda _=False, t=m.text: (self.active.run_macro(t), self.active.term.setFocus()))
            self.macro_bar.addAction(act)
            if m.hotkey:
                key = QKeySequence(m.hotkey).toString(PORTABLE)
                for s in self.sessions:
                    s.term.hotkeys[key] = lambda t=m.text, s=s: s.run_macro(t)
        self.macro_bar.setVisible(bool(self.macros))

    def _edit_macros(self):
        dlg = MacroEditor(self.macros, lambda t: self.active.run_macro(t), self)
        if dlg.exec():
            self.macros = dlg.result_macros()
            save_macros(self.settings, self.macros)
            self._apply_macros()
        self.active.term.setFocus()

    # --- stato ----------------------------------------------------------------
    def _build_status(self):
        sb = self.statusBar()
        self.lbl_sum = [QLabel("") for _ in self.sessions]
        self.lbl_term = QLabel("")
        for lb in self.lbl_sum:
            sb.addPermanentWidget(lb)
        sb.addPermanentWidget(self.lbl_term)

    def _refresh_summary(self):
        for lb, s, f in zip(self.lbl_sum, self.sessions, self.frames):
            lb.setText(s.summary())
            f.refresh_title()
        self.lbl_sum[1].setVisible(not hasattr(self, "act_second") or self.act_second.isChecked())
        self.setWindowTitle(f"Tornado-Term - {self.active.title}")

    def _on_term_stats(self, s: Session, fps: float, bps: float, backlog: int):
        if s is not self.active:
            return
        txt = f"{fps:.0f} fps | {bps / 1000:.1f} kB/s"
        if backlog > 4096:
            txt += f" | coda {backlog // 1024} kB"
        self.lbl_term.setText(txt)

    # --- persistenza ------------------------------------------------------------
    def _load_settings(self):
        s = self.settings
        two = s.value("second_terminal", False, bool)
        horiz = s.value("split_horizontal", True, bool)
        self.act_side.setChecked(horiz)
        self.act_stack.setChecked(not horiz)
        self.act_second.setChecked(two)
        self._show_second(two)
        self._set_orientation(horiz)
        geo = s.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        else:
            self.resize(1500, 800)

    def closeEvent(self, e):
        s = self.settings
        for ses in self.sessions:
            ses.save_settings()
        s.setValue("second_terminal", self.act_second.isChecked())
        s.setValue("split_horizontal", self.act_side.isChecked())
        s.setValue("geometry", self.saveGeometry())
        self.watcher.stop()
        for ses in self.sessions:
            ses.shutdown()
        super().closeEvent(e)


def _install_crash_log():
    """Scrive errori e crash nativi in tornado-term.log (accanto ad app.py): utile con pythonw/doppio clic."""
    import faulthandler, os, traceback
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tornado-term.log")
    try:
        f = open(path, "a", buffering=1, encoding="utf-8")
        f.write("--- avvio ---\n")
        faulthandler.enable(f)
        sys.stderr = sys.stderr or f      # pythonw: stderr assente
    except OSError:
        return

    def hook(et, ev, tb):
        txt = "".join(traceback.format_exception(et, ev, tb))
        f.write(txt + "\n")
        try:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(None, "Tornado-Term - errore", txt[-1500:])
        except Exception:
            pass
    sys.excepthook = hook


def main():
    _install_crash_log()
    app = QApplication(sys.argv)
    apply_theme(app)
    w = MainWindow()
    w.show()
    w.raise_()
    w.activateWindow()
    w.sessions[0].term.setFocus()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
