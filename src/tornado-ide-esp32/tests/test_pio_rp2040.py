"""Test RP2040/STM32 e UF2 dell'IDE (senza Qt e senza PlatformIO)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from tornado_suite.apps.pio_ide import pio_core as pc  # noqa: E402


@pytest.mark.parametrize("plat,fam", [
    ("espressif32", "esp32"), ("espressif32@6.9.0", "esp32"), ("espressif8266", "esp8266"), ("atmelavr", "avr"), ("atmelsam", "sam"),
    ("raspberrypi", "rp2040"), ("raspberrypi@1.13.0", "rp2040"),
    ("https://github.com/maxgerhardt/platform-raspberrypi.git", "rp2040"),
    ("https://github.com/maxgerhardt/platform-raspberrypi.git#develop", "rp2040"),
    ("ststm32", "stm32"), ("", "altro"), ("native", "altro"),
])
def test_family(plat, fam):
    assert pc.family(plat) == fam


def test_default_monitor_speed():
    assert [pc.default_monitor_speed(p) for p in ("espressif32", "raspberrypi", "ststm32", "atmelavr", "atmelsam", "")] == [115200, 115200, 115200, 9600, 9600, 9600]


def test_presets_unique_and_known_families():
    ids = [p.id for p in pc.PRESETS]
    assert len(ids) == len(set(ids))
    assert all(pc.family(p.platform) != "altro" for p in pc.PRESETS)
    assert {"waveshare_rp2040_zero", "pico", "nanorp2040connect"} <= set(ids)


def test_create_rp2040_zero_project(tmp_path):
    zero = next(p for p in pc.PRESETS if p.id == "waveshare_rp2040_zero")
    root = pc.create_project(str(tmp_path), "rp", zero)
    e = pc.read_project(root).envs[0]
    assert (e.board, e.platform, e.framework) == ("waveshare_rp2040_zero", pc.RP2040_FORK, "arduino")
    assert pc.family(e.platform) == "rp2040"
    ini = (tmp_path / "rp" / "platformio.ini").read_text()
    assert "board_build.core = earlephilhower" in ini and "board_build.filesystem_size = 0.5m" in ini


def test_nano_rp2040_connect_official_core(tmp_path):
    nano = next(p for p in pc.PRESETS if p.id == "nanorp2040connect")
    e = pc.read_project(pc.create_project(str(tmp_path), "n", nano)).envs[0]
    assert e.platform == "raspberrypi" and "earlephilhower" not in (tmp_path / "n" / "platformio.ini").read_text()


def test_uf2_info_and_drives(tmp_path):
    ok, no, vuoto = tmp_path / "RPI-RP2", tmp_path / "ALTRO", tmp_path / "VUOTO"
    for d in (ok, no, vuoto):
        d.mkdir()
    (ok / "INFO_UF2.TXT").write_text("UF2 Bootloader v3.0\nModel: Raspberry Pi RP2\nBoard-ID: RPI-RP2\n")
    (no / "INFO_UF2.TXT").write_text("UF2 Bootloader\nBoard-ID: SAMD21G18A-Xplained\n")
    assert pc.find_uf2_drives([str(ok), str(no), str(vuoto), str(tmp_path / "inesistente")]) == [str(ok)]
    assert pc.is_rp_uf2_info("Board-ID: RP2350") and not pc.is_rp_uf2_info("Board-ID: XYZ")


def test_find_uf2_file(tmp_path):
    d = tmp_path / ".pio" / "build" / "pico"
    d.mkdir(parents=True)
    assert pc.find_uf2_file(str(tmp_path), "pico") is None
    (d / "altro.uf2").write_bytes(b"x")
    assert pc.find_uf2_file(str(tmp_path), "pico").endswith("altro.uf2")
    (d / "firmware.uf2").write_bytes(b"y")
    assert pc.find_uf2_file(str(tmp_path), "pico").endswith("firmware.uf2")


def test_rp_port():
    from types import SimpleNamespace as P
    a, b, c = P(name="COM3", vid=0x2E8A), P(name="COM4", vid=0x303A), P(name="COM5", vid=0x2E8A)
    assert pc.rp_port([a, b]) == "COM3"
    assert pc.rp_port([a, b, c]) == ""                 # ambigua senza scelta
    assert pc.rp_port([a, b, c], "COM5") == "COM5"
    assert pc.rp_port([a, b], "COM4") == ""            # la porta scelta non è un RP2040
    assert pc.rp_port([b]) == ""
