"""Finestra principale: mosaico di viste (dock), pannelli, acquisizione, registrazione, riproduzione."""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import serial
from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QFileDialog, QInputDialog, QLabel, QMainWindow, QMessageBox, QStyle, QToolBar,
    QToolButton, QWidget,
)

from .. import decoders
from ..channel import busy_hint
from ..config import PortConfig, parse_port_spec
from ..player import FINISHED, PAUSED, PLAYING, STOPPED, Player
from ..session import Session
from ..ssnfile import Recording, SsnError, read_ssn
from ..triggers import ACTION_LABELS, TriggerEngine, TriggerRule
from .intercept_dialog import InterceptDialog
from .dialogs import BridgeDialog, ExportDialog, OutputsDialog, PortDialog, TriggerDialog
from .hub import DataHub
from .panels import ChannelsPanel, ReplayPanel, SendPanel, fmt_bytes, fmt_time
from .portdef import BridgeDef, PortDef
from .theme import apply_theme
from .tiles import TILE_TYPES, BaseTile

APP_NAME = "Serial Sniffer"
DEFAULT_TILES = ("data", "terminal", "timeline", "rate")
DOCK_STATE_VERSION = 2


class TileDock(QDockWidget):
    closed = Signal(object)

    def closeEvent(self, event) -> None:
        self.closed.emit(self)
        super().closeEvent(event)


