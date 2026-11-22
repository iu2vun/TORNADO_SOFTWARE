"""USB Tree View in Python — GUI PySide6 (Windows)."""
from __future__ import annotations

import ctypes
import datetime
import html
import json
import sys
import time
from ctypes import wintypes as wt

from PySide6.QtCore import QAbstractNativeEventFilter, QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QGuiApplication, QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDockWidget, QFileDialog, QHBoxLayout, QHeaderView,
    QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QSplitter, QTableWidget,
    QTableWidgetItem, QTabWidget, QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import usbcore
from .usbcore import Node, hexdump, walk
from .usbids import UsbIds

try:
    from .sysmon_view import SystemMonitorWidget
    SYSMON_ERROR = ""
except Exception as _e:  # noqa: BLE001  (psutil/pyqtgraph mancanti)
    SystemMonitorWidget = None
    SYSMON_ERROR = str(_e)

WM_DEVICECHANGE = 0x0219
DBT_DEVNODES_CHANGED = 0x0007
ROLE = Qt.UserRole


class Worker(QThread):
    done = Signal(object, object)

    def __init__(self, fn) -> None:
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self.fn(), None)
        except Exception as e:  # noqa: BLE001
            self.done.emit(None, e)


class DeviceChangeFilter(QAbstractNativeEventFilter):
    def __init__(self, cb) -> None:
        super().__init__()
        self.cb = cb

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) == b"windows_generic_MSG":
            msg = wt.MSG.from_address(int(message))
            if msg.message == WM_DEVICECHANGE and msg.wParam == DBT_DEVNODES_CHANGED:
                self.cb()
        return False, 0


def apply_theme(dark: bool) -> None:
    """Il tema (HP 9845C) lo applica la suite: qui nessuna tavolozza propria."""
    return


def level_color(level: str) -> QColor | None:
    return {"error": QColor("#FF0000"), "warn": QColor("#FFFF00"),
            "empty": QColor("#1010FF")}.get(level)


def node_text(n: Node, ind: int = 0) -> str:
    pad = "  " * ind
    out = [f"{pad}{n.title}"]
    for k, v in n.props:
        out.append(f"{pad}    {k}: {v}")
    for w in n.warnings:
        out.append(f"{pad}    ! {w}")
    if n.descriptors:
        out += [f"{pad}    " + ln for ln in n.descriptors.splitlines()]
    for c in n.children:
        out.append(node_text(c, ind + 1))
    return "\n".join(out)


