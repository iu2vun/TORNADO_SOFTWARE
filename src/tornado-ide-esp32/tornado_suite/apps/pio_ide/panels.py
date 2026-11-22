"""Pannelli e dialoghi dell'IDE ESP32: ricerca nel progetto, Git, memoria, debug, partizioni, OTA, installazione di PlatformIO."""
from __future__ import annotations

import os
import threading
from typing import Callable, Optional

from PySide6.QtCore import QObject, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTabWidget, QTableWidget,
    QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ...core.theme import P
from ...modules.editor import mono_font
from . import debugsession as ds
from . import gitutil as gu
from . import memstats as ms
from . import partitions as pt
from . import pio_install as pi
from . import projsearch as ps


def _rel(root: str, path: str) -> str:
    try:
        return os.path.relpath(path, root)
    except ValueError:
        return path


# ===================================================================== trova nel file (Ctrl+F)

class FindBar(QWidget):
    """Barra trova/sostituisci per l'editor corrente."""

    def __init__(self, current: Callable[[], Optional[object]]) -> None:
        super().__init__()
        self._cur = current
        self.ed_find = QLineEdit()
        self.ed_find.setPlaceholderText("Trova…")
        self.ed_repl = QLineEdit()
        self.ed_repl.setPlaceholderText("Sostituisci con…")
        self.chk_case = QCheckBox("Aa")
        self.chk_case.setToolTip("Maiuscole/minuscole")
        self.chk_word = QCheckBox("Parola")
        self.chk_re = QCheckBox(".*")
        self.chk_re.setToolTip("Espressione regolare")
        self.lbl = QLabel("")
        self.lbl.setObjectName("Dim")
        b_prev, b_next, b_one, b_all, b_x = QPushButton("▲"), QPushButton("▼"), QPushButton("Sostituisci"), QPushButton("Tutto"), QPushButton("✕")
        for b in (b_prev, b_next, b_x):
            b.setFixedWidth(30)
        h = QHBoxLayout(self)
        h.setContentsMargins(6, 3, 6, 3)
        for w in (self.ed_find, b_prev, b_next, self.chk_case, self.chk_word, self.chk_re, self.ed_repl, b_one, b_all, self.lbl):
            h.addWidget(w)
        h.addStretch(1)
        h.addWidget(b_x)
        self.ed_find.returnPressed.connect(self.next)
        b_next.clicked.connect(self.next)
        b_prev.clicked.connect(self.prev)
        b_one.clicked.connect(self.replace_one)
        b_all.clicked.connect(self.replace_all)
        b_x.clicked.connect(self.hide)
        self.hide()

    def open(self, text: str = "") -> None:
        self.show()
        if text:
            self.ed_find.setText(text)
        self.ed_find.setFocus()
        self.ed_find.selectAll()

    def _opts(self) -> dict:
        return dict(case=self.chk_case.isChecked(), regex=self.chk_re.isChecked(), whole=self.chk_word.isChecked())

    def _go(self, back: bool) -> None:
        ed = self._cur()
        if ed is None or not self.ed_find.text():
            return
        try:
            ok = ed.find_text(self.ed_find.text(), backwards=back, **self._opts())
        except Exception:  # noqa: BLE001  (regex non valida)
            ok = False
        self.lbl.setText("" if ok else "non trovato")

    def next(self) -> None:
        self._go(False)

    def prev(self) -> None:
        self._go(True)

    def replace_one(self) -> None:
        ed = self._cur()
        if ed is None or ed.isReadOnly():
            return
        c = ed.textCursor()
        if c.hasSelection():
            c.insertText(self.ed_repl.text())
        self.next()

    def replace_all(self) -> None:
        ed = self._cur()
        if ed is None or ed.isReadOnly() or not self.ed_find.text():
            return
        o = self._opts()
        try:
            n = ed.replace_all(self.ed_find.text() if not o["whole"] else rf"\b{self.ed_find.text() if o['regex'] else __import__('re').escape(self.ed_find.text())}\b",
                               self.ed_repl.text(), case=o["case"], regex=o["regex"] or o["whole"])
        except Exception as e:  # noqa: BLE001
            self.lbl.setText(str(e)[:40])
            return
        self.lbl.setText(f"{n} sostituzioni")


# ===================================================================== cerca nel progetto

