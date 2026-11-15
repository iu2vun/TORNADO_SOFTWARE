"""Viste del mosaico. Ogni vista legge dall'hub (storico + lotti) con i propri filtri canale/direzione."""
from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPalette, QPen, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit,
    QSizePolicy, QTableView, QToolButton, QToolTip, QVBoxLayout, QWidget,
)

from ..chunk import DIR_NAMES, GLOBAL, RX, TX, Chunk
from ..config import PortConfig
from ..formats import StreamDecoder, hexdump, render
from .hub import DataHub
from .theme import channel_color, colors_for, mono_font

_NUMERIC = ("hex", "dec", "oct", "bin", "ascii", "escaped")


def _nice_step(span: float, target: int = 6) -> float:
    raw = max(span / target, 1e-9)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def _nice_max(v: float) -> float:
    """Primo valore 1/2/5 x 10^k >= v (minimo 1)."""
    if v <= 1:
        return 1.0
    mag = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 5, 10):
        if v <= m * mag:
            return float(m * mag)
    return float(10 * mag)


def _fmt_rate(v: float) -> str:
    if v >= 1_000_000:
        return f"{v / 1e6:.1f} MB/s"
    if v >= 1000:
        return f"{v / 1e3:.1f} kB/s"
    return f"{v:.0f} B/s"


