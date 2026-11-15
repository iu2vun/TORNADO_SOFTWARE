"""Carta di Smith su pyqtgraph."""
import numpy as np
import pyqtgraph as pg
from .const import TRACE_COLORS


class SmithChart(pg.PlotWidget):
    def __init__(self):
        super().__init__()
        self.setBackground("#0e1013")
        self.setAspectLocked(True)
        self.hideAxis("bottom"); self.hideAxis("left")
        self.setXRange(-1.05, 1.05); self.setYRange(-1.05, 1.05)
        self.setMenuEnabled(False)
        self._draw_grid()
        self._curves: dict = {}

    def _draw_grid(self):
        grid = pg.mkPen("#2a2f37", width=1)
        rim  = pg.mkPen("#6b7280",    width=1.5)
        ax   = pg.mkPen("#4a505a",    width=1)
        t    = np.linspace(0, 2 * np.pi, 361)
        # cerchio unitario
        self.plot(np.cos(t), np.sin(t), pen=rim)
        # asse reale
        self.plot([-1, 1], [0, 0], pen=ax)
        # cerchi R-costante: centro (r/(1+r), 0), raggio 1/(1+r)
        for r in (0.2, 0.5, 1.0, 2.0, 5.0):
            cx, rr = r / (1 + r), 1 / (1 + r)
            self.plot(cx + rr * np.cos(t), rr * np.sin(t), pen=grid)
        # archi X-costante: clippati al disco unitario
        for x in (0.2, 0.5, 1.0, 2.0, 5.0):
            for sgn in (+1, -1):
                xi = sgn * x
                cx, cy, rr = 1.0, 1.0 / xi, 1.0 / abs(xi)
                gx = cx + rr * np.cos(t)
                gy = cy + rr * np.sin(t)
                m  = gx ** 2 + gy ** 2 <= 1.0001
                if m.any():
                    self.plot(gx[m], gy[m], pen=grid, connect="finite")

    def update_trace(self, name: str, gamma: np.ndarray, color: str = None):
        if name not in self._curves:
            pen = pg.mkPen(color or TRACE_COLORS.get(name, "#fff"), width=2)
            self._curves[name] = self.plot([], [], pen=pen)
        self._curves[name].setData(np.real(gamma), np.imag(gamma))

    def clear_trace(self, name: str):
        if name in self._curves:
            self.removeItem(self._curves.pop(name))

    def clear_all(self):
        for name in list(self._curves):
            self.clear_trace(name)
