"""IDE ESP32 / Arduino con motore PlatformIO.

Progetti e schede (platformio.ini), compila e carica con ``pio``, errori cliccabili, monitor seriale
(TerminalPanel di Tornado-Term). Riusa ``CodeEditor`` della suite.
"""
from __future__ import annotations

import codecs
import os
import shutil
import threading
import re
import shlex
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QElapsedTimer, QModelIndex, QProcess, QProcessEnvironment, QSortFilterProxyModel, Qt, QTimer, QUrl, Signal, QObject, QDir
from PySide6.QtGui import QAction, QBrush, QColor, QDesktopServices, QKeySequence, QPainter, QTextBlockUserData, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFileSystemModel, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter,
    QTabWidget, QTextEdit, QToolBar, QToolButton, QTreeView, QVBoxLayout, QWidget,
)

from ...core.icons import icon
from ...core.registry import settings
from ...core.theme import P
from ...modules.editor import CodeEditor, mono_font
from ...tornado_diag import collect_diagnostics, get_logger
from ...tornado_diag.dialog import DiagnosticsDialog
from ..tornado_term.panel import TerminalPanel
from ..tornado_term.serial_core import PortInfo, list_ports
from .dialogs import BoardManager, LibraryManager
from .flash_panel import FlashPanel
from .editor_ext import PioEditorBase
from .panels import DebugPanel, FindBar, GitPanel, InstallDialog, MemoryPanel, OtaDialog, PartitionDialog, SearchPanel
from .plotter import PlotterPanel
from . import extras, gitutil, memstats as ms, symbols, templates
from . import icons_ext  # noqa: F401  (registra le icone «chip», «pio_upload», «pio_clean»)
from . import pio_core as pc

log = get_logger("espide")
WARN = "#ffd23f"      # stesso giallo degli avvisi dell'IDE Python
KIND_COLOR = {"error": P.danger, "warning": WARN, "ok": P.ok, "cmd": P.accent, "dim": P.text_dim, "text": P.text}
S = "espide/"         # prefisso chiavi QSettings
KNOWN_VID = {0x303A: "Espressif", 0x10C4: "CP210x", 0x1A86: "CH340", 0x0403: "FTDI", 0x2341: "Arduino", 0x2A03: "Arduino", 0x239A: "Adafruit"}
ALL = "*"             # dato della voce «Tutti gli ambienti» nel combo


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _rel_path(root: str, path: str) -> str:
    try:
        return os.path.relpath(path, root) if root else path
    except ValueError:
        return path


# ===================================================================== runner di pio

class PioRunner(QObject):
    """Esegue ``pio`` con QProcess e ne restituisce l'output riga per riga.

    ``line(testo, sostituisce, transitoria)``: le righe terminate da ``\\r`` (barre di avanzamento di esptool)
    sono «transitorie» e la riga successiva le sostituisce, così il log non si riempie di aggiornamenti.
    """

    line = Signal(str, bool, bool)
    finished = Signal(int)

    _SEP = re.compile(r"\r\n|\n|\r")

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.proc: Optional[QProcess] = None
        self._buf = ""
        self._prog = False
        self._dec = codecs.getincrementaldecoder("utf-8")("replace")

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning

    def start(self, argv: list[str], cwd: str) -> None:
        self._buf, self._prog = "", False
        self._dec.reset()
        p = QProcess(self)
        p.setWorkingDirectory(cwd)
        p.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUTF8", "1")
        env.insert("NO_COLOR", "1")
        p.setProcessEnvironment(env)
        p.readyReadStandardOutput.connect(self._read)
        p.finished.connect(self._done)
        p.errorOccurred.connect(self._error)
        self.proc = p
        p.start(argv[0], argv[1:])

    def stop(self) -> None:
        p = self.proc
        if p is None or not self.running:
            return
        pid = int(p.processId())
        if sys.platform == "win32" and pid:         # su Windows kill() non ferma scons/gcc figli
            QProcess.startDetached("taskkill", ["/PID", str(pid), "/T", "/F"])
        else:
            p.terminate()

            def _kill() -> None:
                try:
                    if p.state() != QProcess.ProcessState.NotRunning:
                        p.kill()
                except RuntimeError:
                    pass
            QTimer.singleShot(2500, _kill)

    # -- interno
    def _read(self) -> None:
        if self.proc is None:
            return
        self._feed(self._dec.decode(bytes(self.proc.readAllStandardOutput())))

    def _feed(self, text: str) -> None:
        buf = self._buf + text
        pos = 0
        for m in self._SEP.finditer(buf):
            if m.group() == "\r" and m.end() == len(buf):   # potrebbe essere un \r\n spezzato tra due letture
                break
            self._emit(buf[pos:m.start()], m.group())
            pos = m.end()
        self._buf = buf[pos:]

    def _emit(self, text: str, sep: str) -> None:
        transient = sep == "\r"
        if text == "" and (transient or self._prog):
            self._prog = self._prog and transient
            return
        self.line.emit(text, self._prog, transient)
        self._prog = transient

    def _done(self, code: int, status: QProcess.ExitStatus) -> None:
        if self.proc is not None and self.proc.bytesAvailable():
            self._read()
        if self._buf:
            self._emit(self._buf, "\n")
            self._buf = ""
        p, self.proc = self.proc, None
        if p is not None:
            p.deleteLater()
        self.finished.emit(code if status == QProcess.ExitStatus.NormalExit else -1)

    def _error(self, err: QProcess.ProcessError) -> None:
        if err == QProcess.ProcessError.FailedToStart:
            self.line.emit("Impossibile avviare PlatformIO (file non eseguibile o permessi mancanti).", False, False)
            p, self.proc = self.proc, None
            if p is not None:
                p.deleteLater()
            self.finished.emit(-1)


# ===================================================================== console di compilazione

class DiagData(QTextBlockUserData):
    def __init__(self, diag: pc.Diag) -> None:
        super().__init__()
        self.diag = diag


class BuildConsole(QPlainTextEdit):
    """Output di pio: righe colorate per gravità, ``file:riga:col`` sottolineato e cliccabile."""

    diag_clicked = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.setFont(mono_font(10))
        self.setMaximumBlockCount(30000)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setStyleSheet(f"QPlainTextEdit{{background:{P.canvas};border:none;}}")
        self.setMouseTracking(True)
        self._empty = True
        self._transient = False

    def clear(self) -> None:  # noqa: D401
        super().clear()
        self._empty, self._transient = True, False

    def add_line(self, text: str, kind: str = "text", diag: Optional[pc.Diag] = None,
                 replace: bool = False, transient: bool = False) -> None:
        c = QTextCursor(self.document())
        c.movePosition(QTextCursor.MoveOperation.End)
        if replace and self._transient and not self._empty:
            c.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            c.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
            c.removeSelectedText()
        elif not self._empty:
            c.insertBlock()
        self._empty = False
        self._transient = transient
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(KIND_COLOR.get(kind, P.text)))
        start = c.position()
        c.insertText(text, fmt)
        if diag is not None and diag.span[1] > diag.span[0]:
            sel = QTextCursor(self.document())
            sel.setPosition(start + diag.span[0])
            sel.setPosition(start + diag.span[1], QTextCursor.MoveMode.KeepAnchor)
            u = QTextCharFormat()
            u.setFontUnderline(True)
            sel.mergeCharFormat(u)
        if diag is not None:
            c.block().setUserData(DiagData(diag))
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _diag_at(self, pos) -> Optional[pc.Diag]:
        ud = self.cursorForPosition(pos).block().userData()
        return ud.diag if isinstance(ud, DiagData) else None

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if self._diag_at(e.position().toPoint())
                                  else Qt.CursorShape.IBeamCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        super().mouseReleaseEvent(e)
        if e.button() == Qt.MouseButton.LeftButton and not self.textCursor().hasSelection():
            d = self._diag_at(e.position().toPoint())
            if d is not None:
                self.diag_clicked.emit(d)


# ===================================================================== editor con segnalini errori