# ============================================================================ base
class BaseTile(QWidget):
    kind = "base"
    title = "Vista"
    has_clear = True
    PENDING_MAX = 200_000
    REBUILD_MAX = 20_000

    def __init__(self, hub: DataHub, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.hub = hub
        self.channel_filter: Optional[int] = None
        self.dir_filter: Optional[int] = None
        self._paused = False
        self._pending: list[Chunk] = []

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(3, 3, 3, 3)
        self._root.setSpacing(3)
        self._bar = QHBoxLayout()
        self._bar.setSpacing(4)
        self._root.addLayout(self._bar)

        self.cmb_channel = QComboBox()
        self.cmb_channel.setToolTip("Canale mostrato")
        self.cmb_dir = QComboBox()
        self.cmb_dir.setToolTip("Direzione mostrata")
        for label, val in (("RX+TX", None), ("RX", RX), ("TX", TX)):
            self.cmb_dir.addItem(label, val)
        self._bar.addWidget(self.cmb_channel)
        self._bar.addWidget(self.cmb_dir)
        self._extra_index = self._bar.count()
        self._bar.addStretch(1)
        self.btn_pause = QToolButton()
        self.btn_pause.setText("Pausa")
        self.btn_pause.setCheckable(True)
        self.btn_pause.setToolTip("Congela la vista; i dati continuano ad arrivare e compaiono alla ripresa")
        self._bar.addWidget(self.btn_pause)
        self.btn_clear = QToolButton()
        self.btn_clear.setText("Pulisci")
        self.btn_clear.setToolTip("Svuota questa vista (l'acquisizione non viene toccata)")
        self.btn_clear.setVisible(self.has_clear)
        self._bar.addWidget(self.btn_clear)

        self._refresh_channels()
        self.cmb_channel.currentIndexChanged.connect(self._on_channel_changed)
        self.cmb_dir.currentIndexChanged.connect(self._on_dir_changed)
        self.btn_pause.toggled.connect(self._on_pause_toggled)
        self.btn_clear.clicked.connect(self._on_clear_clicked)
        hub.batch.connect(self._on_batch)
        hub.cleared.connect(self._on_hub_cleared)
        hub.channels_changed.connect(self._refresh_channels)

    # --------------------------------------------------------- da specializzare
    def append(self, chunks: list[Chunk]) -> None:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError

    def on_pause(self, paused: bool) -> None:
        pass

    # ------------------------------------------------------------------ comuni
    def _insert_extra(self, widget: QWidget) -> None:
        self._bar.insertWidget(self._extra_index, widget)
        self._extra_index += 1

    def accepts(self, c: Chunk) -> bool:
        if c.is_event:
            return self.channel_filter is None or c.channel in (self.channel_filter, GLOBAL)
        if self.channel_filter is not None and c.channel != self.channel_filter:
            return False
        return self.dir_filter is None or c.direction == self.dir_filter

    def rebuild(self) -> None:
        self.reset()
        items = [c for c in self.hub.history if self.accepts(c)]
        items = items[-self.REBUILD_MAX:]
        if items:
            self.append(items)

    def _on_batch(self, items: list) -> None:
        flt = [c for c in items if self.accepts(c)]
        if not flt:
            return
        if self._paused:
            self._pending.extend(flt)
            if len(self._pending) > self.PENDING_MAX:
                del self._pending[: len(self._pending) - self.PENDING_MAX]
            return
        self.append(flt)

    def _on_hub_cleared(self) -> None:
        self._pending.clear()
        self.reset()

    def _on_clear_clicked(self) -> None:
        self._pending.clear()
        self.reset()

    def _on_pause_toggled(self, on: bool) -> None:
        self._paused = on
        self.btn_pause.setText("Riprendi" if on else "Pausa")
        self.on_pause(on)
        if not on and self._pending:
            pending, self._pending = self._pending, []
            self.append(pending)

    def _refresh_channels(self) -> None:
        cur = self.channel_filter
        self.cmb_channel.blockSignals(True)
        self.cmb_channel.clear()
        self.cmb_channel.addItem("Tutti i canali", None)
        for idx in sorted(self.hub.channels):
            self.cmb_channel.addItem(self.hub.name(idx), idx)
        pos = self.cmb_channel.findData(cur)
        if pos < 0:
            pos = 0
            self.channel_filter = None
        self.cmb_channel.setCurrentIndex(pos)
        self.cmb_channel.blockSignals(False)

    def _on_channel_changed(self) -> None:
        self.channel_filter = self.cmb_channel.currentData()
        self.rebuild()

    def _on_dir_changed(self) -> None:
        self.dir_filter = self.cmb_dir.currentData()
        self.rebuild()

    def describe(self, c: Chunk) -> str:
        head = f"t = {c.ts_ns / 1e9:.6f} s   {self.hub.name(c.channel)}   "
        if c.is_event:
            return head + f"evento: {c.text}"
        body = hexdump(c.data[:256])
        more = f"\n… (+{len(c.data) - 256} byte)" if len(c.data) > 256 else ""
        return head + f"{DIR_NAMES[c.direction]}   {len(c.data)} byte\n{body}{more}"

    # --------------------------------------------------------------- persistenza
    def get_state(self) -> dict:
        return {"channel": self.channel_filter, "dir": self.dir_filter}

    def set_state(self, st: dict) -> None:
        self.channel_filter = st.get("channel")
        self.dir_filter = st.get("dir")
        self._refresh_channels()
        self.cmb_dir.blockSignals(True)
        self.cmb_dir.setCurrentIndex(max(0, self.cmb_dir.findData(self.dir_filter)))
        self.cmb_dir.blockSignals(False)


# ============================================================================ dati
FORMATS_DATA = [
    ("Hexdump", "hexdump"), ("Hex", "hex"), ("Dec", "dec"), ("Oct", "oct"), ("Bin", "bin"),
    ("ASCII", "ascii"), ("Escaped", "escaped"), ("UTF-8", "utf-8"), ("Latin-1", "latin-1"),
    ("CP437", "cp437"), ("CP1252", "cp1252"),
]


class DataTile(BaseTile):
    """Un blocco per messaggio (chunk), con intestazione tempo/canale/direzione, in qualunque formato."""

    kind = "data"
    title = "Dati"
    MAX_BLOCKS = 20_000

    def __init__(self, hub: DataHub, parent: Optional[QWidget] = None) -> None:
        super().__init__(hub, parent)
        self.fmt = "hexdump"
        self.headers = True
        self._offsets: dict[tuple[int, int], int] = {}
        self.cmb_fmt = QComboBox()
        for label, key in FORMATS_DATA:
            self.cmb_fmt.addItem(label, key)
        self.cmb_fmt.setToolTip("Formato di visualizzazione")
        self.chk_hdr = QCheckBox("Intestazioni")
        self.chk_hdr.setChecked(True)
        self._insert_extra(self.cmb_fmt)
        self._insert_extra(self.chk_hdr)
        self.edit = QPlainTextEdit()
        self.edit.setReadOnly(True)
        self.edit.setFont(mono_font())
        self.edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.edit.setMaximumBlockCount(self.MAX_BLOCKS)
        self._root.addWidget(self.edit, 1)
        self.cmb_fmt.currentIndexChanged.connect(self._on_fmt_changed)
        self.chk_hdr.toggled.connect(self._on_hdr_toggled)
        self.rebuild()

    def _on_fmt_changed(self) -> None:
        self.fmt = self.cmb_fmt.currentData()
        self.rebuild()

    def _on_hdr_toggled(self, on: bool) -> None:
        self.headers = on
        self.rebuild()

    def reset(self) -> None:
        self.edit.clear()
        self._offsets.clear()

    def _render(self, c: Chunk) -> str:
        prefix = ""
        if self.headers:
            tag = "EVT" if c.is_event else DIR_NAMES[c.direction]
            prefix = f"{c.ts_ns / 1e9:10.4f} {self.hub.name(c.channel):<6} {tag} "
        if c.is_event:
            return f"{prefix}# {c.text}"
        if self.fmt == "hexdump":
            key = (c.channel, c.direction)
            off = self._offsets.get(key, 0)
            self._offsets[key] = off + len(c.data)
            lines = hexdump(c.data, off).split("\n")
            pad = " " * len(prefix)
            return prefix + lines[0] + "".join("\n" + pad + ln for ln in lines[1:])
        if self.fmt in _NUMERIC:
            return prefix + render(c.data, self.fmt)
        return prefix + render(c.data, self.fmt).replace("\r", "\\r").replace("\n", "\\n")

    def append(self, chunks: list[Chunk]) -> None:
        col = colors_for(self.palette())
        sb = self.edit.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 2
        cur = QTextCursor(self.edit.document())
        cur.movePosition(QTextCursor.MoveOperation.End)
        cur.beginEditBlock()
        for c in chunks:
            fmt = QTextCharFormat()
            if c.is_event:
                fmt.setForeground(col["event"])
                fmt.setFontItalic(True)
            else:
                fmt.setForeground(col["rx"] if c.direction == RX else col["tx"])
            cur.insertText(self._render(c) + "\n", fmt)
        cur.endEditBlock()
        if at_bottom:
            sb.setValue(sb.maximum())

    def get_state(self) -> dict:
        return {**super().get_state(), "fmt": self.fmt, "headers": self.headers}

    def set_state(self, st: dict) -> None:
        self.fmt = st.get("fmt", "hexdump")
        self.headers = bool(st.get("headers", True))
        for w, setter in ((self.cmb_fmt, lambda: self.cmb_fmt.setCurrentIndex(max(0, self.cmb_fmt.findData(self.fmt)))),
                           (self.chk_hdr, lambda: self.chk_hdr.setChecked(self.headers))):
            w.blockSignals(True)
            setter()
            w.blockSignals(False)
        super().set_state(st)
        self.rebuild()


# ====================================================================== terminale
_CTRL = {i: "·" for i in range(32) if i not in (9, 10)}
_CTRL[127] = "·"
CODECS = [("UTF-8", "utf-8"), ("Latin-1", "latin-1"), ("CP437", "cp437"), ("CP1252", "cp1252"), ("ASCII", "ascii")]


class TerminalTile(BaseTile):
    """Testo continuo decodificato (CR, LF e CRLF vanno a capo); i caratteri multibyte spezzati
    fra chunk diversi restano intatti. RX e TX hanno colori diversi."""

    kind = "terminal"
    title = "Terminale"
    MAX_BLOCKS = 20_000

    def __init__(self, hub: DataHub, parent: Optional[QWidget] = None) -> None:
        super().__init__(hub, parent)
        self.codec = "utf-8"
        self._dec: dict[tuple[int, int], StreamDecoder] = {}
        self._last_cr: dict[tuple[int, int], bool] = {}
        self._last_stream: Optional[tuple[int, int]] = None
        self._line_open = False
        self.cmb_codec = QComboBox()
        for label, key in CODECS:
            self.cmb_codec.addItem(label, key)
        self.cmb_codec.setToolTip("Codifica dei caratteri")
        self._insert_extra(self.cmb_codec)
        self.edit = QPlainTextEdit()
        self.edit.setReadOnly(True)
        self.edit.setFont(mono_font())
        self.edit.setMaximumBlockCount(self.MAX_BLOCKS)
        self._root.addWidget(self.edit, 1)
        self.cmb_codec.currentIndexChanged.connect(self._on_codec_changed)
        self.rebuild()

    def _on_codec_changed(self) -> None:
        self.codec = self.cmb_codec.currentData()
        self.rebuild()

    def reset(self) -> None:
        self.edit.clear()
        self._dec.clear()
        self._last_cr.clear()
        self._last_stream = None
        self._line_open = False

    def _normalize(self, key: tuple[int, int], text: str) -> str:
        if self._last_cr.get(key) and text.startswith("\n"):
            text = text[1:]
        if text:
            self._last_cr[key] = text.endswith("\r")
        return text.replace("\r\n", "\n").replace("\r", "\n").translate(_CTRL)

    def append(self, chunks: list[Chunk]) -> None:
        col = colors_for(self.palette())
        sb = self.edit.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 2
        cur = QTextCursor(self.edit.document())
        cur.movePosition(QTextCursor.MoveOperation.End)
        cur.beginEditBlock()

        def put(text: str, color: QColor, italic: bool = False) -> None:
            fmt = QTextCharFormat()
            fmt.setForeground(color)
            fmt.setFontItalic(italic)
            cur.insertText(text, fmt)
            self._line_open = not text.endswith("\n")

        multi = len(self.hub.channels) > 1
        for c in chunks:
            if c.is_event:
                if self._line_open:
                    put("\n", col["event"])
                put(f"# {self.hub.name(c.channel)}: {c.text}\n", col["event"], italic=True)
                self._last_stream = None
                continue
            key = (c.channel, c.direction)
            dec = self._dec.get(key)
            if dec is None:
                dec = self._dec[key] = StreamDecoder(self.codec)
            text = self._normalize(key, dec.feed(c.data))
            if not text:
                continue
            if key != self._last_stream:
                if self._line_open:
                    put("\n", col["event"])
                if multi and (self._last_stream is None or self._last_stream[0] != c.channel):
                    put(f"[{self.hub.name(c.channel)}] ", col["event"])
                self._last_stream = key
            put(text, col["rx"] if c.direction == RX else col["tx"])
        cur.endEditBlock()
        if at_bottom:
            sb.setValue(sb.maximum())

    def get_state(self) -> dict:
        return {**super().get_state(), "codec": self.codec}

    def set_state(self, st: dict) -> None:
        self.codec = st.get("codec", "utf-8")
        self.cmb_codec.blockSignals(True)
        self.cmb_codec.setCurrentIndex(max(0, self.cmb_codec.findData(self.codec)))
        self.cmb_codec.blockSignals(False)
        super().set_state(st)
        self.rebuild()


# ======================================================================== tabella
class ChunkModel(QAbstractTableModel):
    HEAD = ["Tempo (s)", "Δt (ms)", "Canale", "Dir", "Lung.", "Hex", "Testo"]
    CAP = 50_000

    def __init__(self, tile: "TableTile") -> None:
        super().__init__(tile)
        self.tile = tile
        self.rows: list[tuple[Chunk, Optional[float]]] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEAD)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEAD[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        c, delta = self.rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return f"{c.ts_ns / 1e9:.6f}"
            if col == 1:
                return "" if delta is None else f"{delta:.3f}"
            if col == 2:
                return self.tile.hub.name(c.channel)
            if col == 3:
                return "EVT" if c.is_event else DIR_NAMES[c.direction]
            if col == 4:
                return "" if c.is_event else str(len(c.data))
            if col == 5:
                if c.is_event:
                    return ""
                return render(c.data[:24], "hex") + (" …" if len(c.data) > 24 else "")
            if c.is_event:
                return c.text
            return render(c.data[:64], "escaped") + ("…" if len(c.data) > 64 else "")
        if role == Qt.ItemDataRole.ForegroundRole:
            pal = colors_for(self.tile.palette())
            return pal["event"] if c.is_event else (pal["rx"] if c.direction == RX else pal["tx"])
        if role == Qt.ItemDataRole.TextAlignmentRole and col in (0, 1, 4):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.BackgroundRole and index.row() % 2:
            return colors_for(self.tile.palette())["lane"]
        if role == Qt.ItemDataRole.ToolTipRole:
            return self.tile.describe(c)
        return None

    def append(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        chunks = chunks[-self.CAP:]
        prev = self.rows[-1][0].ts_ns if self.rows else None
        new: list[tuple[Chunk, Optional[float]]] = []
        for c in chunks:
            new.append((c, None if prev is None else (c.ts_ns - prev) / 1e6))
            prev = c.ts_ns
        overflow = len(self.rows) + len(new) - self.CAP
        if overflow > 0:
            n = min(overflow, len(self.rows))
            self.beginRemoveRows(QModelIndex(), 0, n - 1)
            del self.rows[:n]
            self.endRemoveRows()
        first = len(self.rows)
        self.beginInsertRows(QModelIndex(), first, first + len(new) - 1)
        self.rows.extend(new)
        self.endInsertRows()

    def clear(self) -> None:
        self.beginResetModel()
        self.rows.clear()
        self.endResetModel()


class TableTile(BaseTile):
    """Una riga per messaggio: tempo, Δt dal precedente, canale, direzione, lunghezza, hex, testo."""

    kind = "table"
    title = "Tabella"

    def __init__(self, hub: DataHub, parent: Optional[QWidget] = None) -> None:
        super().__init__(hub, parent)
        self.follow = True
        self.chk_follow = QCheckBox("Segui")
        self.chk_follow.setChecked(True)
        self._insert_extra(self.chk_follow)
        self.model = ChunkModel(self)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setFont(mono_font())
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setAlternatingRowColors(False)  # righe alternate dal modello: l'accento di sistema non le tinge
        self.view.setShowGrid(False)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 6)
        hh = self.view.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(True)
        for i, w in enumerate((92, 72, 70, 44, 52, 300)):
            self.view.setColumnWidth(i, w)
        self._root.addWidget(self.view, 1)
        self.chk_follow.toggled.connect(self._on_follow)
        self.rebuild()

    def _on_follow(self, on: bool) -> None:
        self.follow = on
        if on:
            self.view.scrollToBottom()

    def reset(self) -> None:
        self.model.clear()

    def append(self, chunks: list[Chunk]) -> None:
        self.model.append(chunks)
        if self.follow:
            self.view.scrollToBottom()

    def get_state(self) -> dict:
        return {**super().get_state(), "follow": self.follow}

    def set_state(self, st: dict) -> None:
        self.chk_follow.setChecked(bool(st.get("follow", True)))
        super().set_state(st)
        self.rebuild()


# ======================================================================= timeline
class TimelineCanvas(QWidget):
    LEFT, TOP, BOTTOM, RIGHT = 96, 6, 22, 8

    def __init__(self, tile: "TimelineTile") -> None:
        super().__init__(tile)
        self.tile = tile
        self._hits: list[tuple[QRectF, Chunk]] = []
        self.setMouseTracking(True)
        self.setMinimumSize(200, 80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, _event) -> None:
        tile, hub = self.tile, self.tile.hub
        pal = self.palette()
        col = colors_for(pal)
        p = QPainter(self)
        p.fillRect(self.rect(), col["base"])
        self._hits = []
        lanes = tile.lanes()
        w, h = self.width(), self.height()
        plot = QRectF(self.LEFT, self.TOP, max(10, w - self.LEFT - self.RIGHT), max(10, h - self.TOP - self.BOTTOM))
        if not lanes:
            p.setPen(col["event"])
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Nessun canale")
            return
        win = tile.window_ns()
        t_right = tile.right_ns()
        t_left = t_right - win
        lane_h = plot.height() / len(lanes)
        lane_idx = {key: i for i, key in enumerate(lanes)}

        def x_of(t: float) -> float:
            return plot.left() + (t - t_left) / win * plot.width()

        for i, (ch, d) in enumerate(lanes):
            y = plot.top() + i * lane_h
            if i % 2 == 0:
                p.fillRect(QRectF(0, y, w, lane_h), col["lane"])
            p.setPen(col["rx"] if d == RX else col["tx"])
            p.drawText(QRectF(4, y, self.LEFT - 8, lane_h),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       p.fontMetrics().elidedText(f"{hub.name(ch)} {DIR_NAMES[d]}", Qt.TextElideMode.ElideRight, self.LEFT - 8))

        step = _nice_step(win / 1e9, 6)
        k = math.ceil(t_left / 1e9 / step) * step
        p.setPen(QPen(col["grid"], 1))
        while k * 1e9 <= t_right:
            x = x_of(k * 1e9)
            p.setPen(QPen(col["grid"], 1))
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            p.setPen(col["event"])
            p.drawText(QRectF(x - 32, plot.bottom() + 3, 64, 16), Qt.AlignmentFlag.AlignHCenter, f"{k:g} s")
            k += step

        n_lane_dirs = {ch: [lane_idx[(ch, d)] for d in (RX, TX) if (ch, d) in lane_idx] for ch in {c for c, _ in lanes}}
        for c in reversed(hub.history):
            if c.ts_ns > t_right:
                continue
            if c.ts_ns < t_left - 3_000_000_000:
                break
            if not tile.accepts(c):
                continue
            if c.is_event:
                x = x_of(c.ts_ns)
                if x < plot.left():
                    continue
                if c.channel == GLOBAL:
                    p.setPen(QPen(col["event"], 1, Qt.PenStyle.DashLine))
                    p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
                    p.setPen(col["event"])
                    p.drawText(QPointF(x + 3, plot.top() + 11), c.text[:24])
                else:
                    idxs = n_lane_dirs.get(c.channel)
                    if idxs:
                        p.setPen(QPen(col["warn"], 1))
                        p.drawLine(QPointF(x, plot.top() + min(idxs) * lane_h),
                                   QPointF(x, plot.top() + (max(idxs) + 1) * lane_h))
                self._hits.append((QRectF(x - 2, plot.top(), 4, plot.height()), c))
                continue
            i = lane_idx.get((c.channel, c.direction))
            if i is None:
                continue
            x0 = x_of(c.ts_ns)
            x1 = x_of(c.ts_ns + len(c.data) * tile.char_time(c.channel) * 1e9)
            if x1 < plot.left():
                continue
            left = max(x0, plot.left())
            r = QRectF(left, plot.top() + i * lane_h + 2, max(2.0, x1 - left), max(2.0, lane_h - 4))
            p.fillRect(r, col["rx"] if c.direction == RX else col["tx"])
            if len(self._hits) < 5000:
                self._hits.append((r, c))
        if tile.paused:
            p.setPen(col["warn"])
            p.drawText(QRectF(plot.right() - 80, plot.top(), 78, 16), Qt.AlignmentFlag.AlignRight, "PAUSA")

    def mouseMoveEvent(self, ev) -> None:
        pos = ev.position()
        for r, c in reversed(self._hits):
            if r.adjusted(-2, -2, 2, 2).contains(pos):
                QToolTip.showText(ev.globalPosition().toPoint(), self.tile.describe(c), self)
                return
        QToolTip.hideText()


class TimelineTile(BaseTile):
    """Asse dei tempi con una corsia RX e una TX per canale: ogni messaggio è una barra larga quanto
    il suo tempo di trasmissione sulla linea. Passa il mouse sulle barre per il contenuto."""

    kind = "timeline"
    title = "Timeline"
    has_clear = False
    WINDOWS = [("1 s", 1), ("2 s", 2), ("5 s", 5), ("10 s", 10), ("30 s", 30), ("60 s", 60)]

    def __init__(self, hub: DataHub, parent: Optional[QWidget] = None) -> None:
        super().__init__(hub, parent)
        self.window_s = 10
        self._frozen: Optional[int] = None
        self._ct: dict[int, float] = {}
        self.cmb_win = QComboBox()
        for label, v in self.WINDOWS:
            self.cmb_win.addItem(label, v)
        self.cmb_win.setCurrentIndex(self.cmb_win.findData(10))
        self.cmb_win.setToolTip("Finestra temporale visibile")
        self._insert_extra(QLabel("Finestra"))
        self._insert_extra(self.cmb_win)
        self.canvas = TimelineCanvas(self)
        self._root.addWidget(self.canvas, 1)
        self.cmb_win.currentIndexChanged.connect(self._on_window)
        hub.channels_changed.connect(self._ct.clear)
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    @property
    def paused(self) -> bool:
        return self._paused

    def _on_window(self) -> None:
        self.window_s = self.cmb_win.currentData()
        self.canvas.update()

    def _tick(self) -> None:
        if self.isVisible() and not self._paused:
            self.canvas.update()

    def _on_batch(self, items: list) -> None:  # legge direttamente dallo storico dell'hub
        pass

    def append(self, chunks: list[Chunk]) -> None:
        pass

    def reset(self) -> None:
        self.canvas.update()

    def rebuild(self) -> None:
        self.canvas.update()

    def on_pause(self, paused: bool) -> None:
        self._frozen = self.hub.now_ns() if paused else None
        self.canvas.update()

    def window_ns(self) -> int:
        return int(self.window_s * 1e9)

    def right_ns(self) -> int:
        return self._frozen if self._frozen is not None else self.hub.now_ns()

    def lanes(self) -> list[tuple[int, int]]:
        chans = [self.channel_filter] if self.channel_filter is not None else sorted(self.hub.channels)
        dirs = (RX, TX) if self.dir_filter is None else (self.dir_filter,)
        return [(ch, d) for ch in chans for d in dirs]

    def char_time(self, ch: int) -> float:
        ct = self._ct.get(ch)
        if ct is None:
            try:
                cfg = PortConfig.parse(str(self.hub.channels.get(ch, {}).get("config", "")))
                ct = (1 + cfg.bytesize + (0 if cfg.parity == "N" else 1) + cfg.stopbits) / cfg.baudrate
            except ValueError:
                ct = 10 / 115200
            self._ct[ch] = ct
        return ct

    def get_state(self) -> dict:
        return {**super().get_state(), "window": self.window_s}

    def set_state(self, st: dict) -> None:
        self.window_s = st.get("window", 10)
        self.cmb_win.blockSignals(True)
        self.cmb_win.setCurrentIndex(max(0, self.cmb_win.findData(self.window_s)))
        self.cmb_win.blockSignals(False)
        super().set_state(st)
        self.canvas.update()


# ======================================================================= traffico
class RateCanvas(QWidget):
    LEFT, TOP, BOTTOM, RIGHT = 62, 8, 22, 10

    def __init__(self, tile: "RateTile") -> None:
        super().__init__(tile)
        self.tile = tile
        self.setMinimumSize(200, 80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, _event) -> None:
        tile, hub = self.tile, self.tile.hub
        col = colors_for(self.palette())
        p = QPainter(self)
        p.fillRect(self.rect(), col["base"])
        w, h = self.width(), self.height()
        plot = QRectF(self.LEFT, self.TOP, max(10, w - self.LEFT - self.RIGHT), max(10, h - self.TOP - self.BOTTOM))
        bin_ns = tile.bin_ns()
        right_bin = tile.right_ns() // bin_ns
        left_bin = right_bin - tile.NBINS
        series: list[tuple[tuple[int, int], list[float]]] = []
        bin_s = bin_ns / 1e9
        for key in sorted(tile.bins):
            bins = tile.bins[key]
            for b in [b for b in bins if b < left_bin - 2]:
                del bins[b]
            series.append((key, [bins.get(b, 0) / bin_s for b in range(left_bin, right_bin)]))
        top = _nice_max(max((max(v) for _, v in series if v), default=0.0))

        ticks = 4
        for i in range(ticks + 1):
            y = plot.bottom() - plot.height() * i / ticks
            p.setPen(QPen(col["grid"], 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(col["event"])
            p.drawText(QRectF(2, y - 8, self.LEFT - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       _fmt_rate(top * i / ticks))
        span_s = tile.window_s
        step = _nice_step(span_s, 5)
        k = 0.0
        while k <= span_s:
            x = plot.right() - plot.width() * k / span_s
            p.setPen(col["event"])
            p.drawText(QRectF(x - 30, plot.bottom() + 3, 60, 16), Qt.AlignmentFlag.AlignHCenter,
                       "ora" if k == 0 else f"-{k:g} s")
            k += step

        n = max(1, tile.NBINS)
        legend_y = plot.top() + 12
        for key, vals in series:
            ch, d = key
            color = channel_color(ch)
            pen = QPen(color, 1.6, Qt.PenStyle.SolidLine if d == RX else Qt.PenStyle.DashLine)
            p.setPen(pen)
            pts = [QPointF(plot.left() + plot.width() * i / n, plot.bottom() - plot.height() * min(v, top) / top)
                   for i, v in enumerate(vals)]
            if len(pts) > 1:
                p.drawPolyline(pts)
            last = vals[-1] if vals else 0.0
            total = hub.totals.get(key, 0)
            p.drawText(QPointF(plot.left() + 8, legend_y), f"{hub.name(ch)} {DIR_NAMES[d]}   {_fmt_rate(last)}   tot {total} B")
            legend_y += 15
        if tile.paused:
            p.setPen(col["warn"])
            p.drawText(QRectF(plot.right() - 80, plot.top(), 78, 16), Qt.AlignmentFlag.AlignRight, "PAUSA")


class RateTile(BaseTile):
    """Byte al secondo per canale e direzione (RX linea continua, TX tratteggiata)."""

    kind = "rate"
    title = "Traffico"
    NBINS = 120
    WINDOWS = [("10 s", 10), ("30 s", 30), ("60 s", 60), ("5 min", 300)]

    def __init__(self, hub: DataHub, parent: Optional[QWidget] = None) -> None:
        super().__init__(hub, parent)
        self.window_s = 60
        self.bins: dict[tuple[int, int], dict[int, int]] = {}
        self._frozen: Optional[int] = None
        self.cmb_win = QComboBox()
        for label, v in self.WINDOWS:
            self.cmb_win.addItem(label, v)
        self.cmb_win.setCurrentIndex(self.cmb_win.findData(60))
        self._insert_extra(QLabel("Finestra"))
        self._insert_extra(self.cmb_win)
        self.canvas = RateCanvas(self)
        self._root.addWidget(self.canvas, 1)
        self.cmb_win.currentIndexChanged.connect(self._on_window)
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self.rebuild()

    @property
    def paused(self) -> bool:
        return self._paused

    def bin_ns(self) -> int:
        return max(1, int(self.window_s * 1e9 / self.NBINS))

    def right_ns(self) -> int:
        return self._frozen if self._frozen is not None else self.hub.now_ns()

    def _on_window(self) -> None:
        self.window_s = self.cmb_win.currentData()
        self.rebuild()

    def _tick(self) -> None:
        if self.isVisible() and not self._paused:
            self.canvas.update()

    def on_pause(self, paused: bool) -> None:
        self._frozen = self.hub.now_ns() if paused else None
        self.canvas.update()

    def reset(self) -> None:
        self.bins.clear()
        self.canvas.update()

    def append(self, chunks: list[Chunk]) -> None:
        bn = self.bin_ns()
        for c in chunks:
            if c.is_event:
                continue
            d = self.bins.setdefault((c.channel, c.direction), {})
            b = c.ts_ns // bn
            d[b] = d.get(b, 0) + len(c.data)

    def rebuild(self) -> None:
        self.reset()
        newest = self.hub.history[-1].ts_ns if self.hub.history else 0
        horizon = newest - int(self.window_s * 1e9) - self.bin_ns()
        items = []
        for c in reversed(self.hub.history):
            if c.ts_ns < horizon:
                break
            if self.accepts(c):
                items.append(c)
        items.reverse()
        self.append(items)

    def get_state(self) -> dict:
        return {**super().get_state(), "window": self.window_s}

    def set_state(self, st: dict) -> None:
        self.window_s = st.get("window", 60)
        self.cmb_win.blockSignals(True)
        self.cmb_win.setCurrentIndex(max(0, self.cmb_win.findData(self.window_s)))
        self.cmb_win.blockSignals(False)
        super().set_state(st)
        self.rebuild()


from .decode_tile import DecodeTile  # noqa: E402  (import in coda: DecodeTile deriva da BaseTile)
from .search_tile import SearchTile  # noqa: E402
from .stats_tile import StatsTile  # noqa: E402

TILE_TYPES = {cls.kind: cls for cls in (DataTile, TerminalTile, TableTile, TimelineTile, RateTile, DecodeTile, StatsTile, SearchTile)}

try:  # vista ANSI/VT100: richiede Tornado-Term (presente nella suite)
    from .ansi_tile import AnsiTile
    TILE_TYPES[AnsiTile.kind] = AnsiTile
except ImportError:  # pragma: no cover
    pass
