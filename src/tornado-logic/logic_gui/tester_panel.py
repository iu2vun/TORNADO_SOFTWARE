"""Pannello "Tester": comandi e stato del tester ESP32 (console seriale), con uscite animate e difetti regolabili."""
from __future__ import annotations

import html
import math
import time
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from logic_core.tester import (CYCLE_S, DESCRIPTIONS, FAULT_RANGES, MODES, ROLES, SIM_PORT, Line, TesterError,
                               TesterLink, list_ports)
from tornado_ui import TRACE_COLORS, Card, P, dim, h2

from .devices import SIM_ENABLED

from .panels import _card

MODE_LABELS = {"off": "Fermo", "mixed": "Misto", "serial": "Seriale", "walk": "Walk"}
MODE_TIPS = {m: f"mode {m}: {DESCRIPTIONS[m]}" for m in MODES}
STALE_S = 3.5            # senza righe per tanto tempo la scheda è considerata non risponde


class ChannelStrip(QWidget):
    """Le 8 uscite del tester: LED colorato come la traccia, nome del canale e ruolo nel modo corrente."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(86)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._mode: str | None = None
        self._walk: int | None = None
        self._stale = True
        self._phase = 0
        self._font = QFont(self.font())

    def set_state(self, mode: str | None, walk: int | None, stale: bool) -> None:
        if (mode, walk, stale) != (self._mode, self._walk, self._stale):
            self._mode, self._walk, self._stale = mode, walk, stale
            self.update()

    def tick(self) -> None:
        """Pulsazione lenta dei LED attivi (indica 'sta generando', non la forma d'onda vera)."""
        self._phase = (self._phase + 1) % 12
        if self._mode in ("mixed", "serial") and not self._stale:
            self.update()

    def lit(self, ch: int) -> bool:
        if self._stale or self._mode in (None, "off"):
            return False
        if self._mode == "walk":
            return self._walk == ch
        return True

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cw = w / 8.0
        roles = ROLES.get(self._mode or "off", ROLES["off"])
        small = QFont(self._font)
        small.setPointSizeF(max(7.0, self._font.pointSizeF() - 2))
        bold = QFont(self._font)
        bold.setBold(True)
        for ch in range(8):
            cx = cw * ch + cw / 2
            col = QColor(TRACE_COLORS[ch % len(TRACE_COLORS)])
            lit = self.lit(ch)
            pulse = 0.65 + 0.35 * math.sin(self._phase / 12.0 * 2 * math.pi + ch) if self._mode in ("mixed", "serial") else 1.0
            if lit:
                glow = QColor(col)
                glow.setAlphaF(0.28 * pulse)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(glow)
                p.drawEllipse(QPointF(cx, 20), 15, 15)
                fill = QColor(col)
                fill.setAlphaF(0.55 + 0.45 * pulse)
                p.setBrush(fill)
                p.setPen(QPen(col, 1.5))
            else:
                p.setBrush(QColor(P.raised))
                p.setPen(QPen(QColor(P.border), 1.5))
            p.drawEllipse(QPointF(cx, 20), 9, 9)
            p.setFont(bold)
            p.setPen(QColor(P.text if lit else P.text_dim))
            p.drawText(QRectF(cx - cw / 2, 34, cw, 18), Qt.AlignmentFlag.AlignCenter, f"CH{ch + 1}")
            p.setFont(small)
            p.setPen(QColor(P.text_dim))
            txt = "—" if self._stale else roles[ch]
            p.drawText(QRectF(cx - cw / 2 + 2, 52, cw - 4, h - 54),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, txt)
        p.end()


class _History(QLineEdit):
    """Riga di comando con cronologia (frecce su/giù)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._hist: list[str] = []
        self._pos = 0

    def remember(self, text: str) -> None:
        if text and (not self._hist or self._hist[-1] != text):
            self._hist.append(text)
        self._pos = len(self._hist)

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Up and self._hist:
            self._pos = max(0, self._pos - 1)
            self.setText(self._hist[self._pos])
        elif e.key() == Qt.Key.Key_Down and self._hist:
            self._pos = min(len(self._hist), self._pos + 1)
            self.setText(self._hist[self._pos] if self._pos < len(self._hist) else "")
        else:
            super().keyPressEvent(e)


class TesterPanel(QWidget):
    connectionChanged = Signal(bool)

    def __init__(self, store: Callable | None = None, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self.link: TesterLink | None = None
        self._sim_kw: dict = {}              # per i test: SimTesterTransport(walk_s=...)
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)

        # ---- colonna 1: collegamento e modo
        card, lay = _card("Tester ESP32")
        row = QHBoxLayout()
        self.port = QComboBox()
        self.port.setMinimumWidth(150)
        self.btn_refresh = QPushButton("↻")
        self.btn_refresh.setFixedWidth(32)
        self.btn_refresh.setToolTip("Aggiorna l'elenco delle porte")
        row.addWidget(self.port, 1)
        row.addWidget(self.btn_refresh)
        lay.addLayout(row)
        self.btn_connect = QPushButton("Connetti")
        self.btn_connect.setProperty("primary", True)
        lay.addWidget(self.btn_connect)
        self.lbl_link = dim("Non collegato")
        self.lbl_link.setWordWrap(True)
        lay.addWidget(self.lbl_link)
        grid = QGridLayout()
        grid.setSpacing(6)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_btn: dict[str, QPushButton] = {}
        for i, m in enumerate(MODES):
            b = QPushButton(MODE_LABELS[m])
            b.setCheckable(True)
            b.setToolTip(MODE_TIPS[m])
            self.mode_group.addButton(b)
            self.mode_btn[m] = b
            grid.addWidget(b, i // 2, i % 2)
            b.clicked.connect(lambda _=False, mm=m: self.send(f"mode {mm}"))
        lay.addLayout(grid)
        lay.addStretch(1)
        root.addWidget(card, 0)

        # ---- colonna 2: difetti
        card, lay = _card("Difetti (modo seriale)")
        f = QGridLayout()
        f.setHorizontalSpacing(8)
        f.setVerticalSpacing(6)
        self.sp_baud = QDoubleSpinBox()
        self.sp_baud.setRange(*FAULT_RANGES["baud"])
        self.sp_baud.setDecimals(1)
        self.sp_baud.setSingleStep(0.5)
        self.sp_baud.setSuffix(" %")
        self.sp_baud.setToolTip("Errore del baud rate della linea: +2 % = più veloce del 2 %")
        self.sp_lead = QSpinBox()
        self.sp_lead.setRange(*map(int, FAULT_RANGES["delead"]))
        self.sp_lead.setSuffix(" µs")
        self.sp_lead.setToolTip("Anticipo di DE sullo start bit; negativo = DE attivato in ritardo")
        self.sp_lag = QSpinBox()
        self.sp_lag.setRange(*map(int, FAULT_RANGES["delag"]))
        self.sp_lag.setSuffix(" µs")
        self.sp_lag.setToolTip("Ritardo del rilascio di DE dopo lo stop bit; negativo = rilasciato prima della fine")
        self.chk_parity = QCheckBox("Errore di parità")
        self.chk_parity.setToolTip("Inverte il bit di parità dei byte RS-485")
        self.chk_crc = QCheckBox("CRC Modbus errato")
        self.chk_crc.setToolTip("Un bit del CRC sbagliato nelle risposte Modbus dei cicli dispari (una sì e una no)")
        self.btn_reset = QPushButton("Azzera difetti")
        for r, (lbl, w) in enumerate((("Baud", self.sp_baud), ("DE anticipo", self.sp_lead), ("DE ritardo", self.sp_lag))):
            f.addWidget(QLabel(lbl), r, 0)
            f.addWidget(w, r, 1)
        f.addWidget(self.chk_parity, 3, 0, 1, 2)
        f.addWidget(self.chk_crc, 4, 0, 1, 2)
        lay.addLayout(f)
        lay.addWidget(self.btn_reset)
        self.lbl_faults = dim("")
        self.lbl_faults.setWordWrap(True)
        lay.addWidget(self.lbl_faults)
        lay.addStretch(1)
        root.addWidget(card, 0)

        # ---- colonna 3: uscite + stato + console
        right = QVBoxLayout()
        right.setSpacing(8)
        card = Card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(12, 8, 12, 8)
        cl.setSpacing(4)
        top = QHBoxLayout()
        top.addWidget(h2("Uscite"))
        self.lbl_stats = dim("")
        self.lbl_stats.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.lbl_stats, 1)
        cl.addLayout(top)
        self.strip = ChannelStrip()
        cl.addWidget(self.strip)
        self.lbl_doing = dim("")
        self.lbl_doing.setWordWrap(True)
        cl.addWidget(self.lbl_doing)
        right.addWidget(card)

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(1500)
        mono = QFont(self.console.font())
        mono.setFamilies(["Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono", "monospace"])
        self.console.setFont(mono)
        right.addWidget(self.console, 1)
        row = QHBoxLayout()
        self.cmd = _History()
        self.cmd.setPlaceholderText("comando per l'ESP32 (help)…  ↑ ↓ cronologia")
        row.addWidget(self.cmd, 1)
        self.btn_help = QPushButton("help")
        self.btn_status = QPushButton("status")
        self.btn_clear = QPushButton("Pulisci")
        for b in (self.btn_help, self.btn_status, self.btn_clear):
            row.addWidget(b)
        right.addLayout(row)
        root.addLayout(right, 1)

        # ---- collegamenti
        self.btn_refresh.clicked.connect(self.refresh_ports)
        self.btn_connect.clicked.connect(self._toggle)
        self.cmd.returnPressed.connect(self._send_typed)
        self.btn_help.clicked.connect(lambda: self.send("help"))
        self.btn_status.clicked.connect(lambda: self.send("status"))
        self.btn_clear.clicked.connect(self.console.clear)
        self.btn_reset.clicked.connect(lambda: self.send("faults reset"))
        self._t_fault = QTimer(self)
        self._t_fault.setSingleShot(True)
        self._t_fault.setInterval(350)
        self._t_fault.timeout.connect(self._send_faults)
        self._edited: set[str] = set()
        for w, key in ((self.sp_baud, "baud"), (self.sp_lead, "delead"), (self.sp_lag, "delag")):
            w.valueChanged.connect(lambda _v=0, k=key: self._edit(k))
        self.chk_parity.toggled.connect(lambda _c=False: self._edit("parity"))
        self.chk_crc.toggled.connect(lambda _c=False: self._edit("crc"))

        self._t_rx = QTimer(self)
        self._t_rx.setInterval(40)
        self._t_rx.timeout.connect(self._on_rx)
        self._t_poll = QTimer(self)
        self._t_poll.setInterval(1000)
        self._t_poll.timeout.connect(self._poll)
        self._t_anim = QTimer(self)
        self._t_anim.setInterval(120)
        self._t_anim.timeout.connect(self.strip.tick)
        self._t_anim.start()
        self.refresh_ports()
        self._refresh_ui()

    # ------------------------------------------------------------------ porte
    def refresh_ports(self) -> None:
        keep = self.port.currentData() or (self._store().value("tester/port", "", type=str) if self._store else "")
        self.port.clear()
        ports = list_ports()
        for dev, desc in ports:
            self.port.addItem(f"{dev} — {desc}" if desc else dev, dev)
        if SIM_ENABLED:
            self.port.addItem("Simulato (nessuna scheda)", SIM_PORT)
        i = self.port.findData(keep)
        if i < 0:
            i = next((k for k, (_, d) in enumerate(ports) if "CP210" in d.upper() or "CH340" in d.upper()), -1)
        self.port.setCurrentIndex(i if i >= 0 else 0)

    def connected(self) -> bool:
        return self.link is not None and self.link.alive

    def _toggle(self) -> None:
        if self.link is not None:
            self.disconnect_tester()
        else:
            self.connect_tester(self.port.currentData())

    def connect_tester(self, port: str) -> bool:
        if self.link is not None:
            return True
        try:
            self.link = TesterLink.open(port, **(self._sim_kw if port == SIM_PORT else {}))
        except TesterError as e:
            self.link = None
            self._log(str(e), "error")
            self.lbl_link.setText(str(e))
            return False
        if self._store:
            self._store().setValue("tester/port", port)
        self._log(f"Collegato a {port}", "info")
        self._t_rx.start()
        self._t_poll.start()
        self.send("status", quiet=True)
        self._refresh_ui()
        self.connectionChanged.emit(True)
        return True

    def disconnect_tester(self, reason: str | None = None) -> None:
        link, self.link = self.link, None
        self._t_rx.stop()
        self._t_poll.stop()
        self._t_fault.stop()
        if link is not None:
            link.close()
            self._log(reason or "Scollegato", "info")
        self._refresh_ui()
        self.connectionChanged.emit(False)

    # ---------------------------------------------------------------- comandi
    def send(self, cmd: str, quiet: bool = False) -> bool:
        if self.link is None:
            self._log("Non collegato: premi Connetti", "error")
            return False
        try:
            self.link.send(cmd, quiet=quiet)
        except TesterError as e:
            self.disconnect_tester(f"Collegamento perso: {e}")
            return False
        return True

    def _send_typed(self) -> None:
        text = self.cmd.text().strip()
        if not text:
            return
        self.cmd.remember(text)
        self.cmd.clear()
        self.send(text)

    def _poll(self) -> None:
        if self.link is not None:
            self.send("status", quiet=True)

    def mode(self) -> str | None:
        return self.link.state.mode if self.link is not None else None

    def ensure_walk(self) -> str | None:
        """Mette il tester in modo walk; restituisce il modo precedente se è stato cambiato, altrimenti None."""
        prev = self.mode()
        if not self.connected() or prev == "walk":
            return None
        return prev if self.send("mode walk") else None

    def restore_mode(self, prev: str | None) -> None:
        if prev in MODES and self.connected():
            self.send(f"mode {prev}")

    # ---------------------------------------------------------------- difetti
    def _edit(self, key: str) -> None:
        if self._syncing:
            return
        self._edited.add(key)
        self._hold_until = time.monotonic() + 1.0       # finché si digita, lo stato della scheda non riscrive i campi
        self._t_fault.start()

    _syncing = False
    _hold_until = 0.0

    def _send_faults(self) -> None:
        edited, self._edited = self._edited, set()
        self._hold_until = time.monotonic() + 0.8      # il tempo che la risposta torni indietro
        if "baud" in edited:
            self.send(f"baud {self.sp_baud.value():g}")
        if "delead" in edited:
            self.send(f"delead {self.sp_lead.value()}")
        if "delag" in edited:
            self.send(f"delag {self.sp_lag.value()}")
        if "parity" in edited:
            self.send("parity on" if self.chk_parity.isChecked() else "parity off")
        if "crc" in edited:
            self.send("crc on" if self.chk_crc.isChecked() else "crc off")

    # ----------------------------------------------------------------- ricezione
    def _on_rx(self) -> None:
        link = self.link
        if link is None:
            return
        for ln in link.drain():
            if ln.quiet:
                continue
            self._log(ln.text, ln.kind)
        if not link.alive:
            self.disconnect_tester(f"Collegamento perso: {link.error or 'porta chiusa'}")
            return
        self._refresh_ui()

    def _log(self, text: str, kind: str = "other") -> None:
        col = {"tx": P.accent, "error": P.danger, "info": P.text_dim, "walk": P.text_dim, "mode": P.ok,
               "boot": P.ok}.get(kind, P.text)
        prefix = "› " if kind == "tx" else ""
        stamp = time.strftime("%H:%M:%S")
        sb = self.console.verticalScrollBar()
        at_end = sb.value() >= sb.maximum() - 4
        self.console.appendHtml(f'<span style="color:{P.text_dim}">{stamp}</span> '
                                f'<span style="color:{col}">{html.escape(prefix + text)}</span>')
        if at_end:                                   # segue le righe nuove, a meno che si stia leggendo più su
            sb.setValue(sb.maximum())

    # ---------------------------------------------------------------- aspetto
    def _stale(self) -> bool:
        link = self.link
        return link is None or time.monotonic() - link.state.last_rx > STALE_S

    def _refresh_ui(self) -> None:
        link = self.link
        on = link is not None
        st = link.state if on else None
        stale = self._stale()
        self.btn_connect.setText("Disconnetti" if on else "Connetti")
        self.btn_connect.setProperty("primary", not on)
        self.btn_connect.style().unpolish(self.btn_connect)
        self.btn_connect.style().polish(self.btn_connect)
        self.port.setEnabled(not on)
        self.btn_refresh.setEnabled(not on)
        for w in (self.cmd, self.btn_help, self.btn_status, *self.mode_btn.values()):
            w.setEnabled(on)
        mode = st.mode if on else None
        for m, b in self.mode_btn.items():
            b.blockSignals(True)
            if m == mode:
                b.setChecked(True)
            elif b.isChecked():
                self.mode_group.setExclusive(False)
                b.setChecked(False)
                self.mode_group.setExclusive(True)
            b.blockSignals(False)
            if bool(b.property("primary")) != (m == mode):         # il modo attivo è il pulsante arancione
                b.setProperty("primary", m == mode)
                b.style().unpolish(b)
                b.style().polish(b)
        in_serial = on and mode == "serial"
        for w in (self.sp_baud, self.sp_lead, self.sp_lag, self.chk_parity, self.chk_crc):
            w.setEnabled(in_serial)
        self.btn_reset.setEnabled(on)
        self.lbl_faults.setText("" if in_serial or not on else "I difetti si applicano al modo seriale: attivalo per modificarli.")
        if on and time.monotonic() >= self._hold_until and not self._edited:
            self._syncing = True
            try:
                for w, v in ((self.sp_baud, st.baud_err), (self.sp_lead, st.de_lead), (self.sp_lag, st.de_lag)):
                    if not w.hasFocus() and w.value() != v:
                        w.setValue(v)
                if self.chk_parity.isChecked() != st.parity_err and not self._t_fault.isActive():
                    self.chk_parity.setChecked(st.parity_err)
                if self.chk_crc.isChecked() != st.crc_err and not self._t_fault.isActive():
                    self.chk_crc.setChecked(st.crc_err)
            finally:
                self._syncing = False
        self.strip.set_state(mode, st.walk_ch if st else None, stale)
        if not on:
            self.lbl_link.setText("Non collegato")
            self.lbl_stats.setText("")
            self.lbl_doing.setText("Collega il tester per vedere cosa sta generando e mandargli i comandi.")
            return
        self.lbl_link.setText(f"{link.name}: nessuna risposta da più di {STALE_S:.0f} s" if stale
                              else f"Collegato a {link.name}")
        if stale or mode is None:
            self.lbl_stats.setText("")
            self.lbl_doing.setText("In attesa di risposta dalla scheda…" if mode is None else
                                   "La scheda non risponde: cavo, porta occupata o firmware bloccato.")
            return
        parts = []
        if mode in CYCLE_S:
            parts.append(f"cicli {st.cycles}")
            if st.rate is not None:
                parts.append(f"{st.rate:.0f}/s (attesi {1 / CYCLE_S[mode]:.0f})")
        if st.compose_errors:
            parts.append(f"errori di composizione {st.compose_errors}")
        parts.append(f"CPU {st.cpu_mhz} MHz")
        self.lbl_stats.setText(" · ".join(parts))
        doing = DESCRIPTIONS.get(mode, "")
        if mode == "walk" and st.walk_ch is not None:
            doing += f" Ora: CH{st.walk_ch + 1} alto."
        if mode == "serial" and (st.baud_err or st.de_lead != 100 or st.de_lag != 60 or st.parity_err or st.crc_err):
            fl = []
            if st.baud_err:
                fl.append(f"baud {st.baud_err:+g} %")
            if st.de_lead != 100:
                fl.append(f"DE anticipo {st.de_lead} µs")
            if st.de_lag != 60:
                fl.append(f"DE ritardo {st.de_lag} µs")
            if st.parity_err:
                fl.append("parità errata")
            if st.crc_err:
                fl.append("CRC Modbus errato (cicli dispari)")
            doing += " Difetti attivi: " + ", ".join(fl) + "."
        self.lbl_doing.setText(doing)

    def shutdown(self) -> None:
        self.disconnect_tester()