class MainWindow(QMainWindow):
    player_state = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1500, 900)
        self.setAcceptDrops(True)
        self.settings = QSettings("SerialSniffer", "SerialSniffer")
        self.hub = DataHub(self)
        self.port_defs: list[PortDef] = []
        self.bridges: list[BridgeDef] = []
        self.mode = "idle"  # idle | live | replay
        self.session: Optional[Session] = None
        self.player: Optional[Player] = None
        self.recording: Optional[Recording] = None
        self.rec_file: Optional[Path] = None
        self.last_recording: Optional[Path] = None
        self.replay_cfg: dict = {"gap": None, "outputs": {}}
        self._sinks_ser: list[serial.SerialBase] = []
        self.tile_docks: list[TileDock] = []
        self._tile_counter = 0
        self._mark_counter = 0
        self._rec_error_shown = False
        self.triggers = TriggerEngine()
        self.hub.batch.connect(self._on_trigger_batch)

        self.setDockNestingEnabled(True)
        self.setDockOptions(
            QMainWindow.DockOption.AllowNestedDocks | QMainWindow.DockOption.AllowTabbedDocks
            | QMainWindow.DockOption.AnimatedDocks | QMainWindow.DockOption.GroupedDragging
        )
        self._plugin_errors = decoders.load_plugin_dir()[1]
        self._build_panels()
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_statusbar()
        self._load_settings()

        self.player_state.connect(self._on_player_state)
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._refresh_static()
        self._update_ui()
        if self._plugin_errors:
            self.statusBar().showMessage("Plugin non caricati: " + "; ".join(self._plugin_errors), 15000)

    # ================================================================ costruzione
    def _make_dock(self, title: str, name: str, widget: QWidget, area: Qt.DockWidgetArea) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(name)
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
        return dock

    def _build_panels(self) -> None:
        self.channels_panel = ChannelsPanel()
        self.send_panel = SendPanel()
        self.replay_panel = ReplayPanel()
        self.dock_channels = self._make_dock("Canali", "panel-channels", self.channels_panel,
                                             Qt.DockWidgetArea.LeftDockWidgetArea)
        self.dock_send = self._make_dock("Invio", "panel-send", self.send_panel, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.splitDockWidget(self.dock_channels, self.dock_send, Qt.Orientation.Vertical)
        self.dock_replay = self._make_dock("Riproduzione", "panel-replay", self.replay_panel,
                                           Qt.DockWidgetArea.BottomDockWidgetArea)
        self.dock_replay.hide()

        cp = self.channels_panel
        cp.add_clicked.connect(self.add_port)
        cp.edit_clicked.connect(self.edit_port)
        cp.remove_clicked.connect(self.remove_port)
        cp.bridge_clicked.connect(self.edit_bridges)
        cp.intercept_clicked.connect(self.intercept_port)
        self.send_panel.send_requested.connect(self.send_data)
        rp = self.replay_panel
        rp.open_clicked.connect(self.open_recording)
        rp.close_clicked.connect(self.close_replay)
        rp.play_clicked.connect(self.replay_play)
        rp.pause_clicked.connect(lambda: self.player and self.player.pause())
        rp.stop_clicked.connect(self.replay_stop)
        rp.seek_requested.connect(lambda ns: self.player and self.player.seek(ns))
        rp.speed_changed.connect(lambda s: self.player and self.player.set_speed(s))
        rp.loop_changed.connect(self._set_loop)
        rp.gap_changed.connect(self._set_gap)
        rp.outputs_clicked.connect(self.edit_outputs)

    def _act(self, text: str, slot: Callable, shortcut: Optional[str] = None,
             icon: Optional[QStyle.StandardPixmap] = None, tip: str = "") -> QAction:
        a = QAction(text, self)
        if icon is not None:
            a.setIcon(self.style().standardIcon(icon))
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        a.triggered.connect(slot)
        return a

    def _build_actions(self) -> None:
        S = QStyle.StandardPixmap
        self.act_start = self._act("Avvia", self.start_live, "F5", S.SP_MediaPlay, "Apre le porte e avvia l'acquisizione (F5)")
        self.act_stop = self._act("Ferma", self.stop_live, "Shift+F5", S.SP_MediaStop, "Ferma l'acquisizione (Maiusc+F5)")
        self.act_rec = self._act("Registra", self.toggle_record, "F7", S.SP_DialogSaveButton,
                                 "Avvia/ferma la registrazione su file .ssn (F7)")
        self.act_rec.setCheckable(True)
        self.act_mark = self._act("Marker", self.mark, "F9", S.SP_FileDialogInfoView, "Inserisce un marker nella timeline (F9)")
        self.act_mark_txt = self._act("Marker con testo…", self.mark_text, "Shift+F9")
        self.act_open = self._act("Apri registrazione…", self.open_recording, "Ctrl+O", S.SP_DialogOpenButton)
        self.act_last = self._act("Apri ultima registrazione", self.open_last)
        self.act_export = self._act("Esporta…", self.export_dialog, "Ctrl+E", S.SP_ArrowRight,
                                    "Esporta una registrazione in CSV, TXT, HEX, JSONL, RAW o PCAP")
        self.act_rec_dir = self._act("Cartella delle registrazioni…", self.choose_rec_dir)
        self.act_rec_as = self._act("Registra con nome…", lambda: self.toggle_record(ask=True))
        self.act_triggers = self._act("Trigger…", self.edit_triggers, "Ctrl+T",
                                      tip="Regole: quando compare un pattern, marca / registra / ferma (solo live)")
        self.act_clear = self._act("Cancella i dati a schermo", self.clear_data, "Ctrl+L")
        self.act_quit = self._act("Esci", self.close, "Ctrl+Q")
        self.act_mosaic = self._act("Disponi a mosaico", self.arrange_mosaic, "Ctrl+M")
        self.act_reset = self._act("Ripristina layout predefinito", self.reset_layout)
        self.act_plugin = self._act("Carica decoder da file…", self.load_decoder_plugin,
                                    tip="Carica un file .py con decoder di protocollo aggiuntivi (codice eseguito: solo file fidati)")
        self.act_about = self._act("Informazioni", self.about)
        self.tile_actions = {}
        for kind, cls in TILE_TYPES.items():
            self.tile_actions[kind] = self._act(cls.title, lambda _=False, k=kind: self.add_tile(k, arrange=True))

    def _build_menus(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("&File")
        for a in (self.act_open, self.act_last, None, self.act_export, self.act_rec_dir, None, self.act_quit):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Acquisizione")
        for a in (self.act_start, self.act_stop, None, self.act_rec, self.act_rec_as, None, self.act_mark,
                  self.act_mark_txt, None, self.act_triggers, None, self.act_clear):
            m.addSeparator() if a is None else m.addAction(a)
        self.menu_tiles = mb.addMenu("&Vista")
        sub = self.menu_add = self.menu_tiles.addMenu("Aggiungi vista")
        for a in self.tile_actions.values():
            sub.addAction(a)
        self.menu_tiles.addAction(self.act_mosaic)
        self.menu_tiles.addAction(self.act_reset)
        self.menu_tiles.addAction(self.act_plugin)
        self.menu_tiles.addSeparator()
        for d in (self.dock_channels, self.dock_send, self.dock_replay):
            self.menu_tiles.addAction(d.toggleViewAction())
        mb.addMenu("&?").addAction(self.act_about)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Principale")
        tb.setObjectName("toolbar-main")
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        tb.setMovable(False)
        for a in (self.act_start, self.act_stop, self.act_rec, self.act_mark):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.act_open)
        tb.addAction(self.act_export)
        tb.addSeparator()
        btn = QToolButton()
        btn.setText("Aggiungi vista")
        btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogNewFolder))
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        btn.setMenu(self.menu_add)
        tb.addWidget(btn)
        tb.addAction(self.act_mosaic)
        self.addToolBar(tb)

    def _build_statusbar(self) -> None:
        self.lbl_mode = QLabel("Fermo")
        self.lbl_rec = QLabel("")
        self.lbl_info = QLabel("")
        sb = self.statusBar()
        sb.addPermanentWidget(self.lbl_info)
        sb.addPermanentWidget(self.lbl_rec)
        sb.addPermanentWidget(self.lbl_mode)

    # ============================================================== impostazioni
    def _rec_dir(self) -> Path:
        d = Path(str(self.settings.value("rec_dir", str(Path.home() / "Documents" / "SerialSniffer"))))
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _load_settings(self) -> None:
        s = self.settings
        try:
            self.port_defs = [PortDef.from_dict(d) for d in json.loads(str(s.value("ports", "[]")))]
            self.bridges = [BridgeDef.from_dict(d) for d in json.loads(str(s.value("bridges", "[]")))]
        except (ValueError, KeyError, TypeError):
            self.port_defs, self.bridges = [], []
        try:
            tiles = json.loads(str(s.value("tiles", "[]")))
        except ValueError:
            tiles = []
        try:
            self.triggers.set_rules([TriggerRule.from_dict(d) for d in json.loads(str(s.value("triggers", "[]")))])
        except (ValueError, TypeError):
            self.triggers.set_rules([])
        tiles = [t for t in tiles if isinstance(t, dict) and t.get("kind") in TILE_TYPES]
        if tiles:
            for t in tiles:
                self.add_tile(t["kind"], t.get("state"), name=t.get("name"), arrange=False)
            geom, state = s.value("geometry"), s.value("dock_state")
            if geom:
                self.restoreGeometry(geom)
            ok = bool(state) and self.restoreState(state, DOCK_STATE_VERSION)
            if not ok:
                self.arrange_mosaic()
        else:
            self._default_tiles()
        self.dock_replay.hide()

    def _save_settings(self) -> None:
        s = self.settings
        s.setValue("ports", json.dumps([d.to_dict() for d in self.port_defs]))
        s.setValue("bridges", json.dumps([b.to_dict() for b in self.bridges]))
        s.setValue("triggers", json.dumps([r.to_dict() for r in self.triggers.rules]))
        s.setValue("tiles", json.dumps([
            {"kind": d.property("tile_kind"), "name": d.objectName(), "state": d.widget().get_state()}
            for d in self.tile_docks
        ]))
        s.setValue("geometry", self.saveGeometry())
        s.setValue("dock_state", self.saveState(DOCK_STATE_VERSION))
        s.sync()

    def load_decoder_plugin(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Carica decoder", str(decoders.PLUGIN_DIR), "Python (*.py)")
        if not path:
            return
        if QMessageBox.question(
                self, "Carica decoder",
                f"{path}\n\nIl file è codice Python e verrà eseguito con i tuoi privilegi. Caricarlo?") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            added = decoders.load_plugin(path)
        except Exception as exc:
            QMessageBox.critical(self, "Decoder non caricato", str(exc))
            return
        for d in self.tile_docks:
            if hasattr(d.widget(), "refresh_protocols"):
                d.widget().refresh_protocols()
        self.statusBar().showMessage("Decoder caricati: " + ", ".join(added), 8000)

    def set_theme(self, mode: str) -> None:
        """Il tema e' unico (HP 9845C, applicato dalla suite): nessuna scelta."""
        return

    # ==================================================================== mosaico
    def add_tile(self, kind: str, state: Optional[dict] = None, name: Optional[str] = None,
                 arrange: bool = False) -> TileDock:
        cls = TILE_TYPES[kind]
        if name:
            try:
                self._tile_counter = max(self._tile_counter, int(name.rsplit("-", 1)[-1]))
            except ValueError:
                pass
        else:
            self._tile_counter += 1
            name = f"tile-{kind}-{self._tile_counter}"
        tile: BaseTile = cls(self.hub)
        if state:
            tile.set_state(state)
        number = name.rsplit("-", 1)[-1]
        dock = TileDock(f"{cls.title} {number}", self)
        dock.setObjectName(name)
        dock.setProperty("tile_kind", kind)
        dock.setWidget(tile)
        dock.closed.connect(self._on_tile_closed)
        if hasattr(tile, "goto_requested"):
            tile.goto_requested.connect(self.goto_time)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.tile_docks.append(dock)
        if arrange:
            self.arrange_mosaic()
        return dock

    def _on_tile_closed(self, dock: TileDock) -> None:
        if dock in self.tile_docks:
            self.tile_docks.remove(dock)
        self.removeDockWidget(dock)
        dock.deleteLater()

    def _default_tiles(self) -> None:
        for kind in DEFAULT_TILES:
            self.add_tile(kind)
        self.arrange_mosaic()
        self.resizeDocks([self.dock_channels], [420], Qt.Orientation.Horizontal)

    def arrange_mosaic(self) -> None:
        docks = [d for d in self.tile_docks if not d.isFloating()]
        n = len(docks)
        if n == 0:
            return
        cols = max(1, math.ceil(math.sqrt(n)))
        rows = math.ceil(n / cols)
        for d in docks:
            self.removeDockWidget(d)
        columns: list[list[TileDock]] = []
        for c in range(min(cols, n)):
            d = docks[c]
            if c == 0:
                self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, d)
            else:
                self.splitDockWidget(columns[-1][0], d, Qt.Orientation.Horizontal)
            d.show()
            columns.append([d])
        idx = cols
        for _ in range(1, rows):
            for c in range(cols):
                if idx >= n:
                    break
                d = docks[idx]
                idx += 1
                self.splitDockWidget(columns[c][-1], d, Qt.Orientation.Vertical)
                d.show()
                columns[c].append(d)
        self.resizeDocks([col[0] for col in columns], [1000] * len(columns), Qt.Orientation.Horizontal)
        for col in columns:
            if len(col) > 1:
                self.resizeDocks(col, [1000] * len(col), Qt.Orientation.Vertical)

    def reset_layout(self) -> None:
        for d in list(self.tile_docks):
            self._on_tile_closed(d)
        for d in (self.dock_channels, self.dock_send, self.dock_replay):
            self.removeDockWidget(d)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock_channels)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock_send)
        self.splitDockWidget(self.dock_channels, self.dock_send, Qt.Orientation.Vertical)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock_replay)
        for d in (self.dock_channels, self.dock_send):
            d.show()
        self.dock_replay.setVisible(self.mode == "replay")
        self._default_tiles()

    # ======================================================================= porte
    def add_port(self) -> None:
        dlg = PortDialog(self, {d.name for d in self.port_defs}, None, f"CH{len(self.port_defs) + 1}")
        if dlg.exec():
            self.port_defs.append(dlg.result_def())
            self._refresh_static()

    def edit_port(self, row: int) -> None:
        if not 0 <= row < len(self.port_defs):
            return
        old = self.port_defs[row]
        dlg = PortDialog(self, {d.name for d in self.port_defs} - {old.name}, old)
        if dlg.exec():
            new = dlg.result_def()
            for b in self.bridges:
                if b.a == old.name:
                    b.a = new.name
                if b.b == old.name:
                    b.b = new.name
            self.port_defs[row] = new
            self._refresh_static()

    def remove_port(self, row: int) -> None:
        if 0 <= row < len(self.port_defs):
            name = self.port_defs[row].name
            del self.port_defs[row]
            self.bridges = [b for b in self.bridges if name not in (b.a, b.b)]
            self._refresh_static()

    def intercept_port(self) -> None:
        last = self.port_defs[0].config if self.port_defs else "115200 8N1"
        dlg = InterceptDialog(self, {d.name for d in self.port_defs}, last)
        if not dlg.exec():
            return
        defs, bridge, app_port = dlg.result()
        self.port_defs.extend(defs)
        self.bridges = [b for b in self.bridges if (b.a, b.b) != (bridge.a, bridge.b)] + [bridge]
        self._refresh_static()
        QMessageBox.information(self, "Intercetta porta",
                                f"Configurato: {bridge.text()}\n\nNel programma da osservare apri la porta {app_port}, poi avvia l'acquisizione qui.")

    def edit_bridges(self) -> None:
        if len(self.port_defs) < 2:
            QMessageBox.information(self, "Bridge", "Servono almeno due porte.")
            return
        dlg = BridgeDialog(self, [d.name for d in self.port_defs], self.bridges)
        if dlg.exec():
            self.bridges = dlg.bridges()
            self._refresh_static()

    def _refresh_static(self) -> None:
        self.send_panel.set_channels([d.name for d in self.port_defs])
        self.channels_panel.set_bridges_text(", ".join(b.text() for b in self.bridges))
        self._tick()

    # ================================================================ acquisizione
    def start_live(self) -> None:
        if self.mode == "live":
            return
        if self.mode == "replay":
            self.close_replay()
        if not self.port_defs:
            QMessageBox.information(self, APP_NAME, "Aggiungi almeno una porta nel pannello Canali.")
            return
        sess = Session()
        try:
            for d in self.port_defs:
                sess.add_channel(d.name, d.url, d.port_config(), reconnect=d.reconnect,
                                 monitor_lines=d.monitor_lines, idle_gap_s=d.gap_s)
            names = {d.name for d in self.port_defs}
            for b in self.bridges:
                if b.a in names and b.b in names:
                    sess.bridge(b.a, b.b, b.bidirectional)
        except ValueError as exc:
            QMessageBox.critical(self, "Configurazione non valida", str(exc))
            return
        self.hub.request_clear()
        self.hub.set_channels([
            {"index": c.index, "name": c.name, "url": c.url, "config": str(c.config)} for c in sess.channels
        ])
        self.hub.set_now_fn(sess.clock)
        sess.add_listener(self.hub.push)
        try:
            sess.start()
        except (serial.SerialException, OSError, ValueError) as exc:
            QMessageBox.critical(self, "Impossibile aprire la porta", str(exc) + busy_hint(exc).replace(" — ", "\n\n", 1))
            self.hub.set_now_fn(None)
            return
        self.session = sess
        self.mode = "live"
        self._rec_error_shown = False
        self._update_ui()

    def stop_live(self) -> None:
        sess = self.session
        if sess is None:
            return
        info = sess.stop_recording()
        sess.stop()
        self.session = None
        self.mode = "idle"
        self.hub.set_now_fn(None)
        if info:
            self.last_recording = Path(info["path"])
            msg = f"Registrazione salvata: {info['path']} ({info['records']} record, {fmt_bytes(info['bytes'])})"
            if info["error"]:
                QMessageBox.warning(self, "Errore di registrazione", f"{info['error']}\nIl file potrebbe essere incompleto.")
            self.statusBar().showMessage(msg, 15000)
        self._update_ui()

    def toggle_record(self, checked: bool = False, ask: bool = False) -> None:
        if self.session is not None and self.session.recording:
            self._stop_recording()
            return
        if self.session is None:
            self.start_live()
            if self.session is None:
                self._update_ui()
                return
        default = self._rec_dir() / datetime.now().strftime("cap_%Y%m%d_%H%M%S.ssn")
        path = default
        if ask:
            chosen, _ = QFileDialog.getSaveFileName(self, "Registra come", str(default), "Registrazioni (*.ssn)")
            if not chosen:
                self._update_ui()
                return
            path = Path(chosen)
        try:
            self.session.start_recording(path)
        except (OSError, RuntimeError) as exc:
            QMessageBox.critical(self, "Impossibile registrare", str(exc))
            self._update_ui()
            return
        self.last_recording = path
        self._rec_error_shown = False
        self._update_ui()

    def _stop_recording(self) -> None:
        info = self.session.stop_recording() if self.session else None
        if info:
            self.last_recording = Path(info["path"])
            self.statusBar().showMessage(
                f"Registrazione salvata: {info['path']} ({info['records']} record, {fmt_bytes(info['bytes'])})", 15000)
            if info["error"]:
                QMessageBox.warning(self, "Errore di registrazione", f"{info['error']}\nIl file potrebbe essere incompleto.")
        self._update_ui()

    def mark(self) -> None:
        if self.session is not None:
            self._mark_counter += 1
            self.session.mark(f"MARK {self._mark_counter}")

    def mark_text(self) -> None:
        if self.session is None:
            return
        text, ok = QInputDialog.getText(self, "Marker", "Testo del marker:")
        if ok and text.strip():
            self.session.mark(text.strip())

    def send_data(self, name: str, data: bytes) -> None:
        if self.session is not None:
            try:
                self.session.send(name, data)
            except ValueError as exc:
                self.statusBar().showMessage(str(exc), 5000)

    def clear_data(self) -> None:
        self.hub.request_clear()

    def choose_rec_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Cartella delle registrazioni", str(self._rec_dir()))
        if d:
            self.settings.setValue("rec_dir", d)

    # ================================================================ riproduzione
    def open_last(self) -> None:
        if self.last_recording and self.last_recording.exists():
            self.open_recording(path=str(self.last_recording))
        else:
            QMessageBox.information(self, APP_NAME, "Nessuna registrazione recente in questa sessione.")

    def open_recording(self, checked: bool = False, path: Optional[str] = None) -> None:
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Apri registrazione", str(self._rec_dir()),
                                                  "Registrazioni (*.ssn);;Tutti i file (*)")
        if not path:
            return
        if self.mode == "live":
            if QMessageBox.question(self, APP_NAME, "L'acquisizione in corso verrà fermata. Continuare?") \
                    != QMessageBox.StandardButton.Yes:
                return
            self.stop_live()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            rec = read_ssn(path)
        except (SsnError, OSError) as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Registrazione non valida", f"{path}\n\n{exc}")
            return
        QApplication.restoreOverrideCursor()
        if rec.truncated:
            QMessageBox.warning(self, "File troncato",
                                f"{rec.error}: verranno riprodotti i {len(rec.chunks)} record validi.")
        self._teardown_player()
        self.recording = rec
        self.rec_file = Path(path)
        self.replay_cfg = {"gap": None, "outputs": {}}
        self.replay_panel.chk_gap.setChecked(False)
        self.mode = "replay"
        self._build_player()
        self.dock_replay.show()
        self._update_ui()

    def _teardown_player(self) -> None:
        self.hub.full_source = None
        if self.player is not None:
            self.player.close()
            self.player = None
        for ser in self._sinks_ser:
            try:
                ser.close()
            except Exception:
                pass
        self._sinks_ser = []

    def _open_sinks(self) -> dict[int, Callable[[bytes], None]]:
        sinks: dict[int, Callable[[bytes], None]] = {}
        rec = self.recording
        if rec is None:
            return sinks
        info = rec.channel_info()
        for name, spec in self.replay_cfg["outputs"].items():
            try:
                idx = rec.resolve_channel(name)
                _, url, cfg = parse_port_spec(spec)
                if cfg is None:
                    hc = info.get(idx, {}).get("config")
                    cfg = PortConfig.parse(hc) if hc else PortConfig()
                ser = serial.serial_for_url(url, do_not_open=True, **cfg.serial_kwargs())
                ser.write_timeout = 2.0
                ser.open()
            except (serial.SerialException, ValueError, OSError) as exc:
                QMessageBox.warning(self, "Uscita non disponibile", f"{name}: {exc}")
                continue
            self._sinks_ser.append(ser)
            sinks[idx] = ser.write
        return sinks

    def _build_player(self) -> None:
        rec = self.recording
        if rec is None:
            return
        self._teardown_player()
        self.player = Player(
            rec, sinks=self._open_sinks(), on_chunk=self.hub.push, on_state=self.player_state.emit,
            speed=self.replay_panel.speed(), loop=self.replay_panel.loop(), max_gap_s=self.replay_cfg["gap"],
            rebase_ts=True, on_seek=self._on_seek, on_loop=self.hub.request_clear,
        )
        self.hub.request_clear()
        self.hub.set_channels(list(rec.header.get("channels", [])))
        player = self.player
        self.hub.set_now_fn(lambda: player.position_ns)
        self.hub.full_source = lambda: player.items
        self.replay_panel.set_loaded(True, f"{self.rec_file.name}  ({len(rec.chunks)} record)", self.player.duration_ns)
        self.replay_panel.set_state(STOPPED)

    def _on_seek(self, pos_ns: int) -> None:
        """Chiamata dal player con le consegne sospese: ricostruisce lo schermo fino a pos_ns."""
        self.hub.request_clear()
        if self.player is not None:
            self.hub.push_many(self.player.chunks_before(pos_ns, limit=20_000))

    def goto_time(self, ts_ns: int) -> None:
        """Da un risultato di ricerca: porta la riproduzione subito dopo quell'istante."""
        if self.mode == "replay" and self.player is not None:
            self.player.seek(int(ts_ns) + 1)
            self.statusBar().showMessage(f"Posizione: {fmt_time(int(ts_ns))}", 5000)
        else:
            self.statusBar().showMessage("Il salto a un istante è disponibile in riproduzione", 4000)

    # ------------------------------------------------------------------ trigger
    def edit_triggers(self) -> None:
        names = [d.name for d in self.port_defs]
        dlg = TriggerDialog(self.triggers.rules, names, self)
        if dlg.exec():
            self.triggers.set_rules(dlg.result_rules)
            if self.triggers.errors:
                self.statusBar().showMessage("; ".join(f"{k}: {v}" for k, v in self.triggers.errors.items()), 8000)

    def _on_trigger_batch(self, items: list) -> None:
        if self.mode != "live" or self.session is None or not self.triggers.rules:
            return
        for f in self.triggers.feed(items):
            act, name = f.rule.action, f.rule.name
            self.statusBar().showMessage(f"Trigger «{name}»: {ACTION_LABELS.get(act, act)}", 8000)
            if act == "mark":
                self.session.mark(f"TRIGGER {name}")
            elif act == "start_rec":
                if not self.session.recording:
                    QTimer.singleShot(0, self.toggle_record)
            elif act == "stop_rec":
                if self.session.recording:
                    QTimer.singleShot(0, self._stop_recording)
            elif act == "stop":
                QTimer.singleShot(0, self.stop_live)

    def replay_play(self) -> None:
        p = self.player
        if p is None:
            return
        if p.state == FINISHED or (p.state == STOPPED and p.position_ns == 0):
            self.hub.request_clear()
        p.play()

    def replay_stop(self) -> None:
        if self.player is not None:
            self.player.stop()
            self.hub.request_clear()
            self.replay_panel.set_position(0)

    def _set_loop(self, on: bool) -> None:
        if self.player is not None:
            self.player.loop = on

    def _set_gap(self, gap: Optional[float]) -> None:
        if self.recording is not None and gap != self.replay_cfg["gap"]:
            self.replay_cfg["gap"] = gap
            self._build_player()

    def edit_outputs(self) -> None:
        if self.recording is None:
            return
        names = [self.recording.channel_name(i) for i in sorted(self.recording.channel_info())]
        dlg = OutputsDialog(self, names, self.replay_cfg["outputs"])
        if dlg.exec():
            self.replay_cfg["outputs"] = dlg.mapping()
            self._build_player()

    def close_replay(self) -> None:
        if self.mode != "replay":
            return
        self._teardown_player()
        self.recording = None
        self.rec_file = None
        self.mode = "idle"
        self.hub.set_now_fn(None)
        self.replay_panel.set_loaded(False)
        self.dock_replay.hide()
        self._update_ui()

    def _on_player_state(self, state: str) -> None:
        self.replay_panel.set_state(state)

    def export_dialog(self) -> None:
        src = self.rec_file or self.last_recording
        ExportDialog(self, str(src) if src else "").exec()

    # ============================================================== stato e UI
    def _update_ui(self) -> None:
        live, replay = self.mode == "live", self.mode == "replay"
        self.act_start.setEnabled(not live)
        self.act_stop.setEnabled(live)
        self.act_rec.setEnabled(not replay)
        self.act_rec_as.setEnabled(not replay)
        self.act_rec.setChecked(bool(live and self.session is not None and self.session.recording))
        self.act_mark.setEnabled(live)
        self.act_mark_txt.setEnabled(live)
        self.channels_panel.set_editable(self.mode == "idle")
        self.send_panel.set_live(live)
        if live:
            self.lbl_mode.setText("● ACQUISIZIONE")
        elif replay:
            self.lbl_mode.setText("▶ RIPRODUZIONE")
        else:
            self.lbl_mode.setText("Fermo")
        self.setWindowTitle(APP_NAME + (f" — {self.rec_file.name}" if replay and self.rec_file else ""))
        self._tick()

    def _tick(self) -> None:
        rows: list[tuple] = []
        tot = self.hub.totals
        if self.mode == "live" and self.session is not None:
            for st, d in zip(self.session.stats(), self.port_defs):
                rows.append((st["name"], st["url"], st["config"], d.gap_text(),
                             "aperta" if st["open"] else "scollegata", fmt_bytes(st["rx_bytes"]), fmt_bytes(st["tx_bytes"])))
            self.lbl_mode.setText(f"● ACQUISIZIONE  {fmt_time(self.session.clock())}")
            rec = self.session.recording_error
            if self.session.recording:
                size = 0
                if self.last_recording and self.last_recording.exists():
                    size = self.last_recording.stat().st_size
                self.lbl_rec.setText(f"● REC {self.last_recording.name if self.last_recording else ''}  {fmt_bytes(size)}")
            else:
                self.lbl_rec.setText("")
            if rec and not self._rec_error_shown:
                self._rec_error_shown = True
                QMessageBox.critical(self, "Errore di registrazione", f"{rec}\nLa registrazione si è interrotta.")
        elif self.mode == "replay" and self.recording is not None:
            for i, info in sorted(self.recording.channel_info().items()):
                rows.append((info.get("name", f"CH{i}"), info.get("url", ""), info.get("config", ""), "—", "registrato",
                             fmt_bytes(tot.get((i, 0), 0)), fmt_bytes(tot.get((i, 1), 0))))
            if self.player is not None:
                self.replay_panel.set_position(self.player.position_ns)
            self.lbl_rec.setText("")
        else:
            for i, d in enumerate(self.port_defs):
                rows.append((d.name, d.url, d.config, d.gap_text(), "—",
                             fmt_bytes(tot.get((i, 0), 0)), fmt_bytes(tot.get((i, 1), 0))))
            self.lbl_rec.setText("")
        self.channels_panel.set_rows(rows)
        self.lbl_info.setText(f"{self.hub.chunk_count} messaggi")

    # ====================================================================== varie
    def about(self) -> None:
        QMessageBox.about(
            self, APP_NAME,
            "<b>Serial Sniffer</b><br>Acquisizione, registrazione e riproduzione di flussi seriali.<br><br>"
            "Sniffer passivo: due adattatori USB-seriale con i soli RX collegati alla linea.<br>"
            "Bridge: il PC in mezzo alla linea, con inoltro immediato.<br>"
            "I messaggi sono raggruppati per gap idle; il timestamp è quello del primo byte.")

    def dragEnterEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if urls and urls[0].toLocalFile().lower().endswith(".ssn"):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if urls:
            self.open_recording(path=urls[0].toLocalFile())

    def closeEvent(self, event) -> None:
        if self.session is not None and self.session.recording:
            if QMessageBox.question(self, APP_NAME, "Registrazione in corso: fermare e uscire?") \
                    != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.stop_live()
        self._teardown_player()
        self._save_settings()
        super().closeEvent(event)