class SearchPanel(QWidget):
    open_hit = Signal(str, int, int)        # file, riga, colonna
    files_replaced = Signal(list)           # file riscritti su disco

    def __init__(self, root: Callable[[], str], overrides: Callable[[], dict]) -> None:
        super().__init__()
        self._root, self._over = root, overrides
        self.ed_q = QLineEdit()
        self.ed_q.setPlaceholderText("Cerca in tutto il progetto…")
        self.ed_r = QLineEdit()
        self.ed_r.setPlaceholderText("Sostituisci con…")
        self.ed_g = QLineEdit()
        self.ed_g.setPlaceholderText("Filtro file (es. *.cpp;*.h)")
        self.chk_case, self.chk_word, self.chk_re = QCheckBox("Aa"), QCheckBox("Parola"), QCheckBox(".*")
        self.b_go, self.b_rep = QPushButton("Cerca"), QPushButton("Sostituisci tutto…")
        self.lbl = QLabel("")
        self.lbl.setObjectName("Dim")
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        r1, r2 = QHBoxLayout(), QHBoxLayout()
        for w in (self.ed_q, self.chk_case, self.chk_word, self.chk_re, self.b_go):
            r1.addWidget(w, 1 if w is self.ed_q else 0)
        for w in (self.ed_r, self.ed_g, self.b_rep, self.lbl):
            r2.addWidget(w, 1 if w in (self.ed_r, self.ed_g) else 0)
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.addLayout(r1)
        v.addLayout(r2)
        v.addWidget(self.tree, 1)
        self.ed_q.returnPressed.connect(self.run)
        self.b_go.clicked.connect(self.run)
        self.b_rep.clicked.connect(self.replace_all)
        self.tree.itemActivated.connect(self._act)
        self.tree.itemClicked.connect(self._act)
        self._hits: list[ps.Hit] = []

    def focus_query(self, text: str = "") -> None:
        if text:
            self.ed_q.setText(text)
        self.ed_q.setFocus()
        self.ed_q.selectAll()

    def _opts(self) -> dict:
        return dict(case=self.chk_case.isChecked(), regex=self.chk_re.isChecked(), whole=self.chk_word.isChecked())

    def run(self) -> None:
        root = self._root()
        self.tree.clear()
        if not root or not self.ed_q.text():
            return
        try:
            self._hits = ps.search(root, self.ed_q.text(), globs=self.ed_g.text(), overrides=self._over(), **self._opts())
        except (ValueError, Exception) as e:  # noqa: BLE001
            self.lbl.setText(f"errore: {e}")
            return
        by: dict[str, list[ps.Hit]] = {}
        for h in self._hits:
            by.setdefault(h.file, []).append(h)
        for f, hs in by.items():
            top = QTreeWidgetItem([f"{_rel(root, f)}   ({len(hs)})"])
            top.setForeground(0, QColor(P.accent))
            self.tree.addTopLevelItem(top)
            for h in hs:
                it = QTreeWidgetItem([f"{h.line}:{h.col}   {h.text}"])
                it.setData(0, Qt.ItemDataRole.UserRole, (h.file, h.line, h.col))
                top.addChild(it)
            top.setExpanded(len(by) <= 12)
        self.lbl.setText(f"{len(self._hits)} risultati in {len(by)} file" + (" (limite raggiunto)" if len(self._hits) >= 2000 else ""))

    def _act(self, it: QTreeWidgetItem, _c: int = 0) -> None:
        d = it.data(0, Qt.ItemDataRole.UserRole)
        if d:
            self.open_hit.emit(*d)

    def replace_all(self) -> None:
        if not self._hits:
            self.run()
        files = sorted({h.file for h in self._hits})
        if not files:
            return
        r = QMessageBox.question(self, "Sostituisci tutto",
                                 f"Sostituire «{self.ed_q.text()}» con «{self.ed_r.text()}» in {len(files)} file?\n\n"
                                 "Le modifiche vengono scritte su disco; i file aperti nell'editor con modifiche non salvate vengono saltati.",
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        if r != QMessageBox.StandardButton.Yes:
            return
        dirty = {os.path.normcase(os.path.abspath(p)) for p in self._over()}      # i file aperti li gestisce l'IDE via overrides: qui solo salvati
        todo = [f for f in files if os.path.normcase(os.path.abspath(f)) not in dirty]
        skipped = len(files) - len(todo)
        try:
            res = ps.replace_in_files(todo, self.ed_q.text(), self.ed_r.text(), **self._opts())
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Sostituisci", str(e))
            return
        self.files_replaced.emit(list(res))
        self.lbl.setText(f"{sum(res.values())} sostituzioni in {len(res)} file" + (f" · {skipped} file aperti saltati" if skipped else ""))
        self.run()


# ===================================================================== Git

class _DiffHL(QSyntaxHighlighter):
    def highlightBlock(self, text: str) -> None:  # noqa: N802
        f = QTextCharFormat()
        if text.startswith("+") and not text.startswith("+++"):
            f.setForeground(QColor(P.ok))
        elif text.startswith("-") and not text.startswith("---"):
            f.setForeground(QColor(P.danger))
        elif text.startswith("@@"):
            f.setForeground(QColor(P.accent))
        else:
            return
        self.setFormat(0, len(text), f)


class GitPanel(QWidget):
    status_map_changed = Signal(dict)     # {percorso assoluto normalizzato: M/A/D/?}
    branch_changed = Signal(str)

    def __init__(self, root: Callable[[], str]) -> None:
        super().__init__()
        self._root = root
        self.git = gu.Git("")
        self.lbl_branch = QLabel("")
        self.lbl_branch.setStyleSheet(f"color:{P.accent};font-weight:600;")
        self.lbl_msg = QLabel("")
        self.lbl_msg.setObjectName("Dim")
        self.b_refresh, self.b_init, self.b_commit, self.b_discard = QPushButton("Aggiorna"), QPushButton("Crea repository"), QPushButton("Commit"), QPushButton("Annulla modifiche")
        self.b_commit.setProperty("primary", True)
        self.files = QTreeWidget()
        self.files.setHeaderLabels(["File", "Stato"])
        self.files.setRootIsDecorated(False)
        self.files.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.diff = QPlainTextEdit()
        self.diff.setReadOnly(True)
        self.diff.setFont(mono_font(10))
        self.diff.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        _DiffHL(self.diff.document())
        self.msg = QPlainTextEdit()
        self.msg.setPlaceholderText("Messaggio di commit")
        self.msg.setMaximumHeight(60)
        self.log = QTreeWidget()
        self.log.setHeaderLabels(["Commit", "Data", "Autore", "Messaggio"])
        self.log.setRootIsDecorated(False)
        top = QHBoxLayout()
        for w in (self.lbl_branch, self.b_refresh, self.b_init, self.lbl_msg):
            top.addWidget(w)
        top.addStretch(1)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(self.files, 1)
        lv.addWidget(self.msg)
        rr = QHBoxLayout()
        rr.addWidget(self.b_commit)
        rr.addWidget(self.b_discard)
        lv.addLayout(rr)
        tabs = QTabWidget()
        tabs.addTab(self.diff, "Differenze")
        tabs.addTab(self.log, "Cronologia")
        sp = QSplitter(Qt.Orientation.Horizontal)
        sp.addWidget(left)
        sp.addWidget(tabs)
        sp.setSizes([420, 640])
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.addLayout(top)
        v.addWidget(sp, 1)
        self.b_refresh.clicked.connect(self.refresh)
        self.b_init.clicked.connect(self._init)
        self.b_commit.clicked.connect(self._commit)
        self.b_discard.clicked.connect(self._discard)
        self.files.currentItemChanged.connect(self._show_diff)
        self.files.itemChanged.connect(self._toggled)
        self._busy = False

    def refresh(self) -> None:
        root = self._root()
        self.git = gu.Git(root)
        self.files.blockSignals(True)
        self.files.clear()
        self.log.clear()
        self.diff.clear()
        ok = bool(root) and self.git.available and self.git.is_repo()
        self.b_init.setVisible(bool(root) and self.git.available and not ok)
        for b in (self.b_commit, self.b_discard):
            b.setEnabled(ok)
        if not self.git.available:
            self.lbl_branch.setText("")
            self.lbl_msg.setText("git non trovato: installa Git per Windows (git-scm.com)")
        elif not ok:
            self.lbl_branch.setText("")
            self.lbl_msg.setText("Il progetto non è un repository git" if root else "")
        if not ok:
            self.files.blockSignals(False)
            self.status_map_changed.emit({})
            self.branch_changed.emit("")
            return
        try:
            st = self.git.status()
            for c in st.changes:
                it = QTreeWidgetItem([c.path, ("in stage · " if c.staged else "") + c.label])
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(0, Qt.CheckState.Checked if c.staged else Qt.CheckState.Unchecked)
                it.setData(0, Qt.ItemDataRole.UserRole, c)
                self.files.addTopLevelItem(it)
            self.lbl_branch.setText(f"⎇ {st.branch}" + (f"  ↑{st.ahead}" if st.ahead else "") + (f" ↓{st.behind}" if st.behind else ""))
            self.lbl_msg.setText(f"{len(st.changes)} modifiche" if st.changes else "nessuna modifica")
            for h, d, a, m in self.git.log(40):
                self.log.addTopLevelItem(QTreeWidgetItem([h, d, a, m]))
            self.branch_changed.emit(st.branch)
            self.status_map_changed.emit(self.git.status_map())
        except Exception as e:  # noqa: BLE001
            self.lbl_msg.setText(f"errore git: {e}")
        self.files.blockSignals(False)

    def _init(self) -> None:
        try:
            self.git.init()
            gi = os.path.join(self._root(), ".gitignore")
            lines = open(gi, encoding="utf-8").read().splitlines() if os.path.isfile(gi) else []
            add = [x for x in (".pio/", ".tornado/") if x not in lines]
            if add:
                with open(gi, "a", encoding="utf-8", newline="\n") as f:
                    f.write(("" if not lines or lines[-1] == "" else "\n") + "\n".join(add) + "\n")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Git", str(e))
        self.refresh()

    def _toggled(self, it: QTreeWidgetItem, _c: int) -> None:
        c: gu.Change = it.data(0, Qt.ItemDataRole.UserRole)
        try:
            (self.git.stage if it.checkState(0) == Qt.CheckState.Checked else self.git.unstage)([c.path])
        except Exception as e:  # noqa: BLE001
            self.lbl_msg.setText(str(e))
        QTimer.singleShot(0, self.refresh)

    def _show_diff(self, it: Optional[QTreeWidgetItem], _prev=None) -> None:
        if it is None:
            return
        c: gu.Change = it.data(0, Qt.ItemDataRole.UserRole)
        try:
            txt = self.git.diff(c.path, staged=c.staged and c.work == " ", untracked=c.untracked)
            if c.staged and c.work != " ":
                txt = self.git.diff(c.path, staged=True) + "\n" + txt
        except Exception as e:  # noqa: BLE001
            txt = str(e)
        self.diff.setPlainText(txt or "(nessuna differenza)")

    def _commit(self) -> None:
        try:
            if not any(self.files.topLevelItem(i).checkState(0) == Qt.CheckState.Checked for i in range(self.files.topLevelItemCount())):
                self.lbl_msg.setText("Spunta i file da includere nel commit")
                return
            h = self.git.commit(self.msg.toPlainText())
            self.msg.clear()
            self.lbl_msg.setText(f"commit {h} creato")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Commit", str(e))
        self.refresh()

    def _discard(self) -> None:
        it = self.files.currentItem()
        if it is None:
            return
        c: gu.Change = it.data(0, Qt.ItemDataRole.UserRole)
        if c.untracked:
            QMessageBox.information(self, "Git", "Il file è nuovo (non tracciato): eliminalo a mano se non serve.")
            return
        if QMessageBox.question(self, "Annulla modifiche", f"Perdere le modifiche non salvate in commit a «{c.path}»?\nL'operazione non si può annullare.",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes:
            try:
                self.git.discard(c.path)
            except Exception as e:  # noqa: BLE001
                QMessageBox.critical(self, "Git", str(e))
            self.refresh()


# ===================================================================== memoria

class _Gauge(QWidget):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.title, self.pct, self.text, self.diff = title, 0.0, "", ""
        self.setFixedHeight(34)

    def set(self, pct: float, text: str, diff: str = "") -> None:
        self.pct, self.text, self.diff = pct, text, diff
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(QColor(P.text))
        p.drawText(0, 0, 60, 16, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.title)
        bar = QRectF(60, 3, w - 70, 12)
        p.setPen(QPen(QColor(P.border), 1))
        p.setBrush(QColor(P.panel))
        p.drawRoundedRect(bar, 3, 3)
        col = P.ok if self.pct < 70 else (P.accent if self.pct < 90 else P.danger)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(col))
        p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * min(self.pct, 100) / 100, bar.height()), 3, 3)
        p.setPen(QColor(P.text_dim))
        p.setFont(QFont(self.font().family(), 8))
        p.drawText(60, 17, w - 70, 15, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"{self.pct:.1f}%  ·  {self.text}" + (f"   {self.diff}" if self.diff else ""))
        p.end()


