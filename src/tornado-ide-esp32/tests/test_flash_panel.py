"""Test del pannello Flash dell'IDE (offscreen, con backend finti): QT_QPA_PLATFORM=offscreen pytest tests/test_flash_panel.py"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from tornado_suite.apps.flash_core import DeviceInfo, FlashError  # noqa: E402
from tornado_suite.apps.pio_ide import flash_panel as fp  # noqa: E402

YES, NO = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeSettings(dict):
    def value(self, k, default=None, type=None):  # noqa: A002
        return self.get(k, default)

    def setValue(self, k, v):  # noqa: N802
        self[k] = v


class FakeBe:
    def __init__(self):
        self.calls = []
        self.fail = None
        self.block = False

    def probe(self, port, ctx):
        self.calls.append(("probe", port))
        if self.block:
            ctx.cancel.wait(5)
            ctx.check_cancel()
        return DeviceInfo(port, "esptool", "ESP32-S3", "ESP32-S3", {"flash_size": "8MB", "part": "m162"})

    def write(self, req, ctx):
        self.calls.append(("write", req.port, [(p.offset, p.path.name) for p in req.parts], req.erase_all, req.verify, req.chip))
        if self.fail:
            raise self.fail
        ctx.log("scrivo")
        ctx.progress(100)

    def read(self, port, dest, ctx, baud=0, chip="auto"):
        self.calls.append(("read", port, str(dest), chip))

    def erase(self, port, ctx, baud=0, chip="auto"):
        self.calls.append(("erase", port, chip))

    def read_fuses(self, port, ctx, part=""):
        self.calls.append(("read_fuses", port, part))
        return {"lfuse": 0xE2, "hfuse": 0x99, "efuse": 0xFD, "lock": 0xFF}

    def write_fuse(self, port, name, value, ctx, part="", force=False):
        self.calls.append(("write_fuse", name, value, force))


@pytest.fixture
def env(qapp, monkeypatch):
    be = FakeBe()
    hooks = {"before": 0, "after": 0, "port": "COM5", "busy": False, "factory": []}
    msgs = {"info": [], "warn": []}

    def factory(kind, programmer, part):
        hooks["factory"].append((kind, programmer, part))
        return be

    monkeypatch.setattr(fp.QMessageBox, "information", staticmethod(lambda *a, **k: msgs["info"].append(a[2])))
    monkeypatch.setattr(fp.QMessageBox, "warning", staticmethod(lambda *a, **k: msgs["warn"].append(a[2])))
    monkeypatch.setattr(fp.QMessageBox, "question", staticmethod(lambda *a, **k: YES))
    panel = fp.FlashPanel(FakeSettings(), lambda: hooks["port"], before=lambda: hooks.__setitem__("before", hooks["before"] + 1),
                          after=lambda: hooks.__setitem__("after", hooks["after"] + 1), busy=lambda: hooks["busy"],
                          backend_factory=factory)
    return panel, be, hooks, msgs


def wait(panel, timeout=5.0):
    t0 = time.time()
    while panel._job is not None and time.time() - t0 < timeout:
        QApplication.processEvents()
        time.sleep(0.005)
    QApplication.processEvents()
    assert panel._job is None, "operazione non terminata"


def set_target(panel, tid):
    panel.cmb_target.setCurrentIndex(panel.cmb_target.findData(tid))


def test_target_changes_visibility(env):
    panel, *_ = env
    set_target(panel, "esptool")
    assert panel.fuse_box.isHidden() and panel.cmb_part.isHidden()
    set_target(panel, "avr:arduinoisp")
    assert not panel.fuse_box.isHidden() and not panel.cmb_part.isHidden()
    set_target(panel, "avr:arduino")
    assert panel.fuse_box.isHidden() and not panel.cmb_part.isHidden()


def test_probe_updates_label_hooks_and_busy(env):
    panel, be, hooks, _ = env
    set_target(panel, "esptool")
    panel.probe()
    assert not panel.btn_probe.isEnabled() and panel.btn_cancel.isEnabled()
    wait(panel)
    assert be.calls == [("probe", "COM5")] and "ESP32-S3" in panel.lbl_info.text()
    assert hooks["before"] == 1 and hooks["after"] == 1 and panel.btn_probe.isEnabled() and not panel.btn_cancel.isEnabled()
    assert panel.cmb_part.currentText() == "m162"          # il chip AVR riconosciuto precompila il campo
    assert hooks["factory"][0][0] == "esptool"


def test_no_port_blocks_without_job(env):
    panel, be, hooks, msgs = env
    set_target(panel, "esptool")
    hooks["port"] = ""
    panel.probe()
    assert panel._job is None and be.calls == [] and "porta" in msgs["info"][0].lower()
    set_target(panel, "avr:usbasp")                         # USBasp non usa la porta seriale
    panel.cmb_part.setCurrentText("m162")
    panel.probe()
    wait(panel)
    assert be.calls and hooks["factory"][-1] == ("avrdude", "usbasp", "m162")


def test_write_flow(env, tmp_path):
    panel, be, hooks, msgs = env
    set_target(panel, "esptool")
    panel.write()
    assert be.calls == [] and "almeno un file" in msgs["info"][0]
    a, b = tmp_path / "boot.bin", tmp_path / "app.bin"
    panel._put(0x10000, str(b))
    panel._put(0x0, str(a))
    panel.chk_erase.setChecked(True)
    panel.chk_verify.setChecked(False)
    panel.write()
    wait(panel)
    assert be.calls == [("write", "COM5", [(0x10000, "app.bin"), (0x0, "boot.bin")], True, False, "auto")]
    assert panel.bar.value() == 100 and "scrivo" in panel.logw.toPlainText() and "Completato" in panel.logw.toPlainText()


def test_write_avr_passes_part(env, tmp_path):
    panel, be, hooks, _ = env
    set_target(panel, "avr:arduino")
    panel.cmb_part.setCurrentText("m328p")
    panel._put(0, str(tmp_path / "fw.hex"))
    panel.write()
    wait(panel)
    assert be.calls[0][-1] == "m328p" and hooks["factory"][-1] == ("avrdude", "arduino", "m328p")


def test_failure_is_reported_not_raised(env, tmp_path):
    panel, be, hooks, msgs = env
    set_target(panel, "esptool")
    be.fail = FlashError("Failed to connect")
    panel._put(0, str(tmp_path / "a.bin"))
    panel.write()
    wait(panel)
    assert msgs["warn"] == ["Failed to connect"] and "✘" in panel.logw.toPlainText() and hooks["after"] == 1


def test_confirmations_can_abort(env, monkeypatch, tmp_path):
    panel, be, *_ = env
    set_target(panel, "esptool")
    monkeypatch.setattr(fp.QMessageBox, "question", staticmethod(lambda *a, **k: NO))
    panel.erase()
    panel._put(0, str(tmp_path / "a.bin"))
    panel.chk_erase.setChecked(True)
    panel.write()
    assert panel._job is None and be.calls == []
    monkeypatch.setattr(fp.QMessageBox, "question", staticmethod(lambda *a, **k: YES))
    panel.erase()
    wait(panel)
    assert be.calls == [("erase", "COM5", "auto")]


def test_busy_ide_runner_blocks(env):
    panel, be, hooks, msgs = env
    set_target(panel, "esptool")
    hooks["busy"] = True
    panel.probe()
    assert panel._job is None and be.calls == [] and hooks["before"] == 0 and "compilazione" in msgs["info"][-1].lower()


def test_cancel_stops_job_without_error_dialog(env):
    panel, be, hooks, msgs = env
    set_target(panel, "esptool")
    be.block = True
    panel.probe()
    time.sleep(0.1)
    panel.cancel()
    wait(panel)
    assert msgs["warn"] == [] and "Annullato" in panel.logw.toPlainText() and hooks["after"] == 1


def test_fuses_read_and_write_only_changed(env):
    panel, be, *_ = env
    set_target(panel, "avr:arduinoisp")
    panel.cmb_part.setCurrentText("m162")
    panel.write_fuses()                                     # senza lettura preventiva: rifiutato
    assert be.calls == []
    panel.read_fuses()
    wait(panel)
    assert panel.fuse_edits["hfuse"].text() == "0x99" and panel._fuse_ref["lfuse"] == 0xE2
    panel.fuse_edits["hfuse"].setText("0x89")
    panel.chk_force.setChecked(True)
    be.calls.clear()
    panel.write_fuses()
    wait(panel)
    assert ("write_fuse", "hfuse", 0x89, True) in be.calls and sum(1 for c in be.calls if c[0] == "write_fuse") == 1
    assert be.calls[-1][0] == "read_fuses"


def test_fuses_unavailable_on_esptool_and_bad_hex(env, monkeypatch):
    panel, be, hooks, msgs = env
    set_target(panel, "esptool")
    be2 = type("NoFuse", (), {})()
    panel._factory = lambda *a: be2
    panel.read_fuses()
    assert "ISP" in msgs["info"][-1]
    panel._factory = lambda *a: be
    set_target(panel, "avr:arduinoisp")
    panel._fuse_ref = {"hfuse": 0x99}
    panel.fuse_edits["hfuse"].setText("zz")
    panel.write_fuses()
    assert msgs["warn"] and "non valido" in msgs["warn"][-1]


def test_shutdown_cancels_running_job(env):
    panel, be, *_ = env
    set_target(panel, "esptool")
    be.block = True
    panel.probe()
    time.sleep(0.1)
    panel.shutdown()
    wait(panel)
    assert panel._job is None
