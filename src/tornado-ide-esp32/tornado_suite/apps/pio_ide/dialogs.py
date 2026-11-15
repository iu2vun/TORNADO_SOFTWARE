"""Gestore schede e gestore librerie dell'IDE ESP32/Arduino (interrogano ``pio``, modificano platformio.ini)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QModelIndex, QObject, QProcess, QProcessEnvironment, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QTableView, QTabWidget, QVBoxLayout, QWidget,
)

from ...core.registry import data_dir
from ...core.theme import P
from . import pio_core as pc


class PioCapture(QObject):
    """Lancia ``pio`` e restituisce stdout/stderr a fine processo: ``done(codice, stdout, stderr)``."""

    done = Signal(int, str, str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.proc: Optional[QProcess] = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning

    def run(self, argv: list[str], cwd: str = "") -> None:
        self.cancel()
        p = QProcess(self)
        if cwd:
            p.setWorkingDirectory(cwd)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUTF8", "1")
        env.insert("NO_COLOR", "1")
        p.setProcessEnvironment(env)
        p.finished.connect(lambda code, st, q=p: self._fin(q, code, st))
        p.errorOccurred.connect(lambda e, q=p: self._err(q, e))
        self.proc = p
        p.start(argv[0], argv[1:])

    def cancel(self) -> None:
        p, self.proc = self.proc, None
        if p is not None:
            try:
                p.finished.disconnect()
                p.errorOccurred.disconnect()
                p.kill()
                p.deleteLater()
            except RuntimeError:
                pass

    def _fin(self, p: QProcess, code: int, st) -> None:
        if p is not self.proc:
            return
        out = bytes(p.readAllStandardOutput()).decode("utf-8", "replace")
        err = bytes(p.readAllStandardError()).decode("utf-8", "replace")
        self.proc = None
        p.deleteLater()
        self.done.emit(code if st == QProcess.ExitStatus.NormalExit else -1, out, err)

    def _err(self, p: QProcess, e) -> None:
        if p is self.proc and e == QProcess.ProcessError.FailedToStart:
            self.proc = None
            p.deleteLater()
            self.done.emit(-1, "", "Impossibile avviare PlatformIO.")


# ===================================================================== gestore schede

class _BoardFilter(QSortFilterProxyModel):
    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.words: list[str] = []
        self.fw = ""
        self.setSortRole(Qt.ItemDataRole.UserRole)

    def set_filters(self, text: str, fw: str) -> None:
        self.words, self.fw = text.lower().split(), fw
        self.invalidateFilter()

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:  # noqa: N802
        m = self.sourceModel()
        b: pc.BoardInfo = m.item(row, 0).data(Qt.ItemDataRole.UserRole + 1)
        if self.fw and self.fw not in b.frameworks:
            return False
        hay = f"{b.id} {b.name} {b.platform} {b.mcu} {b.vendor} {' '.join(b.frameworks)}".lower()
        return all(w in hay for w in self.words)


class BoardManager(QDialog):
    """Elenco di tutte le schede note a PlatformIO (``pio boards``), con ricerca e filtro per framework."""

    COLS = ["Scheda", "Id", "Piattaforma", "MCU", "MHz", "RAM KB", "Flash KB", "Framework", "Produttore"]
    chosen = Signal(object, str)        # BoardInfo, "set" | "add"

    def __init__(self, parent: QWidget, pio: Optional[list[str]], root: str, env: str, current_board: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Gestore schede")
        self.resize(1100, 640)
        self.pio, self.root, self.env = pio, root, env
        self.cache = data_dir() / "pio_boards.json"
        self.cap = PioCapture(self)
        self.cap.done.connect(self._loaded)

        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.ed = QLineEdit()
        self.ed.setPlaceholderText("Cerca per nome, id, MCU, produttore, piattaforma…")
        self.cb_fw = QComboBox()
        self.cb_fw.addItem("Tutti i framework", "")
        self.lbl_n = QLabel("")
        self.lbl_n.setObjectName("Dim")
        self.btn_reload = QPushButton("Aggiorna elenco")
        top.addWidget(self.ed, 1)
        top.addWidget(self.cb_fw)
        top.addWidget(self.btn_reload)
        v.addLayout(top)

        self.model = QStandardItemModel(0, len(self.COLS), self)
        self.model.setHorizontalHeaderLabels(self.COLS)
        self.proxy = _BoardFilter(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(True)
        for i, w in enumerate((240, 170, 100, 110, 55, 65, 75, 130)):
            self.table.setColumnWidth(i, w)
        self.table.doubleClicked.connect(lambda _i: self._use())
        v.addWidget(self.table, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.lbl_detail = QLabel("")
        self.lbl_detail.setWordWrap(True)
        self.lbl_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.progress)
        v.addWidget(self.lbl_detail)

        bot = QHBoxLayout()
        self.lbl_cur = QLabel(f"Ambiente attivo: {env or '—'}" + (f" · scheda {current_board}" if current_board else ""))
        self.lbl_cur.setObjectName("Dim")
        self.btn_set = QPushButton(f"Usa per l'ambiente «{env}»" if env else "Usa per l'ambiente attivo")
        self.btn_set.setProperty("primary", True)
        self.btn_add = QPushButton("Aggiungi come nuovo ambiente")
        self.btn_close = QPushButton("Chiudi")
        bot.addWidget(self.lbl_cur, 1)
        bot.addWidget(self.btn_set)
        bot.addWidget(self.btn_add)
        bot.addWidget(self.btn_close)
        v.addLayout(bot)
        self.btn_set.setEnabled(False)
        self.btn_add.setEnabled(False)

        self.ed.textChanged.connect(self._filter)
        self.cb_fw.currentIndexChanged.connect(self._filter)
        self.btn_reload.clicked.connect(self.reload)
        self.btn_close.clicked.connect(self.accept)
        self.btn_set.clicked.connect(lambda: self._use("set"))
        self.btn_add.clicked.connect(lambda: self._use("add"))
        self.table.selectionModel().currentRowChanged.connect(self._sel)

        if self._load_cache():
            self.lbl_detail.setText("Elenco dalla copia locale. «Aggiorna elenco» lo rilegge da PlatformIO.")
            self.progress.hide()
        else:
            self.reload()
        self.ed.setFocus()

    # -- dati
    def reload(self) -> None:
        if self.pio is None:
            self.progress.hide()
            self.lbl_detail.setText("PlatformIO non trovato: impostane il percorso da «Altro» nella barra strumenti.")
            return
        self.progress.show()
        self.btn_reload.setEnabled(False)
        self.lbl_detail.setText("Leggo l'elenco schede da PlatformIO (può richiedere qualche secondo)…")
        self.cap.run(self.pio + ["boards", "--json-output"], self.root)

    def _loaded(self, code: int, out: str, err: str) -> None:
        self.progress.hide()
        self.btn_reload.setEnabled(True)
        try:
            boards = pc.parse_boards_json(out)
        except ValueError:
            boards = []
        if code != 0 or not boards:
            tail = (err.strip() or out.strip())[-400:]
            self.lbl_detail.setText(f"Impossibile leggere le schede (codice {code}). {tail}")
            self.lbl_detail.setStyleSheet(f"color:{P.danger};")
            return
        try:
            self.cache.write_text(out[out.find("["):], encoding="utf-8")
        except OSError:
            pass
        self.lbl_detail.setStyleSheet("")
        self.lbl_detail.setText(f"{len(boards)} schede.")
        self._fill(boards)

    def _load_cache(self) -> bool:
        try:
            boards = pc.parse_boards_json(self.cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not boards:
            return False
        self._fill(boards)
        return True

    def _fill(self, boards: list[pc.BoardInfo]) -> None:
        self.table.setSortingEnabled(False)
        self.model.removeRows(0, self.model.rowCount())
        fws: set[str] = set()
        for b in boards:
            fws.update(b.frameworks)
            vals = [(b.name, b.name.lower()), (b.id, b.id.lower()), (b.platform, b.platform), (b.mcu, b.mcu.lower()),
                    (f"{b.fcpu / 1e6:g}" if b.fcpu else "", b.fcpu), (f"{b.ram / 1024:g}" if b.ram else "", b.ram),
                    (f"{b.rom / 1024:g}" if b.rom else "", b.rom), (", ".join(b.frameworks), ", ".join(b.frameworks)),
                    (b.vendor, b.vendor.lower())]
            row = []
            for i, (txt, key) in enumerate(vals):
                it = QStandardItem(txt)
                it.setData(key, Qt.ItemDataRole.UserRole)
                if i == 0:
                    it.setData(b, Qt.ItemDataRole.UserRole + 1)
                if i in (4, 5, 6):
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                row.append(it)
            self.model.appendRow(row)
        keep = self.cb_fw.currentData()
        self.cb_fw.blockSignals(True)
        self.cb_fw.clear()
        self.cb_fw.addItem("Tutti i framework", "")
        for f in sorted(fws):
            self.cb_fw.addItem(f, f)
        want = keep if keep else ("arduino" if "arduino" in fws else "")
        self.cb_fw.setCurrentIndex(max(0, self.cb_fw.findData(want)))
        self.cb_fw.blockSignals(False)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self._filter()

    # -- interazione
    def _filter(self) -> None:
        self.proxy.set_filters(self.ed.text(), self.cb_fw.currentData() or "")
        self.lbl_n.setText(f"{self.proxy.rowCount()} schede")
        self.lbl_cur.setToolTip(self.lbl_n.text())
        self.setWindowTitle(f"Gestore schede — {self.proxy.rowCount()} di {self.model.rowCount()}")

    def board(self) -> Optional[pc.BoardInfo]:
        idx = self.table.currentIndex()
        if not idx.isValid():
            return None
        return self.proxy.mapToSource(idx.siblingAtColumn(0)).data(Qt.ItemDataRole.UserRole + 1)

    def _sel(self, *_a) -> None:
        b = self.board()
        self.btn_set.setEnabled(b is not None and bool(self.env))
        self.btn_add.setEnabled(b is not None)
        if b is not None:
            self.lbl_detail.setStyleSheet("")
            self.lbl_detail.setText(
                f"<b>{b.name}</b> · id <b>{b.id}</b> · piattaforma {b.platform} · {b.mcu} {b.fcpu / 1e6:g} MHz · "
                f"RAM {b.ram / 1024:g} KB · Flash {b.rom / 1024:g} KB · framework: {', '.join(b.frameworks)}"
                + (f" · <a href=\"{b.url}\">scheda tecnica</a>" if b.url else ""))
            self.lbl_detail.setOpenExternalLinks(True)

    def _use(self, how: str = "") -> None:
        b = self.board()
        if b is None:
            return
        how = how or ("set" if self.env else "add")
        self.chosen.emit(b, how)
        self.accept()


# ===================================================================== gestore librerie

class LibraryManager(QDialog):
    """Librerie dell'ambiente (``lib_deps``) e ricerca nel registro PlatformIO (``pio pkg search``)."""

    def __init__(self, parent: QWidget, pio: Optional[list[str]], root: str, env: str,
                 get_ini: Callable[[], str], put_ini: Callable[[str], None]) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Gestore librerie — ambiente {env}")
        self.resize(900, 600)
        self.pio, self.root, self.env = pio, root, env
        self.get_ini, self.put_ini = get_ini, put_ini
        self.section = f"env:{env}"
        self.changed = False
        self._results: list[pc.LibInfo] = []
        self.cap = PioCapture(self)
        self.cap.done.connect(self._found)

        v = QVBoxLayout(self)
        tabs = QTabWidget()
        v.addWidget(tabs, 1)

        # --- nel progetto
        w1 = QWidget()
        l1 = QVBoxLayout(w1)
        self.lst_proj = QListWidget()
        self.lst_proj.itemSelectionChanged.connect(self._proj_sel)
        row = QHBoxLayout()
        self.btn_rm = QPushButton("Rimuovi dall'ambiente")
        self.btn_rm.setEnabled(False)
        row.addWidget(self.btn_rm)
        row.addStretch(1)
        self.lbl_proj = QLabel("")
        self.lbl_proj.setObjectName("Dim")
        l1.addWidget(self.lbl_proj)
        l1.addWidget(self.lst_proj, 1)
        l1.addLayout(row)
        tabs.addTab(w1, "Nel progetto")

        # --- registro
        w2 = QWidget()
        l2 = QVBoxLayout(w2)
        srow = QHBoxLayout()
        self.ed_q = QLineEdit()
        self.ed_q.setPlaceholderText("Nome o parola chiave della libreria (es. ArduinoJson, NeoPixel, BME280)…")
        self.btn_search = QPushButton("Cerca")
        srow.addWidget(self.ed_q, 1)
        srow.addWidget(self.btn_search)
        self.lst_res = QListWidget()
        self.lst_res.itemSelectionChanged.connect(self._res_sel)
        self.lbl_res = QLabel("")
        self.lbl_res.setWordWrap(True)
        self.lbl_res.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setMaximumHeight(140)
        self.raw.hide()
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.hide()
        brow = QHBoxLayout()
        self.btn_add = QPushButton("Aggiungi all'ambiente")
        self.btn_add.setProperty("primary", True)
        self.btn_add.setEnabled(False)
        brow.addStretch(1)
        brow.addWidget(self.btn_add)
        l2.addLayout(srow)
        l2.addWidget(self.progress)
        l2.addWidget(self.lst_res, 1)
        l2.addWidget(self.lbl_res)
        l2.addWidget(self.raw)
        l2.addLayout(brow)
        tabs.addTab(w2, "Cerca nel registro")

        bot = QHBoxLayout()
        self.chk_install = QCheckBox("Scarica subito le librerie (pio pkg install) alla chiusura")
        self.chk_install.setChecked(True)
        self.btn_close = QPushButton("Chiudi")
        bot.addWidget(self.chk_install, 1)
        bot.addWidget(self.btn_close)
        v.addLayout(bot)

        self.btn_close.clicked.connect(self.accept)
        self.btn_rm.clicked.connect(self._remove)
        self.btn_search.clicked.connect(self._search)
        self.ed_q.returnPressed.connect(self._search)
        self.btn_add.clicked.connect(self._add)
        self._refresh_proj()
        if pio is None:
            self.lbl_res.setText("PlatformIO non trovato: impostane il percorso da «Altro» nella barra strumenti.")
            self.btn_search.setEnabled(False)

    # -- progetto
    def _refresh_proj(self) -> None:
        self.lst_proj.clear()
        try:
            items = pc.get_list(self.get_ini(), self.section, "lib_deps")
        except OSError:
            items = []
        for e in items:
            it = QListWidgetItem(e)
            if e.startswith((";", "#")):
                it.setForeground(QColor(P.text_dim))
            self.lst_proj.addItem(it)
        local = sorted(d.name for d in Path(self.root, "lib").glob("*") if d.is_dir()) if os.path.isdir(os.path.join(self.root, "lib")) else []
        for n in local:
            it = QListWidgetItem(f"lib/{n}   (locale)")
            it.setForeground(QColor(P.text_dim))
            it.setData(Qt.ItemDataRole.UserRole, "local")
            self.lst_proj.addItem(it)
        self.lbl_proj.setText(f"{len([i for i in items if not i.startswith((';', '#'))])} dipendenze in lib_deps di [{self.section}]"
                              + (f" · {len(local)} librerie locali in lib/" if local else ""))

    def _proj_sel(self) -> None:
        it = self.lst_proj.currentItem()
        self.btn_rm.setEnabled(it is not None and it.data(Qt.ItemDataRole.UserRole) != "local" and not it.text().startswith((";", "#")))

    def _remove(self) -> None:
        it = self.lst_proj.currentItem()
        if it is None:
            return
        try:
            self.put_ini(pc.remove_lib_dep(self.get_ini(), self.section, it.text()))
        except (OSError, KeyError) as e:
            QMessageBox.critical(self, "Librerie", str(e))
            return
        self.changed = True
        self._refresh_proj()

    # -- registro
    def _search(self) -> None:
        q = self.ed_q.text().strip()
        if not q or self.pio is None:
            return
        self.lst_res.clear()
        self.lbl_res.setText("Cerco nel registro PlatformIO…")
        self.raw.hide()
        self.progress.show()
        self.btn_add.setEnabled(False)
        self.cap.run(self.pio + ["pkg", "search", q], self.root)

    def _found(self, code: int, out: str, err: str) -> None:
        self.progress.hide()
        libs = [x for x in pc.parse_pkg_search(out) if x.kind.lower() == "library"] if code == 0 else []
        self._results = libs
        if libs:
            self.lbl_res.setText(f"{len(libs)} librerie trovate (prima pagina dei risultati).")
            for x in libs:
                it = QListWidgetItem(f"{x.name}    {x.version}    {x.description[:110]}")
                it.setData(Qt.ItemDataRole.UserRole, x)
                self.lst_res.addItem(it)
            self.lst_res.setCurrentRow(0)
            return
        self.lbl_res.setText("Nessun risultato interpretabile." if code == 0 else f"Ricerca non riuscita (codice {code}).")
        self.raw.setPlainText((out + "\n" + err).strip()[-3000:])
        self.raw.show()

    def _res_sel(self) -> None:
        it = self.lst_res.currentItem()
        x: Optional[pc.LibInfo] = it.data(Qt.ItemDataRole.UserRole) if it else None
        self.btn_add.setEnabled(x is not None)
        if x is not None:
            self.lbl_res.setText(f"<b>{x.name}</b> {x.version} · {x.published}<br>{x.description}<br>"
                                 f"Voce che verrà scritta in lib_deps: <b>{x.spec}</b>")

    def _add(self) -> None:
        it = self.lst_res.currentItem()
        x: Optional[pc.LibInfo] = it.data(Qt.ItemDataRole.UserRole) if it else None
        if x is None:
            return
        try:
            text, added = pc.add_lib_dep(self.get_ini(), self.section, x.spec)
            if not added:
                QMessageBox.information(self, "Librerie", f"{x.name} è già in lib_deps di [{self.section}].")
                return
            self.put_ini(text)
        except (OSError, KeyError) as e:
            QMessageBox.critical(self, "Librerie", str(e))
            return
        self.changed = True
        self._refresh_proj()
        self.lbl_res.setText(f"Aggiunta {x.spec} a [{self.section}].")

    def wants_install(self) -> bool:
        return self.changed and self.chk_install.isChecked()
