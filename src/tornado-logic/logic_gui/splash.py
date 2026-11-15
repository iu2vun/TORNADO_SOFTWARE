"""Splash screen Tornado Logic: logo Tornado Laboratori, nome del programma, barra di avvio (10 s)."""
import time

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from tornado_ui import P, qcolor

from .branding import logo_pixmap

STAGES = (
    (0.0, "Avvio Tornado Logic…"),
    (1.5, "Caricamento interfaccia…"),
    (3.5, "Inizializzazione motore di cattura…"),
    (5.5, "Caricamento decoder…"),
    (8.0, "Preparazione analizzatore…"),
    (9.5, "Pronto"),
)


class SplashScreen(QWidget):
    W, H = 640, 520

    def __init__(self, version: str = ""):
        super().__init__(None, Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setFixedSize(self.W, self.H)
        self._version = version
        self._p = 0.0
        self._msg = STAGES[0][1]
        self._logo = logo_pixmap(360)
        scr = QGuiApplication.primaryScreen().availableGeometry()
        self.move(scr.center().x() - self.W // 2, scr.center().y() - self.H // 2)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#000000"))
        p.setPen(QPen(qcolor(P.accent), 1))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))

        lw = self._logo.width()
        p.drawPixmap((self.W - lw) // 2, 14, self._logo)
        y = 14 + self._logo.height() + 6

        f = QFont(self.font())
        f.setPointSizeF(24)
        f.setBold(True)
        p.setFont(f)
        p.setPen(qcolor(P.accent))
        p.drawText(QRectF(0, y, self.W, 40), Qt.AlignCenter, "Tornado Logic")

        f.setPointSizeF(10.5)
        f.setBold(False)
        p.setFont(f)
        p.setPen(qcolor(P.text_dim))
        p.drawText(QRectF(0, y + 40, self.W, 22), Qt.AlignCenter,
                   f"Analizzatore logico 8 canali   ·   v{self._version}   ·   Tornado Laboratori")

        # barra di avvio
        bx, bw, by = 60, self.W - 120, self.H - 46
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(P.raised))
        p.drawRoundedRect(QRectF(bx, by, bw, 6), 3, 3)
        p.setBrush(qcolor(P.accent))
        p.drawRoundedRect(QRectF(bx, by, bw * self._p, 6), 3, 3)
        f.setPointSizeF(9.5)
        p.setFont(f)
        p.setPen(qcolor(P.text_dim))
        p.drawText(QRectF(bx, by + 10, bw, 20), Qt.AlignLeft | Qt.AlignVCenter, self._msg)
        p.drawText(QRectF(bx, by + 10, bw, 20), Qt.AlignRight | Qt.AlignVCenter, f"{int(self._p * 100)}%")
        p.end()

    def set_progress(self, frac: float, msg: str | None = None):
        self._p = max(0.0, min(1.0, frac))
        if msg:
            self._msg = msg
        self.repaint()

    def run(self, seconds: float = 10.0, t0: float | None = None):
        """Tiene lo splash per `seconds` (dall'istante t0) aggiornando la barra e processando gli eventi."""
        t0 = time.monotonic() if t0 is None else t0
        app = QApplication.instance()
        while True:
            t = time.monotonic() - t0
            if t >= seconds:
                break
            msg = [m for s, m in STAGES if t >= s * seconds / 10.0][-1]
            self.set_progress(t / seconds, msg)
            app.processEvents()
            time.sleep(0.02)
        self.set_progress(1.0, STAGES[-1][1])
        app.processEvents()
        self.close()