class _HistChart(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(90)
        self.items: list[ms.Sample] = []

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(P.canvas))
        r = QRectF(34, 6, self.width() - 44, self.height() - 22)
        p.setPen(QPen(QColor(P.border), 1))
        p.drawRect(r)
        p.setFont(QFont(self.font().family(), 7))
        peak = max([x.flash_pct for x in self.items] + [x.ram_pct for x in self.items] + [1.0])
        top = 100.0 if peak > 80 else max(10.0, float(-(-peak * 1.3 // 5) * 5))      # scala adattata ai dati (max 100%)
        for g in (0, top / 2, top):
            y = r.bottom() - g / top * r.height()
            p.setPen(QColor(P.text_dim))
            p.drawText(QRectF(0, y - 7, 30, 14), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{g:g}%")
            p.setPen(QPen(QColor(P.border), 1, Qt.PenStyle.DotLine))
            p.drawLine(int(r.left()), int(y), int(r.right()), int(y))
        n = len(self.items)
        for attr, col, name in (("flash_pct", P.accent, "Flash"), ("ram_pct", "#00FFFF", "RAM")):
            pts = [(r.left() + (i / max(1, n - 1)) * r.width() if n > 1 else r.center().x(), r.bottom() - min(getattr(s, attr), top) / top * r.height()) for i, s in enumerate(self.items)]
            p.setPen(QPen(QColor(col), 1.6))
            for a, b in zip(pts, pts[1:]):
                p.drawLine(int(a[0]), int(a[1]), int(b[0]), int(b[1]))
            for x, y in pts[-60:]:
                p.setBrush(QColor(col))
                p.drawEllipse(int(x) - 2, int(y) - 2, 4, 4)
            p.setPen(QColor(col))
            p.drawText(int(r.left()) + (60 if name == "RAM" else 6), int(r.bottom()) + 13, f"■ {name}")
        p.end()


class MemoryPanel(QWidget):
    sym_ready = Signal(object, object, str)       # sezioni, simboli, errore

    def __init__(self, root: Callable[[], str], env: Callable[[], str], platform: Callable[[], str]) -> None:
        super().__init__()
        self._root, self._env, self._platform = root, env, platform
        self.g_flash, self.g_ram = _Gauge("Flash"), _Gauge("RAM")
        self.chart = _HistChart()
        self.lbl = QLabel("Compila per vedere l'occupazione di memoria")
        self.lbl.setObjectName("Dim")
        self.b_an = QPushButton("Analizza simboli")
        self.b_an.setToolTip("Legge il file .elf con size/nm della toolchain: sezioni e simboli più grandi")
        self.sections = QTableWidget(0, 3)
        self.sections.setHorizontalHeaderLabels(["Sezione", "Dimensione", "Indirizzo"])
        self.syms = QTableWidget(0, 4)
        self.syms.setHorizontalHeaderLabels(["Simbolo", "Dimensione", "Area", "Origine"])
        for t in (self.sections, self.syms):
            t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            t.verticalHeader().hide()
            t.horizontalHeader().setStretchLastSection(True)
        self.origins = QTableWidget(0, 2)
        self.origins.setHorizontalHeaderLabels(["Origine (libreria / file)", "Dimensione"])
        self.origins.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.origins.verticalHeader().hide()
        self.origins.horizontalHeader().setStretchLastSection(True)
        tabs = QTabWidget()
        tabs.addTab(self.syms, "Simboli più grandi")
        tabs.addTab(self.origins, "Per libreria/file")
        tabs.addTab(self.sections, "Sezioni")
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(4, 4, 4, 4)
        lv.addWidget(self.g_flash)
        lv.addWidget(self.g_ram)
        lv.addWidget(self.chart, 1)
        lv.addWidget(self.lbl)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 4, 4, 4)
        rv.addWidget(self.b_an, 0, Qt.AlignmentFlag.AlignLeft)
        rv.addWidget(tabs, 1)
        sp = QSplitter(Qt.Orientation.Horizontal)
        sp.addWidget(left)
        sp.addWidget(right)
        sp.setSizes([420, 640])
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(sp)
        self.b_an.clicked.connect(self.analyze)
        self.sym_ready.connect(self._fill)

    def set_samples(self, history: list[ms.Sample], env: str) -> None:
        items = [s for s in history if s.env == env]
        self.chart.items = items[-80:]
        self.chart.update()
        if not items:
            return
        cur = items[-1]
        prev = items[-2] if len(items) > 1 else None
        dr, df = ms.delta(prev, cur)

        def d(x: int) -> str:
            return "" if prev is None or x == 0 else f"({'+' if x > 0 else '−'}{ms.human(abs(x))} vs build prec.)"

        self.g_flash.set(cur.flash_pct, f"{ms.human(cur.flash_used)} / {ms.human(cur.flash_total)}", d(df))
        self.g_ram.set(cur.ram_pct, f"{ms.human(cur.ram_used)} / {ms.human(cur.ram_total)}", d(dr))
        self.lbl.setText(f"{len(items)} build registrate per «{env}»")

    def analyze(self) -> None:
        root, env = self._root(), self._env()
        if not root or not env:
            self.lbl.setText("Scegli un ambiente (non «Tutti»)")
            return
        elf = ms.find_elf(root, env)
        if not elf:
            self.lbl.setText("Nessun .elf: compila prima l'ambiente")
            return
        size_exe, nm_exe = ms.find_binutils("size", self._platform()), ms.find_binutils("nm", self._platform())
        if not size_exe or not nm_exe:
            self.lbl.setText("size/nm della toolchain non trovati: compila almeno una volta con PlatformIO")
            return
        self.b_an.setEnabled(False)
        self.lbl.setText("Analisi in corso…")

        def work() -> None:
            try:
                secs = ms.section_sizes(size_exe, elf)
                syms = ms.top_symbols(nm_exe, elf, 400)
                self.sym_ready.emit(secs, syms, "")
            except Exception as e:  # noqa: BLE001
                self.sym_ready.emit([], [], str(e))

        threading.Thread(target=work, daemon=True).start()

    def _fill(self, secs, syms, err: str) -> None:
        self.b_an.setEnabled(True)
        if err:
            self.lbl.setText("Errore: " + err[:200])
            return
        self.sections.setRowCount(len(secs))
        for i, s in enumerate(secs):
            for j, t in enumerate((s.name, ms.human(s.size), hex(s.addr))):
                self.sections.setItem(i, j, QTableWidgetItem(t))
        top = syms[:120]
        self.syms.setRowCount(len(top))
        for i, s in enumerate(top):
            for j, t in enumerate((s.name, ms.human(s.size), s.area, os.path.basename(s.where.split(":")[0]) if s.where else "")):
                self.syms.setItem(i, j, QTableWidgetItem(t))
        org = ms.group_by_origin(syms)[:60]
        self.origins.setRowCount(len(org))
        for i, (k, n) in enumerate(org):
            self.origins.setItem(i, 0, QTableWidgetItem(k))
            self.origins.setItem(i, 1, QTableWidgetItem(ms.human(n)))
        self.lbl.setText(f"{len(secs)} sezioni, {len(syms)} simboli analizzati")


# ===================================================================== debug

class DebugPanel(QWidget):
    show_location = Signal(str, int)
    state_changed = Signal(bool)

    def __init__(self, argv: Callable[[], Optional[list[str]]], cwd: Callable[[], str]) -> None:
        super().__init__()
        self._argv, self._cwd = argv, cwd
        self.sess = ds.DebugSession(self)
        self.breakpoints: dict[str, set[int]] = {}
        self.fmt: Callable[[str], str] = lambda f: f.replace(chr(92), "/")      # percorso come lo vuole gdb
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setFont(mono_font(10))
        self.out.setMaximumBlockCount(5000)
        self.cmd = QLineEdit()
        self.cmd.setPlaceholderText("Comando gdb (Invio per inviare), es. print contatore")
        self.hist: list[str] = []
        self.hpos = 0
        self.btns: dict[str, QPushButton] = {}
        bar = QHBoxLayout()
        for key, txt, cmd in (("start", "▶ Avvia debug", ""), ("cont", "Continua", "continue"), ("next", "Passo sopra", "next"),
                              ("step", "Passo dentro", "step"), ("fin", "Esci dalla funzione", "finish"), ("bt", "Pila", "bt"),
                              ("loc", "Variabili locali", "info locals"), ("stop", "■ Termina", "")):
            b = QPushButton(txt)
            self.btns[key] = b
            bar.addWidget(b)
            if cmd:
                b.clicked.connect(lambda _=False, c=cmd: self.sess.send(c))
        bar.addStretch(1)
        self.lbl = QLabel("Non attivo")
        self.lbl.setObjectName("Dim")
        bar.addWidget(self.lbl)
        self.bps = QListWidget()
        self.bps.setMaximumWidth(260)
        self.bps.setToolTip("Breakpoint (F9 nell'editor o clic nel margine). Doppio clic: rimuovi")
        row = QHBoxLayout()
        row.addWidget(self.out, 1)
        row.addWidget(self.bps)
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.addLayout(bar)
        v.addLayout(row, 1)
        v.addWidget(self.cmd)
        self.btns["start"].clicked.connect(self.start)
        self.btns["stop"].clicked.connect(self.sess.stop)
        self.cmd.returnPressed.connect(self._send)
        self.sess.output.connect(self._out)
        self.sess.stopped.connect(self._stopped)
        self.sess.ready.connect(self._on_ready)
        self.sess.finished.connect(self._fin)
        self.bps.itemDoubleClicked.connect(self._rm)
        self._sync()

    def _sync(self) -> None:
        run = self.sess.running
        self.btns["start"].setEnabled(not run)
        for k in ("cont", "next", "step", "fin", "bt", "loc", "stop"):
            self.btns[k].setEnabled(run)
        self.cmd.setEnabled(run)
        self.state_changed.emit(run)

    def start(self) -> None:
        argv = self._argv()
        if not argv:
            self.lbl.setText("Non posso avviare: manca pio o l'ambiente")
            return
        self.out.clear()
        self.lbl.setText("Avvio…")
        self.sess.start(argv, self._cwd())
        self._sync()

    def _on_ready(self) -> None:
        self.lbl.setText("Pronto")
        for f, lines in self.breakpoints.items():
            for ln in sorted(lines):
                self.sess.send(f"break {self.fmt(f)}:{ln}")

    def _out(self, line: str) -> None:
        self.out.appendPlainText(line)

    def _stopped(self, f: str, ln: int) -> None:
        self.lbl.setText(f"Fermo a {os.path.basename(f)}:{ln}")
        self.show_location.emit(f, ln)

    def _fin(self, code: int) -> None:
        self.lbl.setText(f"Sessione terminata (codice {code})")
        self.show_location.emit("", 0)
        self._sync()

    def _send(self) -> None:
        t = self.cmd.text().strip()
        if t:
            self.hist.append(t)
            self.sess.send(t)
            self.cmd.clear()

    def toggle(self, file: str, line: int) -> bool:
        """Aggiunge/rimuove un breakpoint; se la sessione è attiva lo applica subito. Ritorna True se ora è attivo."""
        s = self.breakpoints.setdefault(file, set())
        on = line not in s
        if on:
            s.add(line)
        else:
            s.discard(line)
        if self.sess.running:
            self.sess.send((f"break {self.fmt(file)}:{line}") if on else f"clear {self.fmt(file)}:{line}")
        self._refill()
        return on

    def _refill(self) -> None:
        self.bps.clear()
        for f, lines in sorted(self.breakpoints.items()):
            for ln in sorted(lines):
                it = QListWidgetItem(f"{os.path.basename(f)}:{ln}")
                it.setData(Qt.ItemDataRole.UserRole, (f, ln))
                it.setToolTip(f)
                self.bps.addItem(it)

    def _rm(self, it: QListWidgetItem) -> None:
        f, ln = it.data(Qt.ItemDataRole.UserRole)
        self.toggle(f, ln)
        self.state_changed.emit(self.sess.running)

    def lines_for(self, file: str) -> set[int]:
        return set(self.breakpoints.get(file, set()))


# ===================================================================== partizioni

class PartitionDialog(QDialog):
    COLS = ["Nome", "Tipo", "Sottotipo", "Offset", "Dimensione"]

    def __init__(self, parent: QWidget, csv_text: str, flash: int = 0x400000) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tabella delle partizioni")
        self.setMinimumSize(720, 480)
        self.cb_flash = QComboBox()
        for k, v in pt.FLASH_SIZES.items():
            self.cb_flash.addItem(k, v)
        self.cb_flash.setCurrentIndex(max(0, self.cb_flash.findData(flash)))
        self.cb_pre = QComboBox()
        self.cb_pre.addItem("Modelli…", None)
        for k in pt.PRESETS:
            self.cb_pre.addItem(k, k)
        self.tbl = QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels(self.COLS)
        self.tbl.horizontalHeader().setStretchLastSection(True)
        self.lbl = QLabel("")
        self.lbl.setWordWrap(True)
        self.bar = _Gauge("Flash")
        b_add, b_del, b_up, b_dn = QPushButton("Aggiungi"), QPushButton("Rimuovi"), QPushButton("▲"), QPushButton("▼")
        top = QHBoxLayout()
        for w in (QLabel("Flash:"), self.cb_flash, self.cb_pre):
            top.addWidget(w)
        top.addStretch(1)
        for b in (b_add, b_del, b_up, b_dn):
            top.addWidget(b)
        self.bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setText("Salva partitions.csv")
        self.bb.button(QDialogButtonBox.StandardButton.Cancel).setText("Annulla")
        v = QVBoxLayout(self)
        v.addLayout(top)
        v.addWidget(self.tbl, 1)
        v.addWidget(self.bar)
        v.addWidget(self.lbl)
        v.addWidget(self.bb)
        try:
            self._load(pt.parse_csv(csv_text) if csv_text.strip() else [])
        except ValueError as e:
            self._load([])
            self.lbl.setText(f"partitions.csv non leggibile: {e}")
        b_add.clicked.connect(lambda: self._add())
        b_del.clicked.connect(self._del)
        b_up.clicked.connect(lambda: self._move(-1))
        b_dn.clicked.connect(lambda: self._move(1))
        self.cb_pre.activated.connect(self._preset)
        self.cb_flash.currentIndexChanged.connect(self._check)
        self.tbl.itemChanged.connect(self._check)
        self.bb.accepted.connect(self._ok)
        self.bb.rejected.connect(self.reject)
        self._check()

    def flash(self) -> int:
        return int(self.cb_flash.currentData())

    def _load(self, parts: list[pt.Part]) -> None:
        self.tbl.blockSignals(True)
        self.tbl.setRowCount(0)
        for p in parts:
            self._add(p, check=False)
        self.tbl.blockSignals(False)

    def _add(self, p: Optional[pt.Part] = None, check: bool = True) -> None:
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)
        p = p or pt.Part(f"part{r}", "data", "spiffs", None, 0x10000)
        vals = [p.name, p.type, p.subtype, hex(p.offset) if p.offset is not None else "", pt.fmt_size(p.size)]
        for j, t in enumerate(vals):
            self.tbl.setItem(r, j, QTableWidgetItem(t))
        if check:
            self._check()

    def _del(self) -> None:
        r = self.tbl.currentRow()
        if r >= 0:
            self.tbl.removeRow(r)
            self._check()

    def _move(self, d: int) -> None:
        r = self.tbl.currentRow()
        n = r + d
        if r < 0 or not 0 <= n < self.tbl.rowCount():
            return
        rows = self.parts_raw()
        rows[r], rows[n] = rows[n], rows[r]
        self._load_raw(rows)
        self.tbl.setCurrentCell(n, 0)
        self._check()

    def parts_raw(self) -> list[list[str]]:
        return [[(self.tbl.item(r, c).text() if self.tbl.item(r, c) else "") for c in range(5)] for r in range(self.tbl.rowCount())]

    def _load_raw(self, rows: list[list[str]]) -> None:
        self.tbl.blockSignals(True)
        for r, vals in enumerate(rows):
            for c, t in enumerate(vals):
                self.tbl.setItem(r, c, QTableWidgetItem(t))
        self.tbl.blockSignals(False)

    def parts(self) -> list[pt.Part]:
        out: list[pt.Part] = []
        for i, (n, t, s, o, z) in enumerate(self.parts_raw(), 1):
            try:
                out.append(pt.Part(n.strip(), t.strip().lower(), s.strip().lower(), pt.parse_size(o) if o.strip() else None, pt.parse_size(z)))
            except ValueError as e:
                raise ValueError(f"riga {i}: {e}")
        return out

    def _check(self, *_a) -> bool:
        try:
            parts = self.parts()
        except ValueError as e:
            self.lbl.setText("✖ " + str(e))
            self.lbl.setStyleSheet(f"color:{P.danger};")
            return False
        errs = pt.validate(parts, self.flash())
        used = pt.used(parts)
        self.bar.set(100.0 * used / self.flash(), f"{pt.fmt_size(used)} su {pt.fmt_size(self.flash())}")
        self.lbl.setText("\n".join("✖ " + e for e in errs) if errs else "✔ Tabella valida")
        self.lbl.setStyleSheet(f"color:{P.danger if errs else P.ok};")
        return not errs

    def _preset(self, _i: int) -> None:
        k = self.cb_pre.currentData()
        if k:
            text, flash = pt.PRESETS[k]
            self.cb_flash.setCurrentIndex(max(0, self.cb_flash.findData(flash)))
            self._load(pt.parse_csv(text))
            self._check()
        self.cb_pre.setCurrentIndex(0)

    def _ok(self) -> None:
        if self._check():
            self.accept()
        elif QMessageBox.question(self, "Partizioni", "La tabella ha problemi. Salvarla comunque?",
                                  QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            try:
                self.parts()
                self.accept()
            except ValueError:
                pass

    def csv(self) -> str:
        return pt.to_csv(self.parts())


# ===================================================================== OTA

class OtaDialog(QDialog):
    def __init__(self, parent: QWidget, host: str = "", env: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Carica via OTA (rete)")
        self.setMinimumWidth(460)
        self.ed_host = QLineEdit(host)
        self.ed_host.setPlaceholderText("192.168.1.50 oppure esp32.local")
        self.ed_pass = QLineEdit()
        self.ed_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_pass.setPlaceholderText("(vuota se il firmware non la usa)")
        self.sp_port = QSpinBox()
        self.sp_port.setRange(0, 65535)
        self.sp_port.setSpecialValueText("predefinita (3232)")
        note = QLabel(f"Il firmware già sulla scheda deve includere ArduinoOTA (modello «Aggiornamento OTA»). "
                      f"L'IDE crea l'ambiente «{env}-ota» in platformio.ini, che estende «{env}» con upload_protocol = espota.")
        note.setWordWrap(True)
        note.setObjectName("Dim")
        f = QFormLayout(self)
        f.addRow(note)
        f.addRow("Indirizzo", self.ed_host)
        f.addRow("Password OTA", self.ed_pass)
        f.addRow("Porta", self.sp_port)
        self.bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setText("Crea ambiente e carica")
        self.bb.button(QDialogButtonBox.StandardButton.Cancel).setText("Annulla")
        f.addRow(self.bb)
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)


# ===================================================================== installazione di PlatformIO

class _InstallWorker(QThread):
    line = Signal(str)
    done = Signal(bool)

    def __init__(self, py: str) -> None:
        super().__init__()
        self.py = py

    def run(self) -> None:
        try:
            ok = pi.run_install(self.py, self.line.emit)
        except Exception as e:  # noqa: BLE001
            self.line.emit(f"errore: {e}")
            ok = False
        self.done.emit(ok)


class InstallDialog(QDialog):
    installed = Signal(list)       # argv di pio

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Installa PlatformIO")
        self.setMinimumSize(640, 420)
        self.cb = QComboBox()
        self.cands = pi.candidate_pythons()
        for c in self.cands:
            self.cb.addItem(c.label, c.exe)
        info = QLabel(f"PlatformIO verrà installato in un ambiente dedicato:\n{pi.venv_dir()}\n"
                      "Serve connessione a Internet (pochi minuti). Non tocca altre installazioni di Python.")
        info.setWordWrap(True)
        self.warn = QLabel("")
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet(f"color:{P.accent};")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(mono_font(9))
        self.b_go = QPushButton("Installa")
        self.b_go.setProperty("primary", True)
        b_close = QPushButton("Chiudi")
        row = QHBoxLayout()
        row.addWidget(QLabel("Python:"))
        row.addWidget(self.cb, 1)
        row.addWidget(self.b_go)
        v = QVBoxLayout(self)
        v.addWidget(info)
        v.addLayout(row)
        v.addWidget(self.warn)
        v.addWidget(self.log, 1)
        v.addWidget(b_close, 0, Qt.AlignmentFlag.AlignRight)
        b_close.clicked.connect(self.accept)
        self.b_go.clicked.connect(self._go)
        self.cb.currentIndexChanged.connect(self._warn)
        self._worker: Optional[_InstallWorker] = None
        if not self.cands:
            self.warn.setText("Non trovo nessun Python 3 con il modulo venv. Installa Python 3.12 da python.org (spunta «Add to PATH») e riprova.")
            self.b_go.setEnabled(False)
        else:
            self._warn()

    def _warn(self) -> None:
        c = self.cands[self.cb.currentIndex()] if self.cands else None
        if c and c.version > pi.MAX_OK:
            self.warn.setText(f"Python {c.version[0]}.{c.version[1]} è molto recente: alcune dipendenze di PlatformIO potrebbero non essere pronte. "
                              "Se l'installazione fallisce, installa anche Python 3.12.")
        else:
            self.warn.setText("")

    def _go(self) -> None:
        if self._worker is not None:
            return
        self.b_go.setEnabled(False)
        self.log.clear()
        self._worker = _InstallWorker(str(self.cb.currentData()))
        self._worker.line.connect(self.log.appendPlainText)
        self._worker.done.connect(self._done)
        self._worker.start()

    def _done(self, ok: bool) -> None:
        self._worker = None
        self.b_go.setEnabled(True)
        cmd = pi.pio_command() if ok else None
        if cmd:
            self.log.appendPlainText("\n✔ PlatformIO installato: " + " ".join(cmd))
            self.installed.emit(cmd)
        else:
            self.log.appendPlainText("\n✖ Installazione non riuscita: copia il log qui sopra e mandamelo.")

    def reject(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return                      # non chiudere durante l'installazione
        super().reject()
