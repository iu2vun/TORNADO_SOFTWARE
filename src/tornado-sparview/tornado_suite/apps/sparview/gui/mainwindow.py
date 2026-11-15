"""Finestra principale di SParView."""
from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QActionGroup, QDragEnterEvent, QDropEvent, QKeySequence
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDockWidget, QFileDialog,
                               QFormLayout, QLabel, QMainWindow, QMessageBox, QTabWidget)

from .. import __version__
from ..core.network import Network
from ..io import OPEN_FILTER, load_networks, save_network
from .model import Workspace
from .panels.analysis import AnalysisPanel
from .panels.files import FilesPanel
from .panels.markers import MarkersPanel
from .panels.mathinfo import InfoPanel, MathPanel
from .panels.p1db import P1dBPanel
from .panels.timepanel import TimePanel
from .panels.transform import TransformPanel
from .plot.timeplot import TimeSettings
from .plot.rect import RectPlot
from .plot.timeplot import TimePlot
from .plotarea import PlotArea, export_view_dialog

APP = "SParView"
MAX_RECENT = 10


class SaveDialog(QDialog):
    def __init__(self, parent, nports: int, is_csv: bool):
        super().__init__(parent)
        self.setWindowTitle("Opzioni di salvataggio")
        f = QFormLayout(self)
        self.fmt = QComboBox()
        self.fmt.addItems(["db", "ma", "ri"] if is_csv else ["RI", "MA", "DB"])
        self.unit = QComboBox()
        self.unit.addItems(["Hz", "kHz", "MHz", "GHz"])
        self.unit.setCurrentText("GHz")
        f.addRow("Formato dati:", self.fmt)
        f.addRow("Unità frequenza:", self.unit)
        self.ver = QComboBox()
        if not is_csv:
            self.ver.addItem("Touchstone 1.x", 1)
            self.ver.addItem("Touchstone 2.0", 2)
            if nports > 4:
                self.ver.setCurrentIndex(1)
            f.addRow("Versione:", self.ver)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def options(self) -> dict:
        return {"fmt": self.fmt.currentText(), "freq_unit": self.unit.currentText(),
                "version": self.ver.currentData() or 1}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP)
        self.resize(1500, 900)
        self.setAcceptDrops(True)
        self.settings = QSettings("SParView", "SParView")
        self.ws = Workspace()
        self.ts = TimeSettings()
        self.plots = PlotArea(self.ws, self.ts)
        self.setCentralWidget(self.plots)
        self.session_path: Optional[str] = None

        self.files = FilesPanel(self.ws)
        self.files.openRequested.connect(self.open_files)
        self.files.saveRequested.connect(self.save_network_uid)
        self.files.duplicateRequested.connect(self.duplicate)
        d = QDockWidget("Reti", self)
        d.setObjectName("dock_files")
        d.setWidget(self.files)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, d)
        self.dock_files = d

        self.markers = MarkersPanel(self.ws, self._active_kind)
        self.analysis = AnalysisPanel(self.ws, self._set_bands, self._show_kind)
        self.transform = TransformPanel(self.ws, self.statusBar().showMessage)
        self.timepanel = TimePanel(self.ws, self.ts, self.show_time_plot, self.statusBar().showMessage)
        self.mathp = MathPanel(self.ws, self.statusBar().showMessage)
        self.info = InfoPanel(self.ws)
        self.p1db = P1dBPanel(self.ws, self.statusBar().showMessage)
        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        for w, t in ((self.markers, "Marker"), (self.analysis, "Analisi"), (self.transform, "Trasforma"),
                     (self.timepanel, "Tempo"), (self.mathp, "Math"), (self.p1db, "P1dB"), (self.info, "Info")):
            tabs.addTab(w, t)
        self.tabs = tabs
        d = QDockWidget("Strumenti", self)
        d.setObjectName("dock_tools")
        d.setWidget(tabs)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, d)
        self.dock_tools = d
        self.dock_tools.setMinimumWidth(430)
        self.resizeDocks([self.dock_files, self.dock_tools], [290, 440], Qt.Orientation.Horizontal)

        self.plots.hovered.connect(self.statusBar().showMessage)
        self._build_menus()
        self.statusBar().showMessage("Pronto — trascina qui un file .sNp / .csv / .cti oppure usa File → Apri")
        try:
            g = self.settings.value("geometry")
            if g:
                self.restoreGeometry(g)
            st = self.settings.value("state")
            if st:
                self.restoreState(st)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ menu
    def _act(self, menu, text, fn, shortcut=None, checkable=False, checked=False) -> QAction:
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.setCheckable(checkable)
        if checkable:
            a.setChecked(checked)
            a.toggled.connect(fn)
        else:
            a.triggered.connect(lambda *_: fn())
        menu.addAction(a)
        return a

    def _build_menus(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("&File")
        self._act(m, "Apri…", self.open_files, "Ctrl+O")
        self.recent_menu = m.addMenu("Recenti")
        self._refresh_recent()
        m.addSeparator()
        self._act(m, "Salva rete corrente come…", lambda: self.save_network_uid(self.ws.current_uid), "Ctrl+S")
        self._act(m, "Esporta grafico attivo…", self.export_active, "Ctrl+E")
        m.addSeparator()
        self._act(m, "Apri sessione…", self.load_session)
        self._act(m, "Salva sessione…", self.save_session)
        m.addSeparator()
        self._act(m, "Chiudi tutte le reti", self.close_all)
        self._act(m, "Esci", self.close, "Ctrl+Q")

        m = mb.addMenu("&Visualizza")
        grp = QActionGroup(self)
        for k, name in PlotArea.LAYOUT_NAMES.items():
            a = QAction(name, self, checkable=True)
            a.setChecked(k == self.plots.layout_name)
            a.triggered.connect(lambda _=False, k=k: self.plots.set_layout(k))
            grp.addAction(a)
            m.addAction(a)
        m.addSeparator()
        self._act(m, "Collega asse X", self.plots.set_link_x, checkable=True, checked=True)
        self._act(m, "Autoscala tutti i grafici", self.plots.autoscale_all, "Ctrl+0")
        m.addSeparator()
        for dock in (self.dock_files, self.dock_tools):
            m.addAction(dock.toggleViewAction())

        m = mb.addMenu("&Strumenti")
        for i, name in enumerate(["Marker", "Analisi", "Trasforma", "Dominio tempo", "Math", "P1dB", "Info"]):
            self._act(m, name, lambda i=i: self.tabs.setCurrentIndex(i), f"Ctrl+{i + 1}")
        m.addSeparator()
        self._act(m, "Aggiungi marker", self.markers.add_marker, "Ctrl+M")

        m = mb.addMenu("&?")
        self._act(m, "Informazioni", self.about)

    # ------------------------------------------------------------------ file
    def _recent(self) -> list[str]:
        v = self.settings.value("recent", [])
        return [v] if isinstance(v, str) else list(v or [])

    def _refresh_recent(self) -> None:
        self.recent_menu.clear()
        rec = [p for p in self._recent() if os.path.exists(p)]
        for p in rec:
            a = QAction(p, self)
            a.triggered.connect(lambda _=False, p=p: self.open_paths([p]))
            self.recent_menu.addAction(a)
        self.recent_menu.setEnabled(bool(rec))

    def _push_recent(self, p: str) -> None:
        rec = [x for x in self._recent() if x != p]
        rec.insert(0, p)
        self.settings.setValue("recent", rec[:MAX_RECENT])
        self._refresh_recent()

    def open_files(self) -> None:
        start = self.settings.value("last_dir", "")
        paths, _ = QFileDialog.getOpenFileNames(self, "Apri file S-parametri", start, OPEN_FILTER)
        if paths:
            self.settings.setValue("last_dir", str(Path(paths[0]).parent))
            self.open_paths(paths)

    def open_paths(self, paths: list[str]) -> int:
        n_ok = 0
        errs = []
        for p in paths:
            try:
                nets = load_networks(p)
            except Exception as e:  # noqa: BLE001
                errs.append(f"{os.path.basename(p)}: {e}")
                continue
            for k, net in enumerate(nets):
                self.ws.add_network(net)
                n_ok += 1
            self._push_recent(str(p))
        if errs:
            QMessageBox.warning(self, "Errore di lettura", "\n".join(errs))
        if n_ok:
            self.plots.autoscale_all()
            self.statusBar().showMessage(f"Caricate {n_ok} rete/i")
        return n_ok

    def dragEnterEvent(self, ev: QDragEnterEvent) -> None:
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev: QDropEvent) -> None:
        paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.open_paths(paths)

    def save_network_uid(self, uid: Optional[int]) -> None:
        net = self.ws.network(uid) if uid is not None else None
        if net is None:
            return
        ext = f".s{net.nports}p"
        path, filt = QFileDialog.getSaveFileName(self, "Salva rete", net.name + ext,
                                                 f"Touchstone (*{ext});;CSV (*.csv)")
        if not path:
            return
        if "csv" in filt.lower() and not path.lower().endswith(".csv"):
            path += ".csv"
        elif "csv" not in filt.lower() and not path.lower().endswith(ext):
            path += ext
        dlg = SaveDialog(self, net.nports, path.lower().endswith(".csv"))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            save_network(net, path, **dlg.options())
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Salvataggio", str(e))
            return
        self.statusBar().showMessage(f"Salvato {path}")

    def duplicate(self, uid: int) -> None:
        net = self.ws.network(uid)
        if net is not None:
            c = net.copy(name=net.name + "_copia")
            self.ws.add_network(c)

    def close_all(self) -> None:
        self.ws.clear()
        self.ws.markers.clear()

    def export_active(self) -> None:
        export_view_dialog(self.plots.active.view(), self)

    # --------------------------------------------------------------- collegamenti
    def _active_kind(self) -> str:
        v = self.plots.active.view()
        return v.kind if isinstance(v, RectPlot) and not isinstance(v, TimePlot) else "db"

    def _set_bands(self, bands: list) -> None:
        for p in self.plots.visible_panels():
            v = p.view()
            if isinstance(v, RectPlot) and not isinstance(v, TimePlot):
                v.bands = list(bands)
                v.update()

    def _show_kind(self, kind: str) -> None:
        for p in self.plots.visible_panels():
            if p.view_type() == "rect":
                p.set_type("rect", kind)
                p.view().autoscale()
                return
        self.plots.active.set_type("rect", kind)

    def show_time_plot(self) -> None:
        for p in self.plots.visible_panels():
            if p.view_type() == "time":
                self.plots._set_active(p)
                return
        if self.plots.layout_name == "1":
            self.plots.set_layout("2h")
        p = self.plots.visible_panels()[-1]
        p.set_type("time")
        self.plots._set_active(p)
        self.ts.reset_view = True
        self.ts.changed.emit()

    # ------------------------------------------------------------------ sessione
    @staticmethod
    def _is_file_net(n) -> bool:
        return bool(n.path and os.path.exists(n.path) and Path(n.path).stem == n.name
                    and n.source_format != "traccia derivata")

    @staticmethod
    def _net_to_inline(n) -> dict:
        buf = io.BytesIO()
        np.savez_compressed(buf, freq=n.freq, s=n.s, z0=n.z0)
        return {"name": n.name, "npz": base64.b64encode(buf.getvalue()).decode("ascii"),
                "comments": list(n.comments), "source_format": n.source_format,
                "port_info": [list(x) for x in n.port_info] if n.port_info else None,
                "labels": [[i, j, v] for (i, j), v in (n.custom_labels or {}).items()]}

    @staticmethod
    def _net_from_inline(d: dict):
        z = np.load(io.BytesIO(base64.b64decode(d["npz"])))
        return Network(z["freq"], z["s"], z["z0"], name=d["name"], comments=d.get("comments", []),
                       port_info=[tuple(x) for x in d["port_info"]] if d.get("port_info") else None,
                       custom_labels={(i, j): v for i, j, v in d.get("labels", [])} or None,
                       source_format=d.get("source_format", ""))

    def save_session(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Salva sessione", self.session_path or "sessione.sparview",
                                              "Sessione SParView (*.sparview)")
        if not path:
            return
        ents = list(self.ws.entries)
        idx = {e.net.uid: k for k, e in enumerate(ents)}
        files = []
        for e in ents:
            rec = {"visible": e.visible, "cells": sorted(e.cells),
                   "colors": [[i, j, c] for (i, j), c in e.colors.items()]}
            if self._is_file_net(e.net):
                rec["path"] = e.net.path
            else:
                rec["inline"] = self._net_to_inline(e.net)
            files.append(rec)
        d = {"version": 2, "files": files,
             "markers": [{"id": m.id, "freq": m.freq, "key": [idx[m.key[0]], m.key[1], m.key[2]], "ref": m.ref}
                         for m in self.ws.markers.markers if m.key[0] in idx],
             "plots": self.plots.state(), "time": self.ts.to_dict()}
        try:
            Path(path).write_text(json.dumps(d), encoding="utf-8")
        except OSError as e:
            QMessageBox.critical(self, "Sessione", str(e))
            return
        self.session_path = path
        self.statusBar().showMessage("Sessione salvata")

    def load_session(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Apri sessione", self.session_path or "",
                                              "Sessione SParView (*.sparview)")
        if path:
            self.restore_session(path)

    def restore_session(self, path: str) -> None:
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            QMessageBox.critical(self, "Sessione", str(e))
            return
        self.close_all()
        uids = []
        missing = []
        for f in d.get("files", []):
            try:
                net = self._net_from_inline(f["inline"]) if "inline" in f else load_networks(f["path"])[0]
            except Exception:  # noqa: BLE001
                missing.append(f.get("path") or f.get("inline", {}).get("name", "?"))
                uids.append(None)
                continue
            e = self.ws.add_network(net, cells={tuple(c) for c in f.get("cells", [])} or None, select=False)
            e.visible = f.get("visible", True)
            for i, j, c in f.get("colors", []):
                e.colors[(i, j)] = c
            uids.append(net.uid)
        self.ws.changed.emit()
        ids = {}
        for m in d.get("markers", []):
            k = m["key"]
            if k[0] < len(uids) and uids[k[0]] is not None:
                ids[m.get("id", len(ids) + 1)] = self.ws.markers.add(m["freq"], (uids[k[0]], k[1], k[2])).id
        for m in d.get("markers", []):
            if m.get("ref") in ids and m.get("id") in ids:
                self.ws.markers.set_ref(ids[m["id"]], ids[m["ref"]])
        self.ts.from_dict(d.get("time", {}))
        self.timepanel.reload()
        if "plots" in d:
            self.plots.restore(d["plots"])
        self.session_path = path
        if missing:
            QMessageBox.warning(self, "Sessione", "Reti non caricabili:\n" + "\n".join(missing))

    # --------------------------------------------------------------------- vari
    def about(self) -> None:
        QMessageBox.about(self, APP, f"<b>SParView {__version__}</b><br>Visualizzazione e analisi di parametri S "
                                     "(Touchstone sNp, CSV, CITIfile).<br>Rendering QPainter personalizzato.")

    def closeEvent(self, ev) -> None:
        try:
            self.settings.setValue("geometry", self.saveGeometry())
            self.settings.setValue("state", self.saveState())
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(ev)


def main(argv: Optional[list[str]] = None) -> int:
    import sys
    from .theme import apply_theme
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    apply_theme(app)
    w = MainWindow()
    w.show()
    args = [a for a in argv[1:] if os.path.exists(a)]
    sess = [a for a in args if a.lower().endswith(".sparview")]
    files = [a for a in args if a not in sess]
    if files:
        w.open_paths(files)
    if sess:
        w.restore_session(sess[0])
    return app.exec()