class PioEditor(PioEditorBase):
    """CodeEditor + sottolineatura ondulata e pallino nel margine sulle righe con errori/avvisi."""

    _diags: dict[int, tuple[str, int, str]] = {}      # riga(1-based) -> (gravità, colonna, messaggio)

    def set_diagnostics(self, diags: list[pc.Diag]) -> None:
        d: dict[int, tuple[str, int, str]] = {}
        for x in diags:
            cur = d.get(x.line)
            if cur is None or (x.severity == "error" and cur[0] != "error"):
                d[x.line] = (x.severity, x.col, x.message)
        self._diags = d
        self._on_cursor()
        self.gutter.update()

    def _bracket_selections(self) -> list:
        sels = super()._bracket_selections()
        for line, (sev, col, msg) in self._diags.items():
            blk = self.document().findBlockByNumber(line - 1)
            if not blk.isValid():
                continue
            text = blk.text()
            a = min(max(col - 1, 0), max(len(text) - 1, 0))
            m = re.match(r"\w+|\S", text[a:])
            b = a + (len(m.group(0)) if m else 1)
            s = QTextEdit.ExtraSelection()
            s.format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
            s.format.setUnderlineColor(QColor(P.danger if sev == "error" else WARN))
            s.cursor = QTextCursor(self.document())
            s.cursor.setPosition(blk.position() + a)
            s.cursor.setPosition(blk.position() + min(b, len(text)), QTextCursor.MoveMode.KeepAnchor)
            sels.append(s)
        return sels

    def paint_gutter(self, ev) -> None:
        super().paint_gutter(ev)
        if not self._diags:
            return
        p = QPainter(self.gutter)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        blk = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(blk).translated(self.contentOffset()).top())
        h = self.fontMetrics().height()
        while blk.isValid() and top <= ev.rect().bottom():
            d = self._diags.get(blk.blockNumber() + 1)
            if d and blk.isVisible():
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(P.danger if d[0] == "error" else WARN))
                p.drawEllipse(4, top + h // 2 - 3, 7, 7)
            top += round(self.blockBoundingRect(blk).height())
            blk = blk.next()
        p.end()

    def goto(self, line: int, col: int = 1) -> None:
        blk = self.document().findBlockByNumber(max(0, line - 1))
        c = self.textCursor()
        c.setPosition(blk.position() + min(max(col - 1, 0), max(blk.length() - 1, 0)))
        self.setTextCursor(c)
        self.centerCursor()
        self.setFocus()

    def event(self, e) -> bool:
        from PySide6.QtCore import QEvent
        if e.type() == QEvent.Type.ToolTip and self._diags:
            c = self.cursorForPosition(self.viewport().mapFromGlobal(e.globalPos()))
            d = self._diags.get(c.blockNumber() + 1)
            if d:
                from PySide6.QtWidgets import QToolTip
                QToolTip.showText(e.globalPos(), d[2], self)
                return True
        return super().event(e)


# ===================================================================== albero progetto

GIT_COLOR = {"M": P.accent, "A": P.ok, "?": P.ok, "D": P.danger, "U": P.danger, "R": P.accent}


class _TreeFilter(QSortFilterProxyModel):
    git: dict[str, str] = {}      # percorso normalizzato -> M/A/?/D

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:  # noqa: N802
        m = self.sourceModel()
        return m.fileName(m.index(row, 0, parent)) not in pc.SKIP_DIRS and m.fileName(m.index(row, 0, parent)) != ".tornado"

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: D401
        if role == Qt.ItemDataRole.ForegroundRole and self.git:
            path = self.sourceModel().filePath(self.mapToSource(index))
            st = self.git.get(os.path.normcase(os.path.normpath(path)))
            if st in GIT_COLOR:
                return QBrush(QColor(GIT_COLOR[st]))
        return super().data(index, role)


# ===================================================================== nuovo progetto

class NewProjectDialog(QDialog):
    OTHER = "__altra__"

    def __init__(self, parent: QWidget, parent_dir: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Nuovo progetto")
        self.setMinimumWidth(480)
        f = QFormLayout(self)
        self.ed_name = QLineEdit("nuovo_progetto")
        self.ed_dir = QLineEdit(parent_dir)
        b_dir = QPushButton("Sfoglia…")
        row = QHBoxLayout()
        row.addWidget(self.ed_dir, 1)
        row.addWidget(b_dir)
        self.cb_board = QComboBox()
        for p in pc.PRESETS:
            self.cb_board.addItem(f"{p.nome}  ·  {p.id}", p)
        self.cb_board.addItem("Altra scheda (id PlatformIO)…", self.OTHER)
        self.ed_board = QLineEdit()
        self.ed_board.setPlaceholderText("id scheda, es. esp32-s3-devkitc-1")
        self.ed_plat = QLineEdit()
        self.ed_plat.setPlaceholderText("piattaforma, es. espressif32")
        self.ed_baud = QLineEdit("115200")
        self.cb_tpl = QComboBox()
        for t in templates.TEMPLATES:
            self.cb_tpl.addItem(f"{t.nome}", t.id)
            self.cb_tpl.setItemData(self.cb_tpl.count() - 1, t.descr, Qt.ItemDataRole.ToolTipRole)
        self.chk_pin = QCheckBox(f"Core Arduino-ESP32 2.x ({pc.PIN_ESP32_2X})")
        self.chk_pin.setToolTip("Fissa la piattaforma al core 2.x: il core 3.x cambia API (es. watchdog, LEDC, I2S) e rompe molto codice esistente.")
        self.lbl_err = QLabel("")
        self.lbl_err.setStyleSheet(f"color:{P.danger};")
        f.addRow("Nome", self.ed_name)
        f.addRow("Cartella", row)
        f.addRow("Scheda", self.cb_board)
        f.addRow("Modello", self.cb_tpl)
        self.row_board = (QLabel("Id scheda"), self.ed_board)
        self.row_plat = (QLabel("Piattaforma"), self.ed_plat)
        f.addRow(*self.row_board)
        f.addRow(*self.row_plat)
        f.addRow("Baud monitor", self.ed_baud)
        f.addRow(self.chk_pin)
        f.addRow(self.lbl_err)
        self.bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setText("Crea")
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setProperty("primary", True)
        self.bb.button(QDialogButtonBox.StandardButton.Cancel).setText("Annulla")
        f.addRow(self.bb)
        self.bb.accepted.connect(self._ok)
        self.bb.rejected.connect(self.reject)
        b_dir.clicked.connect(self._browse)
        self.cb_board.currentIndexChanged.connect(self._board_changed)
        self.ed_plat.textChanged.connect(self._board_changed)
        self._board_changed()

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Cartella che conterrà il progetto", self.ed_dir.text())
        if d:
            self.ed_dir.setText(d)

    def _board_changed(self) -> None:
        other = self.cb_board.currentData() == self.OTHER
        for w in (*self.row_board, *self.row_plat):
            w.setVisible(other)
        if not other:
            self.ed_baud.setText(str(self.cb_board.currentData().monitor_speed))
        plat = self.ed_plat.text().strip() if other else self.cb_board.currentData().platform
        self.chk_pin.setVisible(plat == "espressif32")

    def preset(self) -> pc.BoardPreset:
        d = self.cb_board.currentData()
        baud = int(self.ed_baud.text()) if self.ed_baud.text().strip().isdigit() else 115200
        if d == self.OTHER:
            plat, p = self.ed_plat.text().strip(), pc.BoardPreset(self.ed_board.text().strip(), self.ed_board.text().strip(), self.ed_plat.text().strip(), monitor_speed=baud)
        else:
            plat, p = d.platform, pc.BoardPreset(d.id, d.nome, d.platform, d.framework, baud, d.extra)
        if plat == "espressif32" and self.chk_pin.isChecked():
            p = pc.BoardPreset(p.id, p.nome, pc.PIN_ESP32_2X, p.framework, p.monitor_speed, p.extra)
        return p

    def _ok(self) -> None:
        p = self.preset()
        if not pc.valid_project_name(self.ed_name.text()):
            self.lbl_err.setText("Nome non valido: usa lettere, cifre, spazio, - _ .")
        elif not os.path.isdir(self.ed_dir.text()):
            self.lbl_err.setText("La cartella indicata non esiste")
        elif not p.id or not p.platform:
            self.lbl_err.setText("Indica id scheda e piattaforma")
        elif os.path.exists(os.path.join(self.ed_dir.text(), self.ed_name.text().strip(), pc.INI_NAME)):
            self.lbl_err.setText("Esiste già un progetto con questo nome")
        else:
            self.accept()


# ===================================================================== modulo

class EspIde(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.s = settings()
        self.root = ""
        self.info = pc.ProjectInfo("")
        self.runner = PioRunner(self)
        self.runner.line.connect(self._on_line)
        self.runner.finished.connect(self._on_finished)
        self._run_kind = ""
        self._diags: dict[str, pc.Diag] = {}
        self._mem: dict[str, tuple[float, int, int]] = {}
        self._took: Optional[float] = None
        self._reconnect = None
        self._uf2_drive = ""
        self._uf2_env = ""
        self._clock = QElapsedTimer()
        self._tick = QTimer(self)
        self._tick.setInterval(500)
        self._tick.timeout.connect(self._update_elapsed)
        self._pio_info = ""
        self._probe: Optional[QProcess] = None
        self._want_port = str(self.s.value(S + "port", "") or "")
        self._last_run = "nessuna"
        self.index = symbols.ProjectIndex()
        self.hist: Optional[ms.History] = None
        self._bp: Optional[ms.BuildProgress] = None
        self._idx_busy = False
        self._branch = ""
        self._git_timer = QTimer(self)
        self._git_timer.setSingleShot(True)
        self._git_timer.setInterval(800)
        self._git_timer.timeout.connect(lambda: self.git.refresh())

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._build_toolbar())

        self.model = QFileSystemModel()
        self.model.setFilter(QDir.Filter.AllDirs | QDir.Filter.Files | QDir.Filter.NoDotAndDotDot)
        self.proxy = _TreeFilter(self)
        self.proxy.setSourceModel(self.model)
        self.tree = QTreeView()
        self.tree.setModel(self.proxy)
        for c in (1, 2, 3):
            self.tree.hideColumn(c)
        self.tree.setHeaderHidden(True)
        self.tree.doubleClicked.connect(self._tree_open)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)

        self.editors = QTabWidget()
        self.editors.setDocumentMode(True)
        self.editors.setTabsClosable(True)
        self.editors.setMovable(True)
        self.editors.tabCloseRequested.connect(self.chiudi_scheda)
        self.editors.currentChanged.connect(self._on_current)

        self.console = BuildConsole()
        self.console.diag_clicked.connect(self.open_diag)
        self.problems = QListWidget()
        self.problems.itemActivated.connect(lambda it: self.open_diag(it.data(Qt.ItemDataRole.UserRole)))
        self.problems.itemClicked.connect(lambda it: self.open_diag(it.data(Qt.ItemDataRole.UserRole)))
        self.term = TerminalPanel("espide", settings=self.s, show_settings=True)
        self._init_ports()
        self.plot = PlotterPanel()
        self.term.rx.connect(self.plot.feed_bytes)
        self.find = FindBar(self.current)
        self.search = SearchPanel(lambda: self.root, self._unsaved)
        self.search.open_hit.connect(self._open_hit)
        self.search.files_replaced.connect(self._reload_files)
        self.git = GitPanel(lambda: self.root)
        self.git.status_map_changed.connect(self._git_map)
        self.git.branch_changed.connect(self._git_branch)
        self.mem = MemoryPanel(lambda: self.root, self.env_name, self._toolchain_hint)
        self.dbg = DebugPanel(self._dbg_argv, lambda: self.root)
        self.dbg.fmt = self._gdb_path
        self.dbg.show_location.connect(self._dbg_location)
        self.dbg.state_changed.connect(lambda _r: self._update_actions())
        self.bottom = QTabWidget()
        self.bottom.setDocumentMode(True)
        self.bottom.addTab(self.console, "Compilazione")
        self.bottom.addTab(self.problems, "Problemi")
        self.bottom.addTab(self.term, "Monitor seriale")
        self.bottom.addTab(self.plot, "Grafico")
        self.bottom.addTab(self.mem, "Memoria")
        self.bottom.addTab(self.search, "Cerca")
        self.bottom.addTab(self.git, "Git")
        self.bottom.addTab(self.dbg, "Debug")
        self.flash = FlashPanel(self.s, self.selected_port, before=self._flash_before, after=self._flash_after,
                                busy=lambda: self.runner.running)
        self.bottom.addTab(self.flash, "Flash")

        ed_box = QWidget()
        eb = QVBoxLayout(ed_box)
        eb.setContentsMargins(0, 0, 0, 0)
        eb.setSpacing(0)
        eb.addWidget(self.editors, 1)
        eb.addWidget(self.find)
        vs = QSplitter(Qt.Orientation.Vertical)
        vs.addWidget(ed_box)
        vs.addWidget(self.bottom)
        vs.setSizes([560, 300])
        hs = QSplitter(Qt.Orientation.Horizontal)
        hs.addWidget(self.tree)
        hs.addWidget(vs)
        hs.setSizes([240, 1120])
        hs.setStretchFactor(1, 1)
        v.addWidget(hs, 1)
        v.addWidget(self._build_statusbar())

        saved = str(self.s.value(S + "progetto", "") or "")
        if saved and os.path.isdir(saved):
            self._set_root(saved)
            for p in self.s.value(S + "aperti", [], type=list) or []:
                if os.path.isfile(p):
                    self.apri_file(p)
        else:
            self._update_actions()
            self.lbl_state.setText("Apri o crea un progetto PlatformIO")
        self._probe_pio()

    # ------------------------------------------------------------------- UI
    def _build_toolbar(self) -> QToolBar:
        tb = QToolBar()
        self.lbl_proj = QLabel("")
        self.lbl_proj.setStyleSheet(f"color:{P.accent};font-weight:600;padding:0 8px;")
        tb.addWidget(self.lbl_proj)

        def act(txt: str, ic: str, cb, keys: str = "", color: Optional[str] = None) -> QAction:
            a = QAction(icon(ic, color, 20), txt, self)
            if keys:
                a.setShortcut(QKeySequence(keys))
                a.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                a.setToolTip(f"{txt} ({keys})")
            a.triggered.connect(lambda *_: cb())
            self.addAction(a)
            tb.addAction(a)
            return a

        act("Apri progetto…", "folder", self.apri_progetto, "Ctrl+Shift+O")
        act("Nuovo progetto…", "new", self.nuovo_progetto, "Ctrl+Shift+N")
        act("Salva", "save", self.salva_corrente, "Ctrl+S")
        tb.addSeparator()
        tb.addWidget(QLabel(" Ambiente: "))
        self.cmb_env = QComboBox()
        self.cmb_env.setMinimumWidth(190)
        self.cmb_env.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.cmb_env.currentIndexChanged.connect(self._env_changed)
        tb.addWidget(self.cmb_env)
        self.a_boards = act("Gestore schede…", "chip", self.gestore_schede, "Ctrl+Shift+B")
        self.a_libs = act("Gestore librerie…", "layers", self.gestore_librerie, "Ctrl+Shift+L")
        tb.addSeparator()
        tb.addWidget(QLabel(" Porta: "))
        self.cmb_port = QComboBox()
        self.cmb_port.setMinimumWidth(240)
        self.cmb_port.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.cmb_port.setToolTip("Porta seriale per caricamento e monitor. «Automatica»: la sceglie PlatformIO (o upload_port del platformio.ini).")
        tb.addWidget(self.cmb_port)
        tb.addSeparator()
        self.a_build = act("Compila", "check", self.compila, "Ctrl+R", P.ok)
        self.a_upload = act("Carica sulla scheda", "pio_upload", self.carica, "Ctrl+U", P.accent)
        self.a_stop = act("Ferma", "stop", self.ferma, "Ctrl+F2", P.danger)
        self.a_clean = act("Pulisci build", "pio_clean", self.pulisci)
        tb.addSeparator()
        self.a_mon = act("Monitor seriale", "terminale", self.monitor, "Ctrl+Shift+M")
        tb.addSeparator()
        self.a_search = act("Cerca nel progetto", "lens", self.cerca, "Ctrl+Shift+F")
        self.a_debug = act("Debug", "bug", self.avvia_debug, "F5")

        def key(keys: str, cb) -> None:
            a = QAction(self)
            a.setShortcut(QKeySequence(keys))
            a.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            a.triggered.connect(lambda *_: cb())
            self.addAction(a)

        key("Ctrl+F", self.trova)
        key("Ctrl+H", self.trova)
        key("F3", lambda: self.find.next())
        key("Shift+F3", lambda: self.find.prev())
        key("Ctrl+Shift+I", self.formatta)
        tb.addSeparator()
        tools = QToolButton()
        tools.setText("Strumenti")
        tools.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        tm = QMenu(tools)
        self._tool_actions: list[QAction] = []

        def ma(txt: str, cb) -> QAction:
            a = tm.addAction(txt, cb)
            self._tool_actions.append(a)
            return a

        ma("Analisi statica (pio check)", self.analisi_statica)
        ma("Test unitari (pio test)", self.test_unit)
        ma("Aggiorna indice dei simboli del framework (compiledb)", self.compiledb)
        ma("Formatta file (clang-format)   Ctrl+Shift+I", self.formatta)
        tm.addSeparator()
        ma("File system: costruisci immagine", self.fs_build)
        ma("File system: carica sulla scheda", self.fs_upload)
        ma("Carica via OTA (rete)…", self.carica_ota)
        ma("Tabella delle partizioni…", self.partizioni)
        tm.addSeparator()
        ma("Debug: configura platformio.ini…", self.configura_debug)
        tm.addSeparator()
        self.menu_recenti = tm.addMenu("Progetti recenti")
        self.menu_recenti.aboutToShow.connect(self._fill_recenti)
        tm.addSeparator()
        tm.addAction("Installa PlatformIO automaticamente…", self.installa_pio)
        self.act_auto = tm.addAction("Completamento automatico mentre scrivi")
        self.act_auto.setCheckable(True)
        self.act_auto.setChecked(bool(self.s.value(S + "autocomplete", True, type=bool)))
        self.act_auto.toggled.connect(self._set_autocomplete)
        tools.setMenu(tm)
        tb.addWidget(tools)
        more = QToolButton()
        more.setText("Altro")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QMenu(more)
        m.addAction("Installa dipendenze dell'ambiente (pio pkg install)", self.installa_dipendenze)
        m.addAction("Percorso di PlatformIO (pio)…", self.set_pio_path)
        m.addAction("Controlla PlatformIO", self._probe_pio_verbose)
        m.addAction("Diagnostica (copia log e stato)…", self.diagnostica)
        m.addSeparator()
        m.addAction("Mostra cartella progetto", lambda: self._reveal(self.root))
        m.addAction("Ricarica platformio.ini", self._reload_ini)
        more.setMenu(m)
        tb.addWidget(more)
        return tb

    def _build_statusbar(self) -> QWidget:
        st = QWidget()
        h = QHBoxLayout(st)
        h.setContentsMargins(8, 2, 8, 2)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedSize(110, 8)
        self.progress.hide()
        self.lbl_state = QLabel("Pronto")
        self.lbl_mem = QLabel("")
        self.lbl_mem.setObjectName("Dim")
        self.lbl_pio = QLabel("")
        self.lbl_pio.setObjectName("Dim")
        self.lbl_path = QLabel("")
        self.lbl_path.setObjectName("Dim")
        self.lbl_info = QLabel("")
        self.lbl_pos = QLabel("")
        h.addWidget(self.progress)
        h.addWidget(self.lbl_state)
        h.addWidget(self.lbl_mem)
        h.addStretch(1)
        h.addWidget(self.lbl_path)
        h.addWidget(self.lbl_pio)
        h.addWidget(self.lbl_info)
        h.addWidget(self.lbl_pos)
        return st

    def _state(self, text: str, color: str = "") -> None:
        self.lbl_state.setText(text)
        self.lbl_state.setStyleSheet(f"color:{color};" if color else "")

    # ------------------------------------------------------------ progetto
    @property
    def path(self) -> str:
        ed = self.current()
        return ed.path if ed else ""

    def _set_root(self, path: str) -> None:
        self.root = os.path.abspath(path)
        self.s.setValue(S + "progetto", self.root)
        self.model.setRootPath(self.root)
        self.tree.setRootIndex(self.proxy.mapFromSource(self.model.index(self.root)))
        self._branch = ""
        self._proj_label()
        self.hist = ms.History(self.root)
        self.index.set_root(self.root)
        self.dbg.breakpoints.clear()
        self.dbg._refill()
        self._recent_add(self.root)
        self._reload_ini()
        self._reindex()
        self.git.refresh()

    def _reload_ini(self, select: str = "") -> None:
        if not self.root:
            return
        self.info = pc.read_project(self.root)
        keep = self.cmb_env.currentData() if self.cmb_env.count() else None
        want = select or (keep if keep is not None else str(self.s.value(S + "env", "") or ""))
        self.cmb_env.blockSignals(True)
        self.cmb_env.clear()
        for e in self.info.envs:
            self.cmb_env.addItem(f"{e.name}" + (f"  ·  {e.board}" if e.board else ""), e.name)
        if len(self.info.envs) > 1:
            self.cmb_env.addItem("Tutti gli ambienti (solo compilazione)", ALL)
        names = [e.name for e in self.info.envs]
        if want in names or (want == ALL and len(names) > 1):
            pick = want
        elif self.info.default_envs and self.info.default_envs[0] in names:
            pick = self.info.default_envs[0]
        else:
            pick = names[0] if names else None
        if pick is not None:
            self.cmb_env.setCurrentIndex(max(0, self.cmb_env.findData(pick)))
        self.cmb_env.blockSignals(False)
        self._env_changed()
        if self.info.error:
            self._state(self.info.error, P.danger)
        self._update_actions()

    def env_name(self) -> str:
        d = self.cmb_env.currentData()
        return d if isinstance(d, str) and d != ALL else ""

    def _env_changed(self) -> None:
        d = self.cmb_env.currentData()
        if isinstance(d, str):
            self.s.setValue(S + "env", d)
        e = self.info.env(self.env_name())
        self.lbl_info.setText(f"{e.platform} · {e.framework}" if e and e.platform else "")
        if getattr(self, "mem", None) is not None and self.hist is not None:
            self.mem.set_samples(self.hist.items, self.env_name())
        if e and e.monitor_speed and not self.term.is_connected:      # allinea il baud del monitor
            try:
                self.term.session.cb_baud.setCurrentText(str(e.monitor_speed))
            except Exception:  # noqa: BLE001
                pass

    def _update_actions(self) -> None:
        ok = bool(self.root) and not self.info.error and not self.runner.running
        for a in (self.a_build, self.a_upload, self.a_clean, self.a_boards, self.a_libs, *self._tool_actions):
            a.setEnabled(ok)
        self.a_debug.setEnabled(bool(self.root) and not self.info.error and not self.runner.running and not self.dbg.sess.running)
        self.a_stop.setEnabled(self.runner.running)
        self.cmb_env.setEnabled(ok)

    def apri_progetto(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Cartella del progetto PlatformIO", self.root or str(self.s.value(S + "parent", "") or os.path.expanduser("~")))
        if d:
            self.apri_file(d)

    def nuovo_progetto(self) -> None:
        default = str(self.s.value(S + "parent", "") or "")
        if not os.path.isdir(default):
            pio_dir = os.path.join(os.path.expanduser("~"), "Documents", "PlatformIO", "Projects")
            default = pio_dir if os.path.isdir(pio_dir) else os.path.expanduser("~")
        dlg = NewProjectDialog(self, default)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            root = pc.create_project(dlg.ed_dir.text(), dlg.ed_name.text(), dlg.preset())
            tid = dlg.cb_tpl.currentData()
            if tid and tid != "blink":
                templates.apply_template(root, tid, dlg.preset().id)
        except (OSError, ValueError) as e:
            QMessageBox.critical(self, "Nuovo progetto", str(e))
            return
        self.s.setValue(S + "parent", dlg.ed_dir.text())
        self._close_all()
        self._set_root(root)
        self.apri_file(os.path.join(root, "src", "main.cpp"))
        self._state("Progetto creato · al primo «Compila» PlatformIO scarica piattaforma e toolchain", P.ok)

    # --------------------------------------------------------------- albero
    def _tree_open(self, idx: QModelIndex) -> None:
        p = self.model.filePath(self.proxy.mapToSource(idx))
        if os.path.isfile(p):
            self.apri_file(p)

    def _tree_menu(self, pos) -> None:
        idx = self.tree.indexAt(pos)
        path = self.model.filePath(self.proxy.mapToSource(idx)) if idx.isValid() else self.root
        d = path if os.path.isdir(path) else os.path.dirname(path)
        m = QMenu(self)
        m.addAction("Nuovo file…", lambda: self._new_file(d))
        m.addAction("Nuova cartella…", lambda: self._new_dir(d))
        m.addSeparator()
        m.addAction("Mostra nella cartella", lambda: self._reveal(path))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _inside_root(self, path: str) -> bool:
        try:
            return os.path.commonpath([_norm(path), _norm(self.root)]) == _norm(self.root)
        except ValueError:
            return False

    def _new_file(self, folder: str) -> None:
        name, ok = QInputDialog.getText(self, "Nuovo file", "Nome file (es. src/sensori.cpp):", text="nuovo.h")
        if not ok or not name.strip():
            return
        path = os.path.join(folder, name.strip())
        if not self._inside_root(path):
            QMessageBox.warning(self, "Nuovo file", "Il file deve restare dentro la cartella del progetto.")
            return
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            if not os.path.exists(path):
                Path(path).write_text("", encoding="utf-8")
        except OSError as e:
            QMessageBox.critical(self, "Nuovo file", str(e))
            return
        self.apri_file(path)

    def _new_dir(self, folder: str) -> None:
        name, ok = QInputDialog.getText(self, "Nuova cartella", "Nome cartella:")
        if ok and name.strip():
            path = os.path.join(folder, name.strip())
            if self._inside_root(path):
                os.makedirs(path, exist_ok=True)

    @staticmethod
    def _reveal(path: str) -> None:
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path if os.path.isdir(path) else os.path.dirname(path)))

    # --------------------------------------------------------------- schede
    def current(self) -> Optional[PioEditor]:
        w = self.editors.currentWidget()
        return w if isinstance(w, PioEditor) else None

    def _all_editors(self) -> list[PioEditor]:
        return [self.editors.widget(i) for i in range(self.editors.count())]

    def _title(self, ed: PioEditor) -> str:
        return (os.path.basename(ed.path) or "nuovo") + (" •" if ed.document().isModified() else "")

    def _retitle(self, ed: PioEditor) -> None:
        i = self.editors.indexOf(ed)
        if i >= 0:
            self.editors.setTabText(i, self._title(ed))

    def _new_editor(self) -> PioEditor:
        ed = PioEditor()
        ed.cursor_info.connect(lambda r, c: self.lbl_pos.setText(f"{r}:{c}"))
        ed.document().modificationChanged.connect(lambda _m, e=ed: self._retitle(e))
        ed.index = self.index
        ed.auto_complete = self.act_auto.isChecked()
        ed.goto_definition.connect(self._goto_def)
        ed.breakpoint_toggled.connect(lambda ln, e=ed: self._toggle_bp(e, ln))
        return ed

    def apri_file(self, path: str, switch: bool = True) -> None:
        """Apre un file (o una cartella progetto). ``switch=False``: non cambia progetto (file del framework citati dagli errori)."""
        path = os.path.abspath(path)
        if os.path.isdir(path):
            root = path if os.path.isfile(os.path.join(path, pc.INI_NAME)) else (pc.find_project_root(path) or path)
            if _norm(root) != _norm(self.root):
                self._close_all()
                self._set_root(root)
            return
        if not os.path.isfile(path):
            return
        if switch and (not self.root or not self._inside_root(path)):
            root = pc.find_project_root(path) or os.path.dirname(path)
            if _norm(root) != _norm(self.root):
                self._close_all()
                self._set_root(root)
        for i, ed in enumerate(self._all_editors()):
            if _norm(ed.path) == _norm(path):
                self.editors.setCurrentIndex(i)
                return
        ed = self._new_editor()
        try:
            ed.load(path)
        except OSError as e:
            QMessageBox.critical(self, "Apri", str(e))
            ed.deleteLater()
            return
        if ed.language == "c/c++":
            ed.indent_unit = "  "                       # convenzione Arduino
        if self._is_external(path):
            ed.setReadOnly(True)                        # file del framework/librerie: solo lettura
        i = self.editors.addTab(ed, self._title(ed))
        self.editors.setTabToolTip(i, path + ("   (sola lettura)" if ed.isReadOnly() else ""))
        self.editors.setCurrentIndex(i)
        self._apply_diags_to(ed)
        ed.set_breakpoints(self.dbg.lines_for(ed.path))
        ed.setFocus()

    def _is_external(self, path: str) -> bool:
        """File fuori dal progetto o in .pio (framework, librerie scaricate): non si modificano."""
        if not self.root:
            return False
        if not self._inside_root(path):
            return os.path.isdir(self.root)
        parts = {x.lower() for x in Path(os.path.relpath(path, self.root)).parts}
        return ".pio" in parts

    def salva_ed(self, ed: PioEditor) -> bool:
        if not ed.path:
            return True
        try:
            ed.save()
        except OSError as e:
            QMessageBox.critical(self, "Salva", str(e))
            return False
        self._retitle(ed)
        if ed.language == "c/c++":
            self.index.update_file(ed.path, ed.toPlainText())
        self._git_timer.start()
        if os.path.basename(ed.path).lower() == pc.INI_NAME and _norm(os.path.dirname(ed.path)) == _norm(self.root):
            self._reload_ini()
        return True

    def salva_corrente(self) -> bool:
        ed = self.current()
        return self.salva_ed(ed) if ed else True

    def salva(self) -> bool:
        return all(self.salva_ed(e) for e in self._all_editors() if e.document().isModified())

    def modificato(self) -> bool:
        return any(e.document().isModified() for e in self._all_editors())

    def chiudi_scheda(self, i: int) -> None:
        ed = self.editors.widget(i)
        if ed.document().isModified():
            r = QMessageBox.question(self, "Chiudi", f"Salvare «{self._title(ed).rstrip(' •')}»?",
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
            if r == QMessageBox.StandardButton.Cancel or (r == QMessageBox.StandardButton.Save and not self.salva_ed(ed)):
                return
        self.editors.removeTab(i)
        ed.deleteLater()

    def _close_all(self) -> None:
        if self.modificato():
            r = QMessageBox.question(self, "Cambia progetto", "Salvare le modifiche ai file aperti?",
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard)
            if r == QMessageBox.StandardButton.Save:
                self.salva()
        while self.editors.count():
            ed = self.editors.widget(0)
            self.editors.removeTab(0)
            ed.deleteLater()
        self._diags.clear()
        self.problems.clear()

    def _on_current(self, _i: int) -> None:
        ed = self.current()
        self.lbl_path.setText(ed.path if ed else "")

    # ------------------------------------------------------------------ pio
    def _pio(self) -> Optional[list[str]]:
        return pc.find_pio(str(self.s.value(S + "pio_exe", "") or ""))

    def _no_pio(self) -> None:
        log.warning("pio non trovato (percorso impostato: %r)", str(self.s.value(S + "pio_exe", "") or ""))
        box = QMessageBox(self)
        box.setWindowTitle("PlatformIO non trovato")
        box.setText("Non trovo il comando «pio».")
        box.setInformativeText(
            "Installa PlatformIO Core («pip install platformio», oppure l'estensione PlatformIO IDE di VS Code, che lo mette in "
            "%USERPROFILE%\\.platformio\\penv\\Scripts), poi riprova. Se è già installato in un'altra cartella, indica il percorso di pio.exe.")
        bi = box.addButton("Installa automaticamente…", QMessageBox.ButtonRole.AcceptRole)
        b = box.addButton("Imposta percorso…", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Chiudi", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is bi:
            self.installa_pio()
        elif box.clickedButton() is b:
            self.set_pio_path()

    def set_pio_path(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Percorso di pio (pio.exe / platformio.exe)", str(self.s.value(S + "pio_exe", "") or os.path.expanduser("~")))
        if f:
            self.s.setValue(S + "pio_exe", f)
            self._probe_pio_verbose()

    def _probe_pio(self, verbose: bool = False) -> None:
        cmd = self._pio()
        if cmd is None:
            self._pio_info = "pio non trovato"
            self.lbl_pio.setText(self._pio_info)
            self.lbl_pio.setStyleSheet(f"color:{P.danger};")
            if verbose:
                self._no_pio()
            return
        if self._probe is not None:
            return
        p = QProcess(self)
        p.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._probe = p

        def fin(*_a) -> None:
            try:
                out = bytes(p.readAllStandardOutput()).decode("utf-8", "replace").strip()
                self._pio_info = out.splitlines()[-1] if out else "pio non risponde"
                self.lbl_pio.setText(self._pio_info)
                self.lbl_pio.setStyleSheet("")
                self._probe = None
                p.deleteLater()
                if verbose:
                    QMessageBox.information(self, "PlatformIO", f"{self._pio_info}\n\n{' '.join(cmd)}")
            except RuntimeError:        # finestra chiusa mentre il controllo era in corso
                pass

        def err(e) -> None:
            if e == QProcess.ProcessError.FailedToStart:
                self._pio_info = "pio non avviabile"
                self.lbl_pio.setText(self._pio_info)
                self.lbl_pio.setStyleSheet(f"color:{P.danger};")
                self._probe = None
                p.deleteLater()

        p.finished.connect(fin)
        p.errorOccurred.connect(err)
        p.start(cmd[0], cmd[1:] + ["--version"])

    def _probe_pio_verbose(self) -> None:
        self._probe_pio(verbose=True)

    # ----------------------------------------------------------- esecuzione
    def compila(self) -> None:
        e = self.env_name()
        self._start("build", ["run"] + (["-e", e] if e else []), "Compilazione")

    def carica(self) -> None:
        e = self.env_name()
        if not e:
            QMessageBox.information(self, "Carica", "Scegli un ambiente: non carico su tutte le schede insieme.")
            return
        # La porta seriale deve essere libera: stacca il monitor e lo riattacca a fine caricamento.
        self._reconnect = None
        if self.term.is_connected:
            try:
                self._reconnect = self.term.session.link.config
            except Exception:  # noqa: BLE001
                self._reconnect = None
            self.term.disconnect_link()
        if self._rp2040_upload(e):
            return
        port = self.selected_port()
        self._start("upload", ["run", "-e", e, "-t", "upload"] + (["--upload-port", port] if port else []), "Caricamento")

    # -------------------------------------------------- RP2040: BOOTSEL / UF2
    def _rp2040_upload(self, env: str) -> bool:
        """Percorso di caricamento per RP2040. True se ha avviato lui l'operazione; False: si usa il caricamento normale di pio.

        Scheda già in BOOTSEL (drive RPI-RP2): compila e copia l'UF2. Scheda in esecuzione (VID 0x2E8A): reset a 1200 baud,
        attesa del drive, poi come sopra. Nessun drive: ci pensa pio (protocollo dell'ambiente)."""
        e = self.info.env(env)
        if not e or pc.family(e.platform) != "rp2040":
            return False
        drives = pc.find_uf2_drives()
        if not drives:
            port = pc.rp_port(list_ports(), self.selected_port())
            if port:
                self.console.add_line(f"RP2040 su {port}: reset a 1200 baud verso BOOTSEL…", "cmd")
                drives = self._touch_1200_wait(port)
        if not drives:
            self.console.add_line("Nessun drive RPI-RP2: carico con il protocollo dell'ambiente (pio).", "dim")
            return False
        self._uf2_drive = drives[0]
        self._uf2_env = env
        self._start("build_uf2", ["run", "-e", env], "Compilazione")
        return True

    def _touch_1200_wait(self, port: str, timeout: float = 6.0) -> list[str]:
        import time

        try:
            import serial

            serial.Serial(port, 1200).close()
        except Exception as ex:  # noqa: BLE001
            log.warning("reset 1200 baud su %s non riuscito: %s", port, ex)
            self.console.add_line(f"Reset 1200 baud non riuscito: {ex}", "warning")
            return []
        from PySide6.QtWidgets import QApplication

        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            QApplication.processEvents()
            d = pc.find_uf2_drives()
            if d:
                return d
            time.sleep(0.2)
        return []

    def _uf2_after_build(self) -> None:
        """Compilazione riuscita con scheda in BOOTSEL: copia l'UF2 sul drive, altrimenti lascia fare a pio."""
        import shutil

        drive, env = self._uf2_drive, self._uf2_env
        uf2 = pc.find_uf2_file(self.root, env)
        if not uf2:
            self.console.add_line("Nessun .uf2 prodotto dalla compilazione: carico con «pio -t upload».", "dim")
            self._start("upload", ["run", "-e", env, "-t", "upload"], "Caricamento")
            return
        if not os.path.isdir(drive):
            drives = pc.find_uf2_drives()
            if not drives:
                self._state("Drive RPI-RP2 scomparso: rimetti la scheda in BOOTSEL e ricarica", P.danger)
                return
            drive = drives[0]
        try:
            shutil.copyfile(uf2, os.path.join(drive, os.path.basename(uf2)))
        except OSError as ex:
            log.exception("copia UF2 non riuscita")
            self._state(f"Copia UF2 su {drive} non riuscita: {ex}", P.danger)
            self.console.add_line(f"Copia UF2 non riuscita: {ex}", "error")
            return
        self.console.add_line(f"UF2 copiato su {drive} ({os.path.basename(uf2)}): la scheda si riavvia da sola.", "ok")
        self._state(f"Caricamento riuscito (UF2 su {drive})", P.ok)
        self._last_run = f"Caricamento UF2 su {drive}"

    def pulisci(self) -> None:
        e = self.env_name()
        self._start("clean", ["run", "-t", "clean"] + (["-e", e] if e else []), "Pulizia")

    def ferma(self) -> None:
        if self.runner.running:
            self.console.add_line("— interrotto dall'utente —", "warning")
            self.runner.stop()

    def _start(self, kind: str, args: list[str], label: str) -> None:
        if self.runner.running or not self.root or self.info.error:
            return
        cmd = self._pio()
        if cmd is None:
            self._no_pio()
            return
        for ed in self._all_editors():                       # salva tutto prima di compilare
            if ed.document().isModified() and ed.path and not ed.isReadOnly() and not self.salva_ed(ed):
                return
        self._run_kind = kind
        log.info("pio %s | cwd=%s | cmd=%s", kind, self.root, " ".join(cmd + args))
        self._diags.clear()
        self._mem.clear()
        self._took = None
        for ed in self._all_editors():
            ed.set_diagnostics([])
        self.problems.clear()
        self.bottom.setTabText(1, "Problemi")
        self.console.clear()
        self.bottom.setCurrentWidget(self.console)
        self.console.add_line("$ pio " + " ".join(shlex.quote(a) if " " in a else a for a in args), "cmd")
        self.lbl_mem.setText("")
        self._state(f"{label} in corso…", P.accent)
        self._bp = None
        if kind in ("build", "upload") and self.hist is not None and self.env_name():
            self._bp = ms.BuildProgress(self.hist.last_compiled(self.env_name()))
        if self._bp is not None and self._bp.known:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
        else:
            self.progress.setRange(0, 0)
        self.progress.show()
        self._clock.start()
        self._tick.start()
        self.runner.start(cmd + args, self.root)
        self._update_actions()

    def _update_elapsed(self) -> None:
        sec = self._clock.elapsed() // 1000
        self.lbl_state.setText(self.lbl_state.text().split(" · ")[0] + f" · {sec} s")

    def _on_line(self, text: str, replace: bool, transient: bool) -> None:
        text = pc.strip_ansi(text)
        d = pc.parse_diag(text)
        self.console.add_line(text, pc.line_kind(text), d, replace, transient)
        if d is not None and d.severity in ("error", "warning"):
            self._diags.setdefault(f"{_norm(pc.resolve_path(self.root, d.file))}:{d.line}:{d.col}:{d.message}", d)
        if self._bp is not None:
            self._bp.feed(text)
            if self._bp.known:
                self.progress.setValue(self._bp.percent)
        mem = pc.parse_memory(text)
        if mem:
            self._mem[mem[0]] = mem[1:]
            self.lbl_mem.setText("   ".join(f"{k} {v[0]:.1f}% ({v[1]}/{v[2]} B)" for k, v in self._mem.items()))
        sm = pc.parse_summary(text)
        if sm:
            self._took = sm[1]

    def _on_finished(self, code: int) -> None:
        self._tick.stop()
        self.progress.hide()
        name = {"build": "Compilazione", "upload": "Caricamento", "build_uf2": "Compilazione", "clean": "Pulizia", "install": "Installazione", "check": "Analisi statica",
                "test": "Test", "compiledb": "Indice simboli", "fs": "File system"}.get(self._run_kind, "Operazione")
        took = self._took if self._took is not None else self._clock.elapsed() / 1000
        errs = [d for d in self._diags.values() if d.severity == "error"]
        warns = [d for d in self._diags.values() if d.severity == "warning"]
        log.log(20 if code == 0 else 30, "pio %s terminato: codice %s in %.1f s (%d errori, %d avvisi)", self._run_kind, code, took, len(errs), len(warns))
        self._last_run = f"{name}: codice {code}, {took:.1f} s, {len(errs)} errori, {len(warns)} avvisi"
        if code != 0:
            log.warning("ultime righe di output:\n%s", "\n".join(self.console.toPlainText().splitlines()[-25:]))
        if code == 0:
            extra = f" · {len(warns)} avvisi" if warns else ""
            self._state(f"{name} riuscita · {took:.1f} s{extra}", P.ok)
        else:
            extra = f" · {len(errs)} errori" if errs else ""
            self._state(f"{name} fallita (codice {code}) · {took:.1f} s{extra}", P.danger)
        self._fill_problems()
        for ed in self._all_editors():
            self._apply_diags_to(ed)
        self._after_run(code, took)
        if code != 0 and errs:
            first = next((d for d in errs if os.path.isfile(pc.resolve_path(self.root, d.file)) and not self._is_external(pc.resolve_path(self.root, d.file))), None)
            if first is not None:
                self.open_diag(first)
        if code == 0 and self._run_kind == "build_uf2":
            self._uf2_after_build()
        if self._reconnect is not None and not self.runner.running:
            QTimer.singleShot(3000 if self._run_kind == "build_uf2" else 1500, self._reopen_monitor)
        self._update_actions()

    def _flash_before(self) -> None:
        """Il pannello Flash ha bisogno della porta libera: stacca il monitor (lo riattacca _flash_after)."""
        self._reconnect = None
        if self.term.is_connected:
            try:
                self._reconnect = self.term.session.link.config
            except Exception:  # noqa: BLE001
                self._reconnect = None
            self.term.disconnect_link()

    def _flash_after(self) -> None:
        if self._reconnect is not None:
            QTimer.singleShot(1500, self._reopen_monitor)

    def _reopen_monitor(self) -> None:
        cfg, self._reconnect = self._reconnect, None
        if cfg is None or self.term.is_connected or self.runner.running:
            return
        try:
            self.term.connect_serial(cfg.port, cfg.baud, cfg.data_bits, cfg.parity, cfg.stop_bits, cfg.flow)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------- problemi
    def _fill_problems(self) -> None:
        self.problems.clear()
        items = sorted(self._diags.values(), key=lambda d: (d.severity != "error", d.file, d.line))
        for d in items[:500]:
            full = pc.resolve_path(self.root, d.file)
            try:
                rel = os.path.relpath(full, self.root) if self.root and self._inside_root(full) else full
            except ValueError:
                rel = full
            it = QListWidgetItem(f"{'✖' if d.severity == 'error' else '⚠'}  {rel}:{d.line}:{d.col}   {d.message}")
            it.setForeground(QColor(P.danger if d.severity == "error" else WARN))
            it.setData(Qt.ItemDataRole.UserRole, d)
            self.problems.addItem(it)
        self.bottom.setTabText(1, f"Problemi ({len(items)})" if items else "Problemi")

    def _apply_diags_to(self, ed: PioEditor) -> None:
        mine = [d for d in self._diags.values() if _norm(pc.resolve_path(self.root, d.file)) == _norm(ed.path)] if ed.path else []
        ed.set_diagnostics(mine)

    def open_diag(self, d: Optional[pc.Diag]) -> None:
        if d is None:
            return
        path = pc.resolve_path(self.root, d.file)
        if not os.path.isfile(path):
            self._state(f"File non trovato: {d.file}", P.danger)
            return
        self.apri_file(path, switch=False)
        ed = self.current()
        if ed is not None and _norm(ed.path) == _norm(path):
            ed.goto(d.line, d.col)

    # ------------------------------------------------------- schede / librerie
    def _ini_path(self) -> str:
        return os.path.join(self.root, pc.INI_NAME)

    def _ini_editor(self) -> Optional[PioEditor]:
        return next((e for e in self._all_editors() if e.path and _norm(e.path) == _norm(self._ini_path())), None)

    def ini_text(self) -> str:
        """Testo corrente di platformio.ini (dall'editor se è aperto, anche con modifiche non salvate)."""
        ed = self._ini_editor()
        if ed is not None:
            return ed.toPlainText()
        with open(self._ini_path(), encoding="utf-8-sig", errors="replace") as f:
            return f.read().replace("\r\n", "\n")

    def write_ini(self, text: str) -> None:
        """Scrive platformio.ini (via editor se aperto, così resta annullabile) e rilegge ambienti."""
        ed = self._ini_editor()
        if ed is not None:
            pos = ed.textCursor().position()
            c = ed.textCursor()
            c.beginEditBlock()
            c.select(QTextCursor.SelectionType.Document)
            c.insertText(text)
            c.endEditBlock()
            c.setPosition(min(pos, len(text)))
            ed.setTextCursor(c)
            if not self.salva_ed(ed):
                raise OSError("salvataggio di platformio.ini non riuscito")
        else:
            with open(self._ini_path(), "rb") as f:
                eol = "\r\n" if b"\r\n" in f.read() else "\n"
            with open(self._ini_path(), "w", encoding="utf-8", newline="") as f:
                f.write(text.replace("\n", eol))
        self._reload_ini()

    def gestore_schede(self) -> None:
        if not self.root or self.info.error:
            return
        e = self.info.env(self.env_name())
        dlg = BoardManager(self, self._pio(), self.root, self.env_name(), e.board if e else "")
        dlg.chosen.connect(self._board_chosen)
        dlg.exec()

    def _board_chosen(self, b: pc.BoardInfo, how: str) -> None:
        env = self.env_name()
        try:
            text = self.ini_text()
            if how == "set" and env:
                self.write_ini(pc.change_env_board(text, env, b))
                sel, msg = env, f"Ambiente «{env}»: scheda {b.id} ({b.platform})"
            else:
                speed = 115200 if b.platform.startswith(("espressif", "raspberrypi")) else 9600
                new, sel = pc.add_env_for_board(text, b, speed)
                self.write_ini(new)
                msg = f"Nuovo ambiente «{sel}» per la scheda {b.id}"
        except (OSError, KeyError, ValueError) as ex:
            log.exception("gestore schede: modifica di platformio.ini non riuscita")
            QMessageBox.critical(self, "Gestore schede", str(ex))
            return
        self._reload_ini(select=sel)
        log.info("platformio.ini: %s", msg)
        self._state(msg + " · al prossimo «Compila» PlatformIO scarica la piattaforma se manca", P.ok)

    def gestore_librerie(self) -> None:
        if not self.root or self.info.error:
            return
        env = self.env_name()
        if not env:
            QMessageBox.information(self, "Librerie", "Scegli un ambiente: le librerie si aggiungono a un ambiente alla volta.")
            return
        dlg = LibraryManager(self, self._pio(), self.root, env, self.ini_text, self.write_ini)
        dlg.exec()
        if dlg.wants_install():
            self.installa_dipendenze()

    def installa_dipendenze(self) -> None:
        env = self.env_name()
        if env:
            self._start("install", ["pkg", "install", "-e", env], "Installazione")

    # ----------------------------------------------------------------- porte
    def _init_ports(self) -> None:
        self.term.watcher.ports_changed.connect(self._ports_changed)
        self.term.session.cb_port.currentIndexChanged.connect(self._term_port_changed)
        self.cmb_port.currentIndexChanged.connect(self._port_changed)
        self._ports_changed(list_ports())

    def selected_port(self) -> str:
        d = self.cmb_port.currentData()
        return d if isinstance(d, str) else ""

    def _ports_changed(self, ports: list[PortInfo]) -> None:
        self.cmb_port.blockSignals(True)
        self.cmb_port.clear()
        self.cmb_port.addItem("Automatica (pio)", "")
        for p in ports:
            hint = f"  [{KNOWN_VID[p.vid]}]" if p.vid in KNOWN_VID else ""
            self.cmb_port.addItem(p.label + hint, p.name)
            tip = (f"VID:PID {p.vid:04X}:{p.pid or 0:04X}  " if p.vid is not None else "") + (f"S/N {p.serial}" if p.serial else "")
            self.cmb_port.setItemData(self.cmb_port.count() - 1, (p.manufacturer + "  " + tip).strip(), Qt.ItemDataRole.ToolTipRole)
        self.cmb_port.setCurrentIndex(max(0, self.cmb_port.findData(self._want_port)))
        self.cmb_port.blockSignals(False)

    def _port_changed(self, _i: int) -> None:
        port = self.selected_port()
        self._want_port = port
        self.s.setValue(S + "port", port)
        if not port:
            return
        cb = self.term.session.cb_port
        if cb.findData(port) < 0:                        # il monitor ha il suo elenco: può non avere ancora la porta nuova
            cb.addItem(port, port)
        cb.setCurrentIndex(cb.findData(port))
        if self.term.is_connected:                       # il monitor segue la porta scelta
            try:
                cfg = self.term.session.link.config
                if cfg.port != port:
                    self.term.connect_serial(port, cfg.baud, cfg.data_bits, cfg.parity, cfg.stop_bits, cfg.flow)
            except Exception:  # noqa: BLE001
                pass

    def _term_port_changed(self, _i: int) -> None:
        d = self.term.session.cb_port.currentData()
        if d and d != self.selected_port() and self.cmb_port.findData(d) >= 0:
            self._want_port = d
            self.cmb_port.blockSignals(True)
            self.cmb_port.setCurrentIndex(self.cmb_port.findData(d))
            self.cmb_port.blockSignals(False)
            self.s.setValue(S + "port", d)

    # -------------------------------------------------------------- monitor
    def monitor(self) -> None:
        self.bottom.setCurrentWidget(self.term)
        if self.term.is_connected:
            self.term.terminal.setFocus()
            return
        if not self.term.session.cb_port.currentData():
            self._state("Monitor: scegli la porta nel pannello Impostazioni, poi Connetti", P.accent)
            return
        self._env_changed()
        self.term.btn_conn.click()

    # --------------------------------------------------------- dopo ogni esecuzione
    def _after_run(self, code: int, took: float) -> None:
        kind, env = self._run_kind, self.env_name()
        if kind == "check":
            n_e = sum(1 for d in self._diags.values() if d.severity == "error")
            n_w = sum(1 for d in self._diags.values() if d.severity == "warning")
            self._state(f"Analisi statica: {n_e} errori, {n_w} avvisi · {took:.1f} s", P.danger if n_e else (WARN if n_w else P.ok))
            if self._diags:
                self.bottom.setCurrentWidget(self.problems)
        if kind == "compiledb" and code == 0:
            self._reindex()
        if code == 0 and kind in ("build", "upload") and env and self.hist is not None:
            ram, flash = self._mem.get("RAM"), self._mem.get("Flash")
            if ram and flash:
                last = self.hist.for_env(env)
                compiled = self._bp.compiled if self._bp is not None else 0
                if not (last and last[-1].ram_used == ram[1] and last[-1].flash_used == flash[1] and compiled == 0):
                    self.hist.add(ms.Sample(ms.now(), env, ram[1], ram[2], flash[1], flash[2], took, compiled or (last[-1].compiled if last else 0)))
                self.mem.set_samples(self.hist.items, env)
            self._reindex()

    # ------------------------------------------------------------- indice simboli
    def _extra_index_dirs(self) -> list[str]:
        dirs = symbols.include_dirs_from_compiledb(self.root) if self.root else []
        core = Path(os.environ.get("PLATFORMIO_CORE_DIR") or Path.home() / ".platformio") / "packages"
        try:
            for pat in ("framework-arduino*/cores", "framework-arduino*/libraries"):
                dirs += [str(x) for x in core.glob(pat) if x.is_dir()]
        except OSError:
            pass
        return list(dict.fromkeys(dirs))

    def _reindex(self) -> None:
        if self._idx_busy or not self.root:
            return
        self._idx_busy = True
        self.index.extra_dirs = self._extra_index_dirs()

        def work() -> None:
            try:
                self.index.scan()
            except Exception:  # noqa: BLE001
                log.exception("indicizzazione dei simboli non riuscita")
            finally:
                self._idx_busy = False

        threading.Thread(target=work, daemon=True).start()

    def _set_autocomplete(self, on: bool) -> None:
        self.s.setValue(S + "autocomplete", on)
        for ed in self._all_editors():
            ed.auto_complete = on

    def _goto_def(self, word: str, _pos: int) -> None:
        cur = self.current()
        syms = self.index.lookup(word, cur.path if cur else "")
        if not syms:
            self._state(f"«{word}»: definizione non trovata nell'indice (prova «Aggiorna indice dei simboli» dal menu Strumenti)", WARN)
            return
        # una sola definizione per file: preferisce i .cpp alle dichiarazioni nei header
        defs = [x for x in syms if not (x.file.lower().endswith(symbols.HEADER_EXT) and x.kind == "funzione")] or syms
        if len(defs) == 1:
            self._open_symbol(defs[0])
            return
        m = QMenu(self)
        for x in defs[:25]:
            m.addAction(f"{x.kind} {x.label}   —   {_rel_path(self.root, x.file)}:{x.line}", lambda _c=False, y=x: self._open_symbol(y))
        m.exec(self.cursor().pos())

    def _open_symbol(self, sym: symbols.Symbol) -> None:
        self.apri_file(sym.file, switch=False)
        ed = self.current()
        if ed is not None and _norm(ed.path) == _norm(sym.file):
            ed.goto(sym.line, 1)

    # --------------------------------------------------------- cerca / trova / formatta
    def _unsaved(self) -> dict:
        return {e.path: e.toPlainText() for e in self._all_editors() if e.path and e.document().isModified()}

    def cerca(self) -> None:
        ed = self.current()
        seed = ed.textCursor().selectedText() if ed and ed.textCursor().hasSelection() and "\u2029" not in ed.textCursor().selectedText() else ""
        self.bottom.setCurrentWidget(self.search)
        self.search.focus_query(seed)

    def trova(self) -> None:
        ed = self.current()
        seed = ed.textCursor().selectedText() if ed and ed.textCursor().hasSelection() and "\u2029" not in ed.textCursor().selectedText() else ""
        self.find.open(seed)

    def _open_hit(self, file: str, line: int, col: int) -> None:
        self.apri_file(file, switch=False)
        ed = self.current()
        if ed is not None and _norm(ed.path) == _norm(file):
            ed.goto(line, col)

    def _reload_files(self, files: list) -> None:
        keep = {_norm(f) for f in files}
        for ed in self._all_editors():
            if ed.path and _norm(ed.path) in keep and not ed.document().isModified():
                pos = ed.textCursor().position()
                ed.load(ed.path)
                c = ed.textCursor()
                c.setPosition(min(pos, ed.document().characterCount() - 1))
                ed.setTextCursor(c)
        self._git_timer.start()

    @staticmethod
    def _find_clang_format() -> Optional[str]:
        p = shutil.which("clang-format")
        if p:
            return p
        for c in (r"C:\Program Files\LLVM\bin\clang-format.exe", r"C:\Program Files (x86)\LLVM\bin\clang-format.exe",
                  os.path.expanduser(r"~\.vscode\extensions\ms-vscode.cpptools*\LLVM\bin\clang-format.exe")):
            import glob as _g
            hit = _g.glob(c)
            if hit:
                return hit[0]
        return None

    def formatta(self) -> None:
        ed = self.current()
        if ed is None or ed.isReadOnly():
            return
        exe = self._find_clang_format() if ed.language == "c/c++" else None
        text = ed.toPlainText()
        if exe:
            import subprocess
            style = "file" if self.root and os.path.isfile(os.path.join(self.root, ".clang-format")) else "{BasedOnStyle: LLVM, IndentWidth: 2, ColumnLimit: 120, AllowShortFunctionsOnASingleLine: All}"
            try:
                r = subprocess.run([exe, f"--style={style}", f"--assume-filename={ed.path or 'x.cpp'}"], input=text, capture_output=True, text=True,
                                   encoding="utf-8", timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, subprocess.SubprocessError) as e:
                self._state(f"clang-format non riuscito: {e}", P.danger)
                return
            if r.returncode != 0 or not r.stdout:
                self._state("clang-format: " + (r.stderr or "errore").strip()[:150], P.danger)
                return
            new, how = r.stdout.replace("\r\n", "\n"), "clang-format"
        else:
            new = "\n".join(ln.rstrip() for ln in text.split("\n")).rstrip("\n") + "\n"
            how = "pulizia spazi finali (clang-format non trovato: installa LLVM per la formattazione completa)"
        if new != text:
            c = ed.textCursor()
            pos = c.position()
            c.beginEditBlock()
            c.select(QTextCursor.SelectionType.Document)
            c.insertText(new)
            c.endEditBlock()
            c.setPosition(min(pos, len(new)))
            ed.setTextCursor(c)
        self._state(f"Formattato: {how}", P.ok)

    # ------------------------------------------------------------------ Git
    def _git_map(self, m: dict) -> None:
        self.proxy.git = m
        self.tree.viewport().update()

    def _git_branch(self, b: str) -> None:
        self._branch = b
        self._proj_label()

    def _proj_label(self) -> None:
        name = os.path.basename(self.root.rstrip("/\\")) or self.root
        self.lbl_proj.setText("▸ " + name + (f"   ⎇ {self._branch}" if self._branch else ""))

    # ------------------------------------------------------------ strumenti pio
    def analisi_statica(self) -> None:
        e = self.env_name()
        if not e:
            QMessageBox.information(self, "Analisi statica", "Scegli un ambiente.")
            return
        self._start("check", ["check", "-e", e, "--skip-packages"], "Analisi statica")

    def test_unit(self) -> None:
        if not os.path.isdir(os.path.join(self.root, "test")):
            QMessageBox.information(self, "Test unitari", "Non c'è la cartella «test» nel progetto.\nMetti i test in test/ (framework Unity) e riprova.")
            return
        e = self.env_name()
        self._start("test", ["test"] + (["-e", e] if e else []), "Test")

    def compiledb(self) -> None:
        e = self.env_name()
        self._start("compiledb", ["run", "-t", "compiledb"] + (["-e", e] if e else []), "Indice simboli")

    def fs_build(self) -> None:
        e = self.env_name()
        if not e:
            QMessageBox.information(self, "File system", "Scegli un ambiente.")
            return
        if not os.path.isdir(os.path.join(self.root, "data")):
            QMessageBox.information(self, "File system", "Crea la cartella «data» nel progetto: il suo contenuto diventa il file system (LittleFS/SPIFFS).")
            return
        self._start("fs", ["run", "-e", e, "-t", "buildfs"], "File system")

    def _free_port(self) -> None:
        self._reconnect = None
        if self.term.is_connected:
            try:
                self._reconnect = self.term.session.link.config
            except Exception:  # noqa: BLE001
                self._reconnect = None
            self.term.disconnect_link()

    def fs_upload(self) -> None:
        e = self.env_name()
        if not e:
            QMessageBox.information(self, "File system", "Scegli un ambiente: non carico su tutte le schede insieme.")
            return
        if not os.path.isdir(os.path.join(self.root, "data")):
            QMessageBox.information(self, "File system", "Crea la cartella «data» nel progetto: il suo contenuto diventa il file system (LittleFS/SPIFFS).")
            return
        self._free_port()
        port = self.selected_port()
        self._start("fs", ["run", "-e", e, "-t", "uploadfs"] + (["--upload-port", port] if port else []), "Caricamento file system")

    def carica_ota(self) -> None:
        e = self.env_name()
        if not e:
            QMessageBox.information(self, "OTA", "Scegli un ambiente.")
            return
        base = e[:-4] if e.endswith("-ota") else e
        try:
            text = self.ini_text()
        except OSError as ex:
            QMessageBox.critical(self, "OTA", str(ex))
            return
        dlg = OtaDialog(self, extras.get_option(text, f"{base}-ota", "upload_port") if extras.has_env(text, f"{base}-ota") else "", base)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            new, name = extras.add_ota_env(text, base, dlg.ed_host.text(), dlg.ed_pass.text(), dlg.sp_port.value())
            self.write_ini(new)
        except (ValueError, OSError, KeyError) as ex:
            QMessageBox.warning(self, "OTA", str(ex))
            return
        self._reload_ini(select=name)
        self._start("upload", ["run", "-e", name, "-t", "upload"], "Caricamento OTA")

    def partizioni(self) -> None:
        if not self.root or self.info.error:
            return
        e = self.env_name() or (self.info.envs[0].name if self.info.envs else "")
        if not e:
            return
        from . import partitions as pt
        csv_path = os.path.join(self.root, "partitions.csv")
        try:
            text = Path(csv_path).read_text(encoding="utf-8") if os.path.isfile(csv_path) else pt.PRESETS[next(iter(pt.PRESETS))][0]
            ini = self.ini_text()
        except OSError as ex:
            QMessageBox.critical(self, "Partizioni", str(ex))
            return
        fl = extras.get_option(ini, e, "board_upload.flash_size").upper().replace(" ", "")
        flash = {"2MB": 0x200000, "4MB": 0x400000, "8MB": 0x800000, "16MB": 0x1000000, "32MB": 0x2000000}.get(fl, 0x400000)
        dlg = PartitionDialog(self, text, flash)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            Path(csv_path).write_text(dlg.csv(), encoding="utf-8", newline="\n")
            new = extras.set_partitions(ini, e)
            if dlg.flash() != flash or fl:
                from . import pio_core as _pc
                new = _pc.set_option(new, extras.section(e), "board_upload.flash_size", f"{dlg.flash() // 0x100000}MB")
            self.write_ini(new)
        except (OSError, ValueError, KeyError) as ex:
            QMessageBox.critical(self, "Partizioni", str(ex))
            return
        self.apri_file(csv_path, switch=False)
        self._state("partitions.csv salvato e collegato a platformio.ini (board_build.partitions)", P.ok)

    # ------------------------------------------------------------------ debug
    def _gdb_path(self, p: str) -> str:
        try:
            r = os.path.relpath(p, self.root) if self.root and self._inside_root(p) else p
        except ValueError:
            r = p
        return r.replace("\\", "/")

    def _dbg_argv(self) -> Optional[list[str]]:
        cmd, e = self._pio(), self.env_name()
        if cmd is None:
            self._no_pio()
            return None
        if not e:
            QMessageBox.information(self, "Debug", "Scegli un ambiente.")
            return None
        if not extras.has_debug(self.ini_text(), e):
            if not self.configura_debug():
                return None
        return cmd + ["debug", "--interface=gdb", "-e", e]

    def configura_debug(self) -> bool:
        e = self.env_name()
        if not e or not self.root:
            return False
        info = self.info.env(e)
        board = info.board if info else ""
        r = QMessageBox.question(
            self, "Debug", "Aggiungo a platformio.ini le opzioni di debug per questo ambiente?\n\n"
            "• debug_tool: USB-JTAG integrato (esp-builtin) per ESP32-S3/C3/C6/H2, altrimenti sonda esterna (esp-prog)\n"
            "• build_type = debug (il firmware viene ricompilato senza ottimizzazioni)\n"
            "• debug_init_break = tbreak setup\n\n"
            "Serve l'hardware di debug collegato (sonda JTAG o USB-JTAG). Con altre sonde cambia debug_tool a mano.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if r != QMessageBox.StandardButton.Yes:
            return False
        try:
            new, tool = extras.add_debug_options(self.ini_text(), e, board)
            self.write_ini(new)
        except (OSError, KeyError, ValueError) as ex:
            QMessageBox.critical(self, "Debug", str(ex))
            return False
        self._state(f"Debug configurato: debug_tool = {tool}", P.ok)
        return True

    def avvia_debug(self) -> None:
        self.bottom.setCurrentWidget(self.dbg)
        if self.dbg.sess.running:
            return
        for ed in self._all_editors():
            if ed.document().isModified() and ed.path and not ed.isReadOnly():
                self.salva_ed(ed)
        self.dbg.start()

    def _toggle_bp(self, ed: PioEditor, line: int) -> None:
        if not ed.path or ed.language != "c/c++":
            return
        self.dbg.toggle(ed.path, line)
        ed.set_breakpoints(self.dbg.lines_for(ed.path))

    def _dbg_location(self, file: str, line: int) -> None:
        for ed in self._all_editors():
            ed.set_exec_line(0)
        if not file:
            return
        path = pc.resolve_path(self.root, file)
        if not os.path.isfile(path):
            return
        self.apri_file(path, switch=False)
        ed = self.current()
        if ed is not None and _norm(ed.path) == _norm(path):
            ed.set_exec_line(line)

    # ---------------------------------------------------- installazione / recenti
    def installa_pio(self) -> None:
        dlg = InstallDialog(self)
        dlg.installed.connect(lambda _cmd: self._probe_pio())
        dlg.exec()
        self._probe_pio()

    def _toolchain_hint(self) -> str:
        e = self.info.env(self.env_name())
        b = (e.board if e else "").lower()
        if any(x in b for x in ("c3", "c6", "h2")):
            return "riscv"
        return "xtensa" if e and e.platform.startswith("espressif32") else ""

    def _recent_add(self, root: str) -> None:
        cur = [p for p in (self.s.value(S + "recenti", [], type=list) or []) if _norm(p) != _norm(root)]
        self.s.setValue(S + "recenti", ([root] + cur)[:10])

    def _fill_recenti(self) -> None:
        self.menu_recenti.clear()
        items = [p for p in (self.s.value(S + "recenti", [], type=list) or []) if os.path.isdir(p)]
        if not items:
            self.menu_recenti.addAction("(nessuno)").setEnabled(False)
        for p in items:
            self.menu_recenti.addAction(p, lambda _c=False, x=p: self.apri_file(x))

    # ------------------------------------------------------------ diagnostica
    def diagnostica_extra(self) -> dict:
        """Stato dell'IDE per la diagnostica (usato anche dal comando Diagnostica della suite)."""
        e = self.info.env(self.env_name())
        pio = self._pio()
        return {
            "IDE: progetto": self.root or "-",
            "IDE: ambiente": f"{self.env_name() or '(tutti)'}" + (f" board={e.board} platform={e.platform} framework={e.framework}" if e else ""),
            "IDE: errore ini": self.info.error or "nessuno",
            "IDE: pio": " ".join(pio) if pio else "NON TROVATO",
            "IDE: versione pio": self._pio_info or "-",
            "IDE: porta scelta": self.selected_port() or "automatica",
            "IDE: monitor connesso": self.term.is_connected,
            "IDE: ultima operazione": self._last_run,
            "IDE: simboli indicizzati": self.index.count(),
            "IDE: git": (gitutil.find_git() or "non trovato") + (f" · ramo {self._branch}" if self._branch else ""),
            "IDE: debug attivo": self.dbg.sess.running,
        }

    def diagnostica(self) -> None:
        DiagnosticsDialog(self, collect_diagnostics(extra=self.diagnostica_extra()), "Diagnostica IDE ESP32/Arduino",
                          "Premi «Copia diagnostica» e incolla il testo nella chat.").exec()

    # ------------------------------------------------------------- assistente
    def contesto_assistente(self) -> str:
        ed = self.current()
        s = f"Progetto PlatformIO: {self.root}\nAmbiente: {self.env_name() or '(tutti)'}\n"
        e = self.info.env(self.env_name())
        if e:
            s += f"Scheda: {e.board} · piattaforma: {e.platform} · framework: {e.framework}\n"
        if ed:
            sel = ed.textCursor().selectedText().replace(" ", "\n")
            s += f"File: {ed.path}\n" + (f"Selezione:\n{sel}\n" if sel else "") + "---\n" + ed.toPlainText()
        if self.problems.count():
            s += "\n--- Problemi ---\n" + "\n".join(self.problems.item(i).text() for i in range(self.problems.count()))
        out = self.console.toPlainText()[-3000:]
        if out.strip():
            s += f"\n--- Ultimo output ---\n{out}"
        return s

    def closeEvent(self, e) -> None:  # noqa: N802
        self.runner.stop()
        self.dbg.sess.stop()
        self.flash.shutdown()
        if self._probe is not None:
            self._probe.kill()
        self.s.setValue(S + "aperti", [x.path for x in self._all_editors() if x.path])
        try:
            self.term.shutdown()
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(e)