def node_html(n: Node) -> str:
    col = {"error": "#FF0000", "warn": "#FFFF00", "empty": "#1010FF"}.get(n.level, "inherit")
    rows = "".join(f"<tr><td>{html.escape(k)}</td><td>{html.escape(v)}</td></tr>" for k, v in n.props)
    warns = "".join(f"<div class=w>&#9888; {html.escape(w)}</div>" for w in n.warnings)
    desc = f"<pre>{html.escape(n.descriptors)}</pre>" if n.descriptors else ""
    kids = "".join(node_html(c) for c in n.children)
    return (f"<details open><summary style='color:{col}'>{html.escape(n.title)}</summary>"
            f"<div class=b>{warns}<table>{rows}</table>{desc}{kids}</div></details>")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("USB Tree View")
        self.resize(1300, 800)
        self.settings = QSettings("usbtree", "usbtree")
        self.ids = UsbIds()
        self.roots: list[Node] = []
        self._workers: set[Worker] = set()
        self._busy = False
        self._again = False
        self._prev: dict[str, str] | None = None
        self._last_scan = 0.0

        # --- widgets
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Dispositivo"])
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.tree.currentItemChanged.connect(self._on_select)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Campo", "Valore"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setWordWrap(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        copy = QAction("Copia", self.table)
        copy.setShortcut("Ctrl+C")
        copy.setShortcutContext(Qt.WidgetShortcut)
        copy.triggered.connect(lambda: self._copy_rows(False))
        self.table.addAction(copy)

        mono = QFont("VT323", 15)
        self.desc_view = QPlainTextEdit(readOnly=True)
        self.desc_view.setFont(mono)
        self.desc_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.hex_view = QPlainTextEdit(readOnly=True)
        self.hex_view.setFont(mono)
        self.hex_view.setLineWrapMode(QPlainTextEdit.NoWrap)

        tabs = QTabWidget()
        tabs.addTab(self.table, "Proprieta'")
        tabs.addTab(self.desc_view, "Descrittori")
        tabs.addTab(self.hex_view, "Dump esadecimale")

        split = QSplitter()
        split.addWidget(self.tree)
        split.addWidget(tabs)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        self.main_tabs = QTabWidget()
        self.main_tabs.addTab(split, "USB")
        self.sysmon = None
        if SystemMonitorWidget is not None:
            self.sysmon = SystemMonitorWidget(self.settings)
            self.main_tabs.addTab(self.sysmon, "Sistema")
        else:
            from PySide6.QtWidgets import QLabel
            msg = QLabel("Monitor di sistema non disponibile:\n" + SYSMON_ERROR
                         + "\n\nInstalla: pip install psutil pyqtgraph nvidia-ml-py pythonnet")
            msg.setAlignment(Qt.AlignCenter)
            self.main_tabs.addTab(msg, "Sistema")
        self.setCentralWidget(self.main_tabs)

        self.log_view = QPlainTextEdit(readOnly=True)
        self.log_view.setFont(mono)
        dock = QDockWidget("Log connessioni", self)
        dock.setWidget(self.log_view)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        self.resizeDocks([dock], [160], Qt.Vertical)

        # --- toolbar / menu
        tb = QToolBar("Principale")
        tb.setMovable(False)
        self.addToolBar(tb)
        act_refresh = QAction("Aggiorna", self, shortcut="F5", triggered=self.refresh)
        tb.addAction(act_refresh)
        self.filter = QLineEdit(placeholderText="Filtra: VID:PID, nome, classe, driver, serial...")
        self.filter.setClearButtonEnabled(True)
        self.filter.setMinimumWidth(340)
        self.filter.textChanged.connect(self.apply_filter)
        tb.addSeparator()
        tb.addWidget(self.filter)
        self.chk_empty = QCheckBox("Porte vuote")
        self.chk_empty.setChecked(self.settings.value("empty", True, bool))
        self.chk_empty.toggled.connect(self.apply_filter)
        self.chk_auto = QCheckBox("Auto-aggiorna hot-plug")
        self.chk_auto.setChecked(True)
        tb.addSeparator()
        tb.addWidget(self.chk_empty)
        tb.addWidget(self.chk_auto)

        m = self.menuBar().addMenu("&File")
        m.addAction("Esporta JSON...", lambda: self.export("json"))
        m.addAction("Esporta HTML...", lambda: self.export("html"))
        m.addAction("Esporta testo...", lambda: self.export("txt"))
        m.addSeparator()
        m.addAction("Confronta con snapshot JSON...", self.compare)
        m.addSeparator()
        m.addAction("Esci", self.close)
        v = self.menuBar().addMenu("&Visualizza")
        v.addAction("Espandi tutto", self.tree.expandAll)
        v.addAction("Comprimi tutto", self.tree.collapseAll)
        self.act_dark = QAction("Tema scuro", self, checkable=True)
        self.act_dark.setChecked(self.settings.value("dark", True, bool))
        self.act_dark.toggled.connect(self._toggle_dark)
        t = self.menuBar().addMenu("&Strumenti")
        t.addAction("Aggiorna database usb.ids", self.update_ids)
        t.addAction("Cancella log", self.log_view.clear)
        if self.sysmon is not None:
            self.sysmon.fill_menu(self.menuBar().addMenu("&Sistema"))
            self.sysmon.message.connect(self.log)

        def _tab_changed(idx: int) -> None:  # barra e log USB solo nella scheda USB
            tb.setVisible(idx == 0)
            dock.setVisible(idx == 0)
        self.main_tabs.currentChanged.connect(_tab_changed)

        apply_theme(self.act_dark.isChecked())

        self._debounce = QTimer(self, singleShot=True, interval=700)
        self._debounce.timeout.connect(self.refresh)
        self._filter = None
        if sys.platform == "win32":
            self._filter = DeviceChangeFilter(self._hotplug)
            QApplication.instance().installNativeEventFilter(self._filter)

        QTimer.singleShot(0, self.refresh)
        if not self.ids.available:
            QTimer.singleShot(500, lambda: self.update_ids(silent=True))

    # ------------------------------------------------------------------ scan
    def _hotplug(self) -> None:
        if self.chk_auto.isChecked():
            self._debounce.start()

    def refresh(self) -> None:
        if self._busy:
            self._again = True
            return
        self._busy = True
        self.statusBar().showMessage("Scansione in corso...")
        self._start(Worker(lambda: usbcore.scan(self.ids)), self._scan_done)

    def _start(self, w: Worker, slot) -> None:
        self._workers.add(w)
        w.done.connect(slot)
        w.start()

    def _reap(self) -> None:
        w = self.sender()
        if isinstance(w, Worker):
            w.wait()
            self._workers.discard(w)

    def _scan_done(self, roots, err) -> None:
        self._reap()
        self._busy = False
        if err is not None:
            self.statusBar().showMessage(f"Errore di scansione: {err}")
            self.log(f"ERRORE scansione: {err}")
        else:
            self.roots = roots
            self._diff(roots)
            self.populate(roots)
        if self._again:
            self._again = False
            self.refresh()

    def _diff(self, roots) -> None:
        cur = {n.key + "|" + f"{n.vid:04X}:{n.pid:04X}": n.title
               for n in walk(roots) if n.kind == "device" and n.vid}
        if self._prev is None:
            self.log(f"Scansione iniziale: {len(cur)} dispositivi")
        else:
            for k in cur.keys() - self._prev.keys():
                self.log(f"CONNESSO     {cur[k]}")
            for k in self._prev.keys() - cur.keys():
                self.log(f"DISCONNESSO  {self._prev[k]}")
        self._prev = cur

    def log(self, msg: str) -> None:
        self.log_view.appendPlainText(f"{datetime.datetime.now():%H:%M:%S}  {msg}")

    # ------------------------------------------------------------------ tree
    def populate(self, roots) -> None:
        t0 = time.time()
        sel = None
        cur = self.tree.currentItem()
        if cur is not None:
            sel = cur.data(0, ROLE).key
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        found = [None]

        def add(parent, node: Node) -> None:
            it = QTreeWidgetItem(parent if parent is not None else self.tree, [node.title])
            it.setData(0, ROLE, node)
            col = level_color(node.level)
            if col:
                it.setForeground(0, QBrush(col))
            if node.warnings:
                it.setToolTip(0, "\n".join(node.warnings))
            if node.key == sel:
                found[0] = it
            for c in node.children:
                add(it, c)

        for r in roots:
            add(None, r)
        self.tree.expandAll()
        self.tree.setUpdatesEnabled(True)
        self.apply_filter()
        if found[0] is not None:
            self.tree.setCurrentItem(found[0])
        elif self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        nodes = list(walk(roots))
        ndev = sum(1 for n in nodes if n.kind == "device" and n.vid)
        nhub = sum(1 for n in nodes if n.kind == "hub" or (n.kind == "device" and n.children))
        nwarn = sum(1 for n in nodes if n.warnings)
        self.statusBar().showMessage(
            f"{ndev} dispositivi  |  {nhub} hub  |  {nwarn} avvisi  |  "
            f"{len(roots)} host controller  |  {time.time() - t0 + 0:.2f}s UI")

    def apply_filter(self) -> None:
        text = self.filter.text().strip().lower()
        show_empty = self.chk_empty.isChecked()
        self.settings.setValue("empty", show_empty)

        def rec(it: QTreeWidgetItem) -> bool:
            node: Node = it.data(0, ROLE)
            kids = False
            for i in range(it.childCount()):
                kids = rec(it.child(i)) or kids
            own = (show_empty or node.kind != "empty") and (not text or text in node.search)
            it.setHidden(not (own or kids))
            return own or kids

        for i in range(self.tree.topLevelItemCount()):
            rec(self.tree.topLevelItem(i))

    def _tree_menu(self, pos) -> None:
        it = self.tree.itemAt(pos)
        menu = QMenu(self)
        if it is not None:
            node: Node = it.data(0, ROLE)
            if node.vid:
                menu.addAction("Copia VID:PID", lambda: self._clip(f"{node.vid:04X}:{node.pid:04X}"))
            for k, v in node.props:
                if k == "Instance ID":
                    menu.addAction("Copia Instance ID", lambda v=v: self._clip(v))
            menu.addAction("Copia sottoalbero come testo", lambda: self._clip(node_text(node)))
            menu.addSeparator()
        menu.addAction("Espandi tutto", self.tree.expandAll)
        menu.addAction("Comprimi tutto", self.tree.collapseAll)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # --------------------------------------------------------------- dettagli
    def _on_select(self, cur, _prev) -> None:
        self.table.setRowCount(0)
        if cur is None:
            self.desc_view.clear()
            self.hex_view.clear()
            return
        node: Node = cur.data(0, ROLE)
        rows = [("AVVISO", w) for w in node.warnings] + [tuple(p) for p in node.props]
        self.table.setRowCount(len(rows))
        for i, (k, v) in enumerate(rows):
            a, b = QTableWidgetItem(k), QTableWidgetItem(v)
            if k == "AVVISO":
                col = QBrush(level_color("error" if node.level == "error" else "warn"))
                a.setForeground(col)
                b.setForeground(col)
            self.table.setItem(i, 0, a)
            self.table.setItem(i, 1, b)
        self.table.resizeRowsToContents()
        self.desc_view.setPlainText(node.descriptors)
        self.hex_view.setPlainText("\n\n".join(
            f"[{name}]  {len(data)} byte\n{hexdump(data)}" for name, data in node.raw.items()))

    def _table_menu(self, pos) -> None:
        menu = QMenu(self)
        menu.addAction("Copia valore", lambda: self._copy_rows(True))
        menu.addAction("Copia riga", lambda: self._copy_rows(False))
        menu.addAction("Copia tutto", self._copy_all)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _copy_rows(self, value_only: bool) -> None:
        rows = sorted({i.row() for i in self.table.selectedItems()})
        lines = []
        for r in rows:
            k, v = self.table.item(r, 0).text(), self.table.item(r, 1).text()
            lines.append(v if value_only else f"{k}: {v}")
        self._clip("\n".join(lines))

    def _copy_all(self) -> None:
        self._clip("\n".join(f"{self.table.item(r, 0).text()}: {self.table.item(r, 1).text()}"
                             for r in range(self.table.rowCount())))

    @staticmethod
    def _clip(text: str) -> None:
        QGuiApplication.clipboard().setText(text)

    # ------------------------------------------------------------------ export
    def export(self, fmt: str) -> None:
        if not self.roots:
            return
        flt = {"json": "JSON (*.json)", "html": "HTML (*.html)", "txt": "Testo (*.txt)"}[fmt]
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path, _ = QFileDialog.getSaveFileName(self, "Esporta", f"usbtree_{stamp}.{fmt}", flt)
        if not path:
            return
        if fmt == "json":
            data = json.dumps([r.to_dict() for r in self.roots], indent=2, ensure_ascii=False)
        elif fmt == "txt":
            data = "\n\n".join(node_text(r) for r in self.roots)
        else:
            data = ("<!doctype html><meta charset=utf-8><title>USB Tree</title><style>"
                    "body{font:14px Segoe UI,sans-serif;margin:20px}.b{margin-left:22px}"
                    "table td{padding:1px 14px 1px 0;vertical-align:top}pre{background:#f3f3f3;"
                    "padding:8px;overflow:auto}.w{color:#d68910}summary{cursor:pointer;margin:3px 0}"
                    f"</style><h2>USB Tree &mdash; {datetime.datetime.now():%Y-%m-%d %H:%M:%S}</h2>"
                    + "".join(node_html(r) for r in self.roots))
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
        self.statusBar().showMessage(f"Esportato: {path}", 5000)

    def compare(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Snapshot JSON", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                old = json.load(f)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Confronto", f"File non valido: {e}")
            return

        def flat(items) -> dict:
            out = {}
            for d in items:
                if d["kind"] == "device" and d["vid"]:
                    out[f'{d["key"]}|{d["vid"]:04X}:{d["pid"]:04X}'] = d
                out.update(flat(d["children"]))
            return out

        a = flat(old)
        b = flat([r.to_dict() for r in self.roots])
        ignore = {"Indirizzo bus"}
        lines = []
        for k in sorted(b.keys() - a.keys()):
            lines.append(f"+ AGGIUNTO     {b[k]['title']}")
        for k in sorted(a.keys() - b.keys()):
            lines.append(f"- RIMOSSO      {a[k]['title']}")
        for k in sorted(a.keys() & b.keys()):
            pa = {x: y for x, y in a[k]["props"] if x not in ignore}
            pb = {x: y for x, y in b[k]["props"] if x not in ignore}
            diff = [f"      {x}: {pa.get(x, '-')}  ->  {pb.get(x, '-')}"
                    for x in sorted(pa.keys() | pb.keys()) if pa.get(x) != pb.get(x)]
            if diff:
                lines.append(f"~ MODIFICATO   {b[k]['title']}")
                lines += diff
        dlg = QDialog(self)
        dlg.setWindowTitle("Confronto snapshot")
        dlg.resize(800, 500)
        view = QPlainTextEdit(readOnly=True)
        view.setFont(QFont("VT323", 15))
        view.setPlainText("\n".join(lines) or "Nessuna differenza.")
        lay = QVBoxLayout(dlg)
        lay.addWidget(view)
        dlg.exec()

    # ------------------------------------------------------------------ usb.ids
    def update_ids(self, silent: bool = False) -> None:
        self.statusBar().showMessage("Download usb.ids...")
        w = Worker(self.ids.update)
        w.silent = bool(silent)
        self._start(w, self._ids_done)

    def _ids_done(self, count, err) -> None:
        silent = getattr(self.sender(), "silent", False)
        self._reap()
        if err is not None:
            self.statusBar().showMessage(f"usb.ids non aggiornato: {err}", 8000)
            if not silent:
                QMessageBox.warning(self, "usb.ids", f"Download non riuscito:\n{err}")
            return
        self.statusBar().showMessage(f"usb.ids aggiornato: {count} vendor", 5000)
        self.refresh()

    def _toggle_dark(self, on: bool) -> None:
        self.settings.setValue("dark", on)
        apply_theme(on)
        self.populate(self.roots)
        if self.sysmon is not None:
            self.sysmon.restyle()

    def sysmon_prompt(self) -> None:
        if self.sysmon is not None:
            self.sysmon.maybe_prompt()

    def closeEvent(self, event) -> None:
        if self.sysmon is not None:
            self.sysmon.shutdown()
        super().closeEvent(event)


def main() -> int:
    if sys.platform != "win32":
        print("USB Tree View funziona solo su Windows.")
        return 1
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("usbtree.python")
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    QTimer.singleShot(1500, win.sysmon_prompt)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
