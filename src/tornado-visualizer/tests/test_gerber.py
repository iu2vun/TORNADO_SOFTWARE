"""Test del lettore/visualizzatore Gerber + Excellon (sintetici sempre; campioni reali se in GERBER_SAMPLES)."""
import os

import pytest

from tornado_suite.apps.gerber_core import load_excellon, load_gerber, guess_role

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SAMPLES = os.environ.get("GERBER_SAMPLES", "/tmp/gbr")


def _w(tmp_path, name, txt):
    p = tmp_path / name
    p.write_text(txt)
    return str(p)


def test_gerber_tratti_flash_regioni(tmp_path):
    g = ("%FSLAX24Y24*%\n%MOMM*%\n%ADD10C,0.5*%\n%ADD11R,2X1*%\nD10*\nX0Y0D02*\nX100000Y0D01*\nD11*\nX50000Y50000D03*\n"
         "G36*\nX0Y0D02*\nX10000Y0D01*\nX10000Y10000D01*\nX0Y10000D01*\nG37*\nM02*\n")
    L = load_gerber(_w(tmp_path, "a.gbr", g))
    kinds = [o[0] for o in L.ops]
    assert kinds == ["s", "f", "r"]
    assert L.ops[0][3] == pytest.approx(10.0) and L.ops[0][5] == pytest.approx(0.5)
    assert L.apertures[11].kind == "R" and L.apertures[11].p == (2.0, 1.0)
    assert L.bbox()[2] == pytest.approx(10.25, abs=0.3)


def test_gerber_arco_e_pollici(tmp_path):
    g = "%FSLAX23Y23*%\n%MOIN*%\n%ADD10C,0.01*%\nD10*\nG75*\nG03*\nX1000Y0I0J500D02*\nX0Y0I-500J0D01*\nM02*\n"
    # arco antiorario da (1,0) a (0,0) con centro (0.5,0)... nessuna eccezione e unità convertite
    L = load_gerber(_w(tmp_path, "b.gbr", g))
    assert L.ops and L.ops[0][5] == pytest.approx(0.254)


def test_macro_e_step_repeat(tmp_path):
    g = ("%FSLAX24Y24*%\n%MOMM*%\n%AMBOX*\n21,1,2.0,1.0,0,0,0*%\n%ADD10BOX*%\n%SRX3Y2I10.0J5.0*%\nD10*\nX0Y0D03*\n%SR*%\nM02*\n")
    L = load_gerber(_w(tmp_path, "c.gbr", g))
    assert L.apertures[10].kind == "M" and len(L.apertures[10].polys) == 1
    assert sum(o[0] == "f" for o in L.ops) == 6
    xs = sorted({o[1] for o in L.ops}); ys = sorted({o[2] for o in L.ops})
    assert xs == [0.0, 10.0, 20.0] and ys == [0.0, 5.0]


def test_excellon_metrico_e_pollici(tmp_path):
    m = "M48\nMETRIC,TZ\nT1C0.8\n%\nT1\nX10.5Y-2.0\nM30\n"
    L = load_excellon(_w(tmp_path, "m.drl", m))
    assert L.holes == [(10.5, -2.0, 0.8)]
    i = "M48\nINCH,LZ\nT1C0.04\n%\nT1\nX010000Y005000\nM30\n"
    L = load_excellon(_w(tmp_path, "i.drl", i))
    assert L.holes[0][0] == pytest.approx(25.4) and L.holes[0][1] == pytest.approx(12.7)
    assert L.holes[0][2] == pytest.approx(1.016)


def test_ruoli():
    assert guess_role("board-F_Cu.gbr") == "top" and guess_role("x.gbl") == "bottom"
    assert guess_role("x.gbr", "G04 #@! TF.FileFunction,Legend,Top*") == "silk_t"
    assert guess_role("p.drl") == "drill"


def test_viewer_smoke(tmp_path):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from tornado_suite.apps.visualizer.mainwindow import MainWindow
    g = ("%FSLAX24Y24*%\n%MOMM*%\n%ADD10C,0.5*%\nD10*\nX0Y0D02*\nX100000Y0D01*\nX50000Y50000D03*\nM02*\n")
    p = _w(tmp_path, "t-F_Cu.gbr", g)
    w = MainWindow(); w.open_paths([p]); w.resize(500, 400)
    assert len(w.canvas.layers) == 1 and not w.canvas.grab().isNull()
    assert w.canvas.export_png(str(tmp_path / "o.png")) and w.canvas.export_pdf(str(tmp_path / "o.pdf"))


@pytest.mark.skipif(not os.path.exists(os.path.join(SAMPLES, "ZoneFill-4.0.7-F_Cu.gbr")), reason="campioni assenti")
def test_gerber_reale():
    L = load_gerber(os.path.join(SAMPLES, "ZoneFill-4.0.7-F_Cu.gbr"))
    assert not L.warnings and sum(o[0] == "s" for o in L.ops) > 400 and L.role == "top"


def test_pollici_decimali_e_poligono(tmp_path):
    g = "%FSLAX24Y24*%\n%MOIN*%\n%ADD10C,0.1*%\n%ADD11P,0.2X6X15*%\nD10*\nX0.0Y0.0D02*\nX1.0Y0.0D01*\nD11*\nX0Y0D03*\nM02*\n"
    L = load_gerber(_w(tmp_path, "d.gbr", g))
    assert L.ops[0][3] == pytest.approx(25.4)                 # coordinata decimale in pollici -> mm
    p = L.apertures[11]
    assert p.p[0] == pytest.approx(5.08) and p.p[1] == 6 and p.p[2] == 15   # n. vertici e rotazione non scalati


def test_regione_senza_spostamento_iniziale(tmp_path):
    g = "%FSLAX24Y24*%\n%MOMM*%\nX10000Y10000D02*\nG36*\nX20000Y10000D01*\nX20000Y20000D01*\nG37*\nM02*\n"
    L = load_gerber(_w(tmp_path, "e.gbr", g))
    assert L.ops[0][0] == "r" and L.ops[0][1][0] == (1.0, 1.0)
