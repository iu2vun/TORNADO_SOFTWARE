"""Area dei grafici: pannelli (rettangolare/Smith/polare/tempo) disposti in layout a splitter."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QSpinBox,
                               QSplitter, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from ..core.traces import KIND_ORDER, KINDS
from ..core.units import fmt_eng, parse_freq
from .model import Workspace
from .plot.base import PlotView
from .plot.complexplot import PolarPlot, SmithPlot
from .plot.rect import RectPlot
from .plot.timeplot import TimePlot, TimeSettings
from .theme import DARK, LIGHT, Theme

TYPE_LABELS = [("rect", "Rettangolare"), ("smith", "Smith"), ("polar", "Polare"), ("time", "Dominio tempo")]


class PlotSettingsDialog(QDialog):
    """Impostazioni del grafico: assi manuali, smoothing, legenda ecc."""

    def __init__(self, view: PlotView, parent=None):
        super().__init__(parent)
        self.view = view
        self.setWindowTitle("Impostazioni grafico")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        lay.addLayout(form)
        self.legend = QCheckBox("Mostra legenda")
        self.legend.setChecked(view.show_legend)
        form.addRow(self.legend)
        self.is_rect = isinstance(view, RectPlot)
        self.is_time = isinstance(view, TimePlot)
        if self.is_rect:
            self.minor = QCheckBox("Griglia secondaria")
            self.minor.setChecked(view.show_minor)
            form.addRow(self.minor)
            self.xlog = QCheckBox("Asse X logaritmico")
            self.xlog.setChecked(view.xlog)
            form.addRow(self.xlog)
            if not self.is_time:
                self.smooth = QSpinBox()
                self.smooth.setRange(0, 999)
                self.smooth.setSpecialValueText("off")
                self.smooth.setValue(int(view.smooth))
                form.addRow("Smoothing (punti):", self.smooth)
                self.gda = QSpinBox()
                self.gda.setRange(1, 200)
                self.gda.setValue(int(view.gd_aperture))
                form.addRow("Apertura ritardo di gruppo (± punti):", self.gda)
                self.xmin, self.xmax = QLineEdit(), QLineEdit()
                self.xmin.setPlaceholderText("auto  (es. 800M, 1.2 GHz)")
                self.xmax.setPlaceholderText("auto")
                if view.x_manual:
                    self.xmin.setText(fmt_eng(view.x_manual[0], "Hz", 8))
                    self.xmax.setText(fmt_eng(view.x_manual[1], "Hz", 8))
                form.addRow("X min:", self.xmin)
                form.addRow("X max:", self.xmax)
            self.ymin, self.ymax = QLineEdit(), QLineEdit()
            self.ymin.setPlaceholderText("auto")
            self.ymax.setPlaceholderText("auto")
            if view.y_manual:
                self.ymin.setText(f"{view.y_manual[0]:.6g}")
                self.ymax.setText(f"{view.y_manual[1]:.6g}")
            form.addRow("Y min:", self.ymin)
            form.addRow("Y max:", self.ymax)
        if isinstance(view, SmithPlot):
            self.adm = QCheckBox("Carta Y (ammettenza)")
            self.adm.setChecked(view.admittance)
            form.addRow(self.adm)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def apply(self) -> None:
        v = self.view
        v.show_legend = self.legend.isChecked()
        if isinstance(v, SmithPlot):
            v.admittance = self.adm.isChecked()
        if self.is_rect:
            v.show_minor = self.minor.isChecked()
            if v.xlog != self.xlog.isChecked():
                v.xlog = self.xlog.isChecked()
                v.x_manual = None
            xr = v.x_manual
            if not self.is_time:
                v.smooth = self.smooth.value()
                v.gd_aperture = self.gda.value()
                a, b = self.xmin.text().strip(), self.xmax.text().strip()
                xr = None
                if a and b:
                    try:
                        xr = (parse_freq(a), parse_freq(b))
                    except ValueError:
                        QMessageBox.warning(self, "Asse X", "Valori di frequenza non validi: asse lasciato in automatico.")
                        xr = None
            ya, yb = self.ymin.text().strip(), self.ymax.text().strip()
            yr = None
            if ya and yb:
                try:
                    yr = (float(ya.replace(",", ".")), float(yb.replace(",", ".")))
                except ValueError:
                    yr = None
            v.invalidate()
            v.set_ranges(xr, yr)
        v.refresh()


class PlotPanel(QFrame):
    activated = Signal(object)
    viewCreated = Signal(object)

    def __init__(self, ws: Workspace, tsettings: TimeSettings, default_type: str = "rect",
                 default_kind: str = "db", title: str = "", parent=None):
        super().__init__(parent)
        self.ws = ws
        self.ts = tsettings
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("active", False)
        self.title = title
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        head = QHBoxLayout()
        head.setContentsMargins(4, 0, 4, 0)
        lab = QLabel(title)
        lab.setProperty("title", True)
        head.addWidget(lab)
        self.type_combo = QComboBox()
        for k, t in TYPE_LABELS:
            self.type_combo.addItem(t, k)
        head.addWidget(self.type_combo)
        self.kind_combo = QComboBox()
        for k in KIND_ORDER:
            self.kind_combo.addItem(KINDS[k][0], k)
        self.kind_combo.setMinimumWidth(170)
        head.addWidget(self.kind_combo)
        head.addStretch(1)
        self.btn_auto = QToolButton()
        self.btn_auto.setText("Auto")
        self.btn_auto.setToolTip("Autoscala (Home / doppio clic)")
        self.btn_set = QToolButton()
        self.btn_set.setText("⚙")
        self.btn_set.setToolTip("Impostazioni grafico")
        self.btn_exp = QToolButton()
        self.btn_exp.setText("⤓")
        self.btn_exp.setToolTip("Esporta immagine (PNG/SVG/PDF)")
        for b in (self.btn_auto, self.btn_set, self.btn_exp):
            head.addWidget(b)
        lay.addLayout(head)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.views: dict[str, PlotView] = {}
        self.type_combo.currentIndexChanged.connect(self._type_changed)
        self.kind_combo.currentIndexChanged.connect(self._kind_changed)
        self.btn_auto.clicked.connect(self._auto)
        self.btn_set.clicked.connect(self._settings)
        self.btn_exp.clicked.connect(self._export)
        self.set_type(default_type, default_kind)

    # ------------------------------------------------------------- viste
    def _make(self, t: str) -> PlotView:
        if t == "rect":
            v = RectPlot(self.ws)
        elif t == "smith":
            v = SmithPlot(self.ws)
        elif t == "polar":
            v = PolarPlot(self.ws)
        else:
            v = TimePlot(self.ws, self.ts)
        v.installEventFilter(self)
        self.stack.addWidget(v)
        self.views[t] = v
        self.viewCreated.emit(v)
        return v

    def view_type(self) -> str:
        return self.type_combo.currentData()

    def view(self) -> PlotView:
        t = self.view_type()
        return self.views.get(t) or self._make(t)

    def set_type(self, t: str, kind: Optional[str] = None) -> None:
        i = self.type_combo.findData(t)
        self.type_combo.blockSignals(True)
        self.type_combo.setCurrentIndex(max(i, 0))
        self.type_combo.blockSignals(False)
        if kind and kind in KINDS:
            self.kind_combo.blockSignals(True)
            self.kind_combo.setCurrentIndex(self.kind_combo.findData(kind))
            self.kind_combo.blockSignals(False)
        self._type_changed()

    def _type_changed(self) -> None:
        v = self.view()
        self.stack.setCurrentWidget(v)
        self.kind_combo.setVisible(self.view_type() == "rect")
        if isinstance(v, RectPlot) and not isinstance(v, TimePlot):
            v.kind = self.kind_combo.currentData()
        v.refresh()

    def _kind_changed(self) -> None:
        v = self.view()
        if isinstance(v, RectPlot) and not isinstance(v, TimePlot):
            v.kind = self.kind_combo.currentData()
            v.y_manual = None
            v.refresh()
            v.viewChanged.emit()

    def refresh(self) -> None:
        self.view().refresh()

    def update_markers(self) -> None:
        self.view().update()

    def _auto(self) -> None:
        v = self.view()
        if isinstance(v, RectPlot):
            v.autoscale()
        else:
            v.fit()

    def _settings(self) -> None:
        d = PlotSettingsDialog(self.view(), self)
        if d.exec() == QDialog.DialogCode.Accepted:
            d.apply()

    def _export(self) -> None:
        export_view_dialog(self.view(), self)

    def set_active(self, on: bool) -> None:
        self.setProperty("active", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.FocusIn):
            self.activated.emit(self)
        return False

    # ------------------------------------------------------------- stato
    def state(self) -> dict:
        v = self.view()
        d = {"type": self.view_type(), "kind": self.kind_combo.currentData(), "legend": v.show_legend}
        if isinstance(v, RectPlot):
            d.update(xlog=v.xlog, minor=v.show_minor, smooth=v.smooth, gd=v.gd_aperture,
                     x=v.x_manual, y=v.y_manual, hlines=list(v.hlines))
        if isinstance(v, SmithPlot):
            d["admittance"] = v.admittance
        return d

    def restore(self, d: dict) -> None:
        self.set_type(d.get("type", "rect"), d.get("kind"))
        v = self.view()
        v.show_legend = d.get("legend", True)
        if isinstance(v, RectPlot):
            v.xlog = d.get("xlog", False)
            v.show_minor = d.get("minor", True)
            v.smooth = d.get("smooth", 0)
            v.gd_aperture = d.get("gd", 1)
            v.hlines = list(d.get("hlines", []))
            v.x_manual = tuple(d["x"]) if d.get("x") else None
            v.y_manual = tuple(d["y"]) if d.get("y") else None
        if isinstance(v, SmithPlot):
            v.admittance = d.get("admittance", False)
        v.refresh()


def export_view_dialog(view: PlotView, parent=None) -> Optional[str]:
    path, filt = QFileDialog.getSaveFileName(parent, "Esporta grafico", "grafico.png",
                                             "PNG (*.png);;SVG vettoriale (*.svg);;PDF (*.pdf)")
    if not path:
        return None
    box = QMessageBox(parent)
    box.setWindowTitle("Tema di esportazione")
    box.setText("Quale tema usare per l'immagine esportata?")
    b_dark = box.addButton("Scuro", QMessageBox.ButtonRole.AcceptRole)
    b_light = box.addButton("Chiaro (stampa)", QMessageBox.ButtonRole.AcceptRole)
    box.exec()
    th = DARK if box.clickedButton() is b_dark else LIGHT
    try:
        view.export_to(path, max(view.width(), 1000), max(view.height(), 650), th)
    except Exception as e:  # noqa: BLE001
        QMessageBox.critical(parent, "Esportazione", str(e))
        return None
    return path


class PlotArea(QWidget):
    hovered = Signal(str)
    activeChanged = Signal(object)

    LAYOUT_NAMES = {"1": "Singolo", "2h": "Due affiancati", "2v": "Due sovrapposti", "3": "Tre", "4": "Quattro (2×2)"}

    def __init__(self, ws: Workspace, tsettings: TimeSettings, parent=None):
        super().__init__(parent)
        self.ws = ws
        self.ts = tsettings
        self.link_x = True
        self._syncing = False
        defaults = [("rect", "db"), ("smith", "db"), ("rect", "phase"), ("polar", "db")]
        self.panels = [PlotPanel(ws, tsettings, t, k, f"Grafico {i + 1}") for i, (t, k) in enumerate(defaults)]
        self.active: PlotPanel = self.panels[0]
        for p in self.panels:
            p.activated.connect(self._set_active)
            p.viewCreated.connect(self._wire_view)
        self._wire_views()
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(0, 0, 0, 0)
        self._container: Optional[QWidget] = None
        self.layout_name = "2h"
        self.set_layout("2h")
        self._set_active(self.panels[0])
        ws.changed.connect(self.refresh_all)
        ws.markers.changed.connect(self.update_markers)
        tsettings.changed.connect(self._time_changed)

    # ------------------------------------------------------------ layout
    def _count(self) -> int:
        return {"1": 1, "2h": 2, "2v": 2, "3": 3, "4": 4}[self.layout_name]

    def set_layout(self, name: str) -> None:
        self.layout_name = name
        for p in self.panels:
            p.setParent(None)
            p.hide()
        if self._container is not None:
            self.root.removeWidget(self._container)
            self._container.deleteLater()
        P = self.panels
        if name == "1":
            c = QWidget()
            QVBoxLayout(c).setContentsMargins(0, 0, 0, 0)
            c.layout().addWidget(P[0])
        elif name in ("2h", "2v"):
            c = QSplitter(Qt.Orientation.Horizontal if name == "2h" else Qt.Orientation.Vertical)
            c.addWidget(P[0])
            c.addWidget(P[1])
        elif name == "3":
            c = QSplitter(Qt.Orientation.Vertical)
            c.addWidget(P[0])
            row = QSplitter(Qt.Orientation.Horizontal)
            row.addWidget(P[1])
            row.addWidget(P[2])
            c.addWidget(row)
        else:
            c = QSplitter(Qt.Orientation.Vertical)
            for a, b in ((0, 1), (2, 3)):
                row = QSplitter(Qt.Orientation.Horizontal)
                row.addWidget(P[a])
                row.addWidget(P[b])
                c.addWidget(row)
        for p in P[:self._count()]:
            p.show()
        self.root.addWidget(c)
        self._container = c
        if isinstance(c, QSplitter):
            c.setChildrenCollapsible(False)
        self._wire_views()
        self.refresh_all()

    def visible_panels(self) -> list[PlotPanel]:
        return self.panels[:self._count()]

    def _wire_view(self, v: PlotView) -> None:
        if getattr(v, "_wired", False):
            return
        v.hovered.connect(self.hovered.emit)
        v.viewChanged.connect(lambda v=v: self._sync_from(v))
        v._wired = True

    def _wire_views(self) -> None:
        for p in self.panels:
            for v in p.views.values():
                self._wire_view(v)

    # ------------------------------------------------------------ azioni
    def _set_active(self, panel: PlotPanel) -> None:
        for p in self.panels:
            p.set_active(p is panel)
        self.active = panel
        self.activeChanged.emit(panel)

    def refresh_all(self) -> None:
        self._wire_views()
        for p in self.visible_panels():
            p.refresh()

    def update_markers(self) -> None:
        for p in self.visible_panels():
            p.update_markers()

    def _time_changed(self) -> None:
        reset = getattr(self.ts, "reset_view", False)
        self.ts.reset_view = False
        for p in self.visible_panels():
            if p.view_type() == "time":
                v = p.view()
                if reset:
                    v.x_manual = None
                v.refresh()

    def _sync_from(self, src: PlotView) -> None:
        if not self.link_x or self._syncing or not isinstance(src, RectPlot) or isinstance(src, TimePlot):
            return
        self._syncing = True
        try:
            for p in self.visible_panels():
                v = p.view()
                if v is src or not isinstance(v, RectPlot) or isinstance(v, TimePlot):
                    continue
                if v.x_manual != src.x_manual:
                    v.x_manual = src.x_manual
                    v._poly_cache.clear()
                    v._geom = None
                    v.update()
        finally:
            self._syncing = False

    def set_link_x(self, on: bool) -> None:
        self.link_x = on
        if on:
            self._sync_from(self.active.view())

    def autoscale_all(self) -> None:
        for p in self.visible_panels():
            p._auto()

    # ------------------------------------------------------------- stato
    def state(self) -> dict:
        return {"layout": self.layout_name, "link_x": self.link_x,
                "panels": [p.state() for p in self.panels],
                "splitter": self._container.sizes() if isinstance(self._container, QSplitter) else None}

    def restore(self, d: dict) -> None:
        self.link_x = d.get("link_x", True)
        self.set_layout(d.get("layout", "2h"))
        for p, st in zip(self.panels, d.get("panels", [])):
            p.restore(st)
        self._wire_views()
        self.refresh_all()
