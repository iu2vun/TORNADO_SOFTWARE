"""Test di fumo di Tornado Serial Sniffer:  pytest -q tests   (offscreen, senza strumenti collegati)"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _niente_dialoghi_modali(monkeypatch):
    """Un dialogo modale bloccherebbe il test; un «Salvare?» inatteso alla chiusura deve farlo fallire, non appenderlo."""
    from PySide6.QtWidgets import QDialog, QMessageBox
    monkeypatch.setattr(QDialog, "exec", lambda self: 0)

    def _no(*a, **k):
        raise AssertionError("dialogo di conferma inatteso: modificato() e' True su un documento vuoto")
    monkeypatch.setattr(QMessageBox, "question", staticmethod(_no))


@pytest.fixture
def qapp(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from PySide6.QtWidgets import QApplication
    from tornado_suite.core.theme import apply_theme
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    return app


def _finestra(qapp):
    from tornado_suite import standalone
    spec = standalone.load_spec("sniffer")
    try:
        w = spec.factory()
    except RuntimeError as e:
        if "solo su Windows" in str(e) and sys.platform != "win32":
            pytest.skip(str(e))
        raise
    return standalone.make_window(spec, w), w


def test_si_avvia_e_si_chiude(qapp):
    win, w = _finestra(qapp)
    win.show()
    qapp.processEvents()
    if hasattr(w, "diagnostica_extra"):
        assert isinstance(w.diagnostica_extra(), dict)
    assert win.close()
    qapp.processEvents()
