"""Test del lettore/visualizzatore Altium: dati sintetici (sempre) + campioni reali se presenti (ALTIUM_SAMPLES=cartella)."""
import os
import struct

import pytest

from tornado_suite.apps.altium_core import PcbDoc, Track, Pad, load_pcbdoc, load_schdoc, layer_name
from tornado_suite.apps.altium_core import sch as schmod

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SAMPLES = os.environ.get("ALTIUM_SAMPLES", "/tmp/altref")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _ascii(tmp_path):
    txt = (b"|HEADER=Protel for Windows - Schematic Capture Ascii File Version 5.0|WEIGHT=4\n"
           b"|RECORD=31|SHEETSTYLE=0\n"
           b"|RECORD=1|LIBREFERENCE=R|LOCATION.X=100|LOCATION.Y=100|CURRENTPARTID=1\n"
           b"|RECORD=14|OWNERINDEX=1|OWNERPARTID=1|LOCATION.X=100|LOCATION.Y=100|CORNER.X=120|CORNER.Y=110\n"
           b"|RECORD=2|OWNERINDEX=1|OWNERPARTID=1|LOCATION.X=100|LOCATION.Y=105|PINLENGTH=10|PINCONGLOMERATE=24|NAME=A|DESIGNATOR=1\n"
           b"|RECORD=2|OWNERINDEX=1|OWNERPARTID=2|LOCATION.X=0|LOCATION.Y=5|PINLENGTH=10|NAME=B|DESIGNATOR=2\n"
           b"|RECORD=27|LOCATIONCOUNT=2|X1=50|Y1=60|X2=80|Y2=60\n"
           b"|RECORD=25|LOCATION.X=50|LOCATION.Y=60|TEXT=NET1\n")
    p = tmp_path / "t.SchDoc"
    p.write_bytes(txt)
    return str(p)


def test_schdoc_ascii(tmp_path):
    d = load_schdoc(_ascii(tmp_path))
    kinds = [p.kind for p in d.prims]
    assert d.sheet == (11500, 7600)
    assert kinds.count("pin") == 1          # il pin di un'altra parte (OWNERPARTID=2) è nascosto
    assert "rect" in kinds and "wire" in kinds and "netlabel" in kinds
    pin = next(p for p in d.prims if p.kind == "pin")
    assert pin.pts == [(1000.0, 1050.0), (1100.0, 1050.0)]     # 10 mil/unità, rotazione 0 = verso destra
    wire = next(p for p in d.prims if p.kind == "wire")
    assert wire.pts == [(500.0, 600.0), (800.0, 600.0)]


def test_props_binary_ole_records(monkeypatch):
    raw = b"|RECORD=29|LOCATION.X=10|LOCATION.Y=20|LOCATION.X_FRAC=50000\x00"
    assert schmod._parse_props(raw)["LOCATION.X_FRAC"] == "50000"
    d = schmod._parse_props(raw)
    x, y = schmod._loc(d)
    assert abs(x - 105.0) < 1e-6 and y == 200.0


def test_layer_names():
    assert layer_name(1) == "Top" and layer_name(32) == "Bottom" and layer_name(57) == "Mech1"


def test_canvas_smoke(qapp):
    from tornado_suite.apps.visualizer.canvas import Canvas
    d = PcbDoc(tracks=[Track(1, 0, 0, 1000, 0, 10)], pads=[Pad("1", 0, 0, 1, 60, 60, 1, 20, 0)],
               outline=[(0, 0), (1000, 0), (1000, 500), (0, 500)])
    c = Canvas(); c.resize(300, 200); c.set_pcb(d)
    assert 1 in c.visible
    assert not c.grab().isNull()
    c.mirror = True
    assert not c.grab().isNull()


def test_modulo_registrato():
    from tornado_suite.standalone import tool_ids
    assert tool_ids() == ["visualizer"]


@pytest.mark.skipif(not os.path.exists(os.path.join(SAMPLES, "HiFive1.B01.PcbDoc")), reason="campioni Altium assenti")
def test_pcbdoc_reale():
    d = load_pcbdoc(os.path.join(SAMPLES, "HiFive1.B01.PcbDoc"))
    assert not d.warnings
    assert len(d.tracks) > 2000 and len(d.pads) > 400 and len(d.vias) > 200
    x0, y0, x1, y1 = d.bbox()
    assert 2600 < x1 - x0 < 2800 and 2000 < y1 - y0 < 2200      # ~68.6 x 53.3 mm


@pytest.mark.skipif(not os.path.exists(os.path.join(SAMPLES, "it6251_core.SchDoc")), reason="campioni Altium assenti")
def test_schdoc_reale():
    d = load_schdoc(os.path.join(SAMPLES, "it6251_core.SchDoc"))
    assert not d.warnings
    assert sum(p.kind == "pin" for p in d.prims) > 200 and sum(p.kind == "wire" for p in d.prims) > 150


def test_net_highlight(qapp):
    from PySide6.QtCore import QPointF
    from tornado_suite.apps.visualizer.canvas import Canvas
    d = PcbDoc(tracks=[Track(1, 0, 0, 1000, 0, 10, net=0), Track(1, 0, 500, 1000, 500, 10, net=1)],
               nets=["GND", "VCC"], outline=[(0, 0), (1000, 500)])
    c = Canvas(); c.resize(400, 300); c.set_pcb(d)
    got = []
    c.net_picked.connect(got.append)
    c.pick_net(QPointF(500, 505))
    assert got == ["Rete: VCC"] and c.hl_net == 1
    c.pick_net(QPointF(500, 250))
    assert c.hl_net == -1
    assert not c.grab().isNull()


def test_misura_ed_export(qapp, tmp_path):
    from PySide6.QtCore import QPointF
    from tornado_suite.apps.visualizer.canvas import Canvas
    d = PcbDoc(tracks=[Track(1, 0, 0, 1000, 0, 10)], outline=[(0, 0), (1000, 500)])
    c = Canvas(); c.resize(400, 300); c.set_pcb(d)
    c.meas = [(0.0, 0.0), (1000.0, 0.0)]
    assert c.meas_text() == "25.400 mm"
    c.unit = "mil"
    assert c.meas_text() == "1000.000 mil"
    assert c.export_png(str(tmp_path / "a.png")) and (tmp_path / "a.png").stat().st_size > 500
    assert c.export_pdf(str(tmp_path / "a.pdf")) and (tmp_path / "a.pdf").read_bytes()[:4] == b"%PDF"


def test_autofit_al_ridimensionamento(qapp):
    from tornado_suite.apps.visualizer.canvas import Canvas
    d = PcbDoc(tracks=[Track(1, 0, 0, 1000, 0, 10)], outline=[(0, 0), (1000, 500)])
    c = Canvas(); c.set_pcb(d); c.resize(300, 200); c.show()
    s1 = c.scale
    c.resize(900, 600)
    assert c.scale > s1 * 2                  # finché l'utente non zooma, la vista segue la finestra
