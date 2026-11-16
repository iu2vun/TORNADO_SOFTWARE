"""Test di flash_core (senza Qt, senza hardware e senza esptool installato): pytest tests/test_flash_core.py"""
import os
import sys
import textwrap
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from tornado_suite.apps.flash_core import (Cancelled, EsptoolBackend, FlashError, ImagePart, PortInfo,  # noqa: E402
                                           RunContext, WriteRequest, candidates, run_streaming, scan)
from tornado_suite.apps.flash_core import esptool_backend as eb  # noqa: E402

V4 = """esptool.py v4.7.0
Serial port COM5
Connecting....
Detecting chip type... ESP32-S3
Chip is ESP32-S3 (revision v0.1)
Features: WiFi, BLE, Embedded PSRAM 8MB (AP_3v3)
Crystal is 40MHz
MAC: 7c:df:a1:00:11:22
Detected flash size: 8MB
"""
V5 = """esptool v5.0.0
Connected to ESP32-D0WD-V3 on COM7:
Chip type:          ESP32-D0WD-V3 (revision v3.0)
Features:           Wi-Fi, BT, Dual Core + LP Core, 240MHz, Vref calibration in eFuse
Crystal frequency:  40MHz
MAC:                24:6f:28:aa:bb:cc
Flash size:         4MB
"""


def test_parse_chip_info_v4():
    i = eb.parse_chip_info(V4)
    assert i["chip"] == "ESP32-S3" and i["revision"] == "v0.1" and i["flash_size"] == "8MB"
    assert i["mac"] == "7c:df:a1:00:11:22" and i["crystal_mhz"] == "40"
    assert eb.family_of(i["chip"]) == "ESP32-S3"


def test_parse_chip_info_v5_classic_normalized():
    i = eb.parse_chip_info(V5)
    assert i["chip"] == "ESP32-D0WD-V3" and i["flash_size"] == "4MB" and i["revision"] == "v3.0"
    assert eb.family_of(i["chip"]) == "ESP32"


@pytest.mark.parametrize("chip,fam", [("ESP32-C3", "ESP32-C3"), ("ESP32-S2", "ESP32-S2"), ("ESP32-PICO-D4", "ESP32"),
                                      ("ESP8266EX", "ESP8266"), ("ESP32", "ESP32")])
def test_family_of(chip, fam):
    assert eb.family_of(chip) == fam


def test_parse_info_none():
    assert eb.parse_chip_info("A fatal error occurred: Failed to connect to ESP32") == {}


@pytest.mark.parametrize("line,val", [("Writing at 0x00010000... (12 %)", 12.0), ("Writing at 0x1000 [====>] 45.5% 100/200", 45.5),
                                      ("Wrote 8192 bytes (4000 compressed) at 0x0 in 0.5 seconds", None),
                                      ("Hash of data verified.", None), ("Connecting....", None), ("x 150 %", None)])
def test_parse_progress(line, val):
    assert eb.parse_progress(line) == val


def make(cmd=("esptool",)):
    return EsptoolBackend(command=list(cmd))


def test_build_argv_write_sorted_and_options(tmp_path):
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(b"1"), b.write_bytes(b"2")
    seen = {}
    be = make()

    def fake_run(argv, ctx, on_line=None, tail_lines=12):
        seen["argv"] = argv
        return []

    import tornado_suite.apps.flash_core.esptool_backend as mod
    orig = mod.run_streaming
    mod.run_streaming = fake_run
    try:
        be.write(WriteRequest("COM3", [ImagePart(0x10000, b), ImagePart(0x0, a)], baud=921600, erase_all=True, verify=False),
                 RunContext())
    finally:
        mod.run_streaming = orig
    assert seen["argv"] == ["esptool", "--chip", "auto", "--port", "COM3", "--baud", "921600", "write_flash",
                            "--erase-all", "--no-verify", "0x0", str(a), "0x10000", str(b)]


def test_write_validation(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"1")
    be = make()
    with pytest.raises(FlashError, match="Nessun file"):
        be.write(WriteRequest("COM3", []), RunContext())
    with pytest.raises(FlashError, match="non trovato"):
        be.write(WriteRequest("COM3", [ImagePart(0, tmp_path / "x.bin")]), RunContext())
    with pytest.raises(FlashError, match="stesso offset"):
        be.write(WriteRequest("COM3", [ImagePart(0, f), ImagePart(0, f)]), RunContext())


FAKE = textwrap.dedent('''
    import sys, time
    a = sys.argv[1:]
    op = next(x for x in a if x in ("flash_id", "write_flash", "read_flash", "erase_flash"))
    if op == "flash_id":
        if "FAIL" in a:
            print("A fatal error occurred: Failed to connect"); sys.exit(2)
        print("Chip is ESP32-S3 (revision v0.1)"); print("MAC: 7c:df:a1:00:11:22"); print("Detected flash size: 8MB")
    elif op == "write_flash":
        n = a.count("write_flash")
        for f in range(2):
            for p in (0, 50, 100):
                sys.stdout.write("Writing at 0x%08x... (%d %%)\\r" % (f * 4096, p)); sys.stdout.flush()
            print("\\nWrote 4096 bytes (100 compressed) at 0x%x in 0.1 seconds" % (f * 4096))
    elif op == "read_flash":
        open(a[-1], "wb").write(b"\\xff" * 16)
        print("Read 16 bytes (100 %)")
    elif op == "erase_flash":
        print("Chip erase completed successfully")
''')


@pytest.fixture
def fake(tmp_path):
    p = tmp_path / "fake_esptool.py"
    p.write_text(FAKE)
    return EsptoolBackend(command=[sys.executable, str(p)])


def test_probe_ok(fake):
    d = fake.probe("COM9", RunContext())
    assert d and d.family == "ESP32-S3" and d.backend == "esptool" and d.details["flash_size"] == "8MB"


def test_probe_fail_returns_none(tmp_path):
    p = tmp_path / "f.py"
    p.write_text("import sys; print('Failed to connect'); sys.exit(2)")
    assert EsptoolBackend(command=[sys.executable, str(p)]).probe("COM1", RunContext()) is None


def test_write_progress_monotonic_ends_100(fake, tmp_path):
    f1, f2 = tmp_path / "a.bin", tmp_path / "b.bin"
    f1.write_bytes(b"1"), f2.write_bytes(b"2")
    prog = []
    fake.write(WriteRequest("COM9", [ImagePart(0, f1), ImagePart(0x1000, f2)]), RunContext(progress=prog.append))
    assert prog == sorted(prog) and prog[-1] == 100 and 50 in prog


def test_read_backup_and_erase(fake, tmp_path):
    dest = tmp_path / "sub" / "bk.bin"
    fake.read("COM9", dest, RunContext())
    assert dest.read_bytes() == b"\xff" * 16
    fake.erase("COM9", RunContext())


def test_nonzero_exit_raises_with_tail():
    with pytest.raises(FlashError, match="(?s)codice 3.*boom"):
        run_streaming([sys.executable, "-c", "import sys; print('boom'); sys.exit(3)"], RunContext())


def test_missing_executable():
    with pytest.raises(FlashError, match="Impossibile avviare"):
        run_streaming(["/non/esiste/tool"], RunContext())


def test_cancel_kills_process_quickly():
    ctx = RunContext()
    threading.Timer(0.4, ctx.cancel.set).start()
    t0 = time.time()
    with pytest.raises(Cancelled):
        run_streaming([sys.executable, "-c", "import time; print('start', flush=True); time.sleep(30)"], ctx)
    assert time.time() - t0 < 5


def test_candidates_and_scan(fake):
    uno = PortInfo("COM3", 0x2341, 0x0043)
    s3 = PortInfo("COM4", 0x303A, 0x1001)
    ch340 = PortInfo("COM5", 0x1A86, 0x7523)
    other = PortInfo("COM6", 0x1234, 0x5678)
    assert candidates(uno) == ["avrdude"] and candidates(s3) == ["esptool"]
    assert candidates(ch340) == ["esptool", "avrdude"] and candidates(other) == []
    res = scan({"esptool": fake}, ports=[uno, s3, ch340, other])
    by = {p.device: (info, c) for p, info, c in res}
    assert by["COM3"][0] is None and by["COM3"][1] == []          # avrdude non registrato: niente probe
    assert by["COM4"][0].family == "ESP32-S3"
    assert by["COM5"][0].chip == "ESP32-S3"
    assert by["COM6"] == (None, [])
    assert scan({"esptool": fake}, ports=[s3], probe=False)[0][1] is None


# ---------------------------------------------------------------- avrdude
from tornado_suite.apps.flash_core import AvrdudeBackend, PROGRAMMERS  # noqa: E402
from tornado_suite.apps.flash_core import avrdude_backend as ab  # noqa: E402

FAKE_AVR = textwrap.dedent('''
    import sys
    a = sys.argv[1:]
    err = lambda s: (sys.stderr.write(s + "\\n"), sys.stderr.flush())
    if "FAILSYNC" in a:
        err("avrdude: stk500_getsync() attempt 10 of 10: not in sync: resp=0x00"); sys.exit(1)
    if "-n" in a:
        err("avrdude: AVR device initialized and ready to accept instructions")
        err("avrdude: Device signature = 0x1e9404 (probably m162)"); sys.exit(0)
    for i, x in enumerate(a):
        if x != "-U":
            continue
        mem, op, f = a[i + 1].split(":")[:3]
        if op == "w" and mem == "flash":
            for ph in ("Writing", "Reading"):
                sys.stderr.write("%s | ################ | 50%% 0.1s\\r" % ph)
                sys.stderr.write("%s | ################ | 100%% 0.2s\\n" % ph)
        elif op == "r" and mem == "flash":
            err("Reading | ################ | 100% 0.2s"); open(f, "wb").write(b"\\xff" * 8)
        elif op == "r":
            print({"lfuse": "0xe2", "hfuse": "0x99", "efuse": "0xfd", "lock": "0xff"}[mem])
''')


@pytest.fixture
def avr(tmp_path):
    p = tmp_path / "fake_avrdude.py"
    p.write_text(FAKE_AVR)
    return lambda prog="arduino", part="m328p": AvrdudeBackend(prog, part, command=[sys.executable, str(p)])


def test_programmer_argv(avr):
    uno = avr("arduino")
    assert uno.build_argv(["-U", "x"], "COM3")[2:] == ["-p", "m328p", "-c", "arduino", "-P", "COM3", "-b", "115200", "-U", "x"]
    isp = avr("arduinoisp", "m162")
    assert isp.build_argv([], "COM3", part="")[2:] == ["-p", "m162", "-c", "stk500v1", "-P", "COM3", "-b", "19200"]
    assert isp.build_argv([], "COM3", baud=9600)[-1] == "9600"
    assert avr("usbasp").build_argv([], "")[2:] == ["-p", "m328p", "-c", "usbasp"]
    assert avr("avrispmkii").build_argv([], "")[-2:] == ["-P", "usb"]
    with pytest.raises(FlashError, match="Chip non specificato"):
        avr("usbasp", "").build_argv([])
    with pytest.raises(FlashError, match="Porta seriale"):
        avr("arduino").build_argv([], "")
    with pytest.raises(ValueError):
        AvrdudeBackend("inesistente")
    assert set(PROGRAMMERS) == {"arduino", "arduinoisp", "usbasp", "avrispmkii"}


@pytest.mark.parametrize("name,fmt", [("a.hex", "i"), ("a.ELF", "e"), ("a.bin", "r"), ("a.xyz", "a")])
def test_fmt_for(name, fmt):
    assert ab._fmt_for(Path(name)) == fmt


def test_avr_probe_ok_and_fail(avr):
    d = avr("arduinoisp", "m162").probe("COM4", RunContext())
    assert d and d.chip == "ATmega162" and d.family == "AVR" and d.details["signature"] == "0x1e9404"
    assert ab.parse_signature("Device signature = 0x000000") == 0
    be = avr()
    be.build_argv = lambda *a, **k: [sys.executable, "-c", "import sys; sys.stderr.write('x'); sys.exit(1)"]
    assert be.probe("COM4", RunContext()) is None


def test_avr_write_args_and_progress(avr, tmp_path):
    hexf = tmp_path / "fw.hex"
    hexf.write_text(":00000001FF\n")
    uno = avr("arduino")
    argv = uno.build_argv(["-D", "-U", f"flash:w:{hexf}:i"], "COM3")
    assert "-D" in argv
    prog = []
    uno.write(WriteRequest("COM3", [ImagePart(0, hexf)]), RunContext(progress=prog.append))
    assert prog == sorted(prog) and prog[-1] == 100 and 25 in prog and 75 in prog
    prog2 = []
    avr("arduinoisp", "m162").write(WriteRequest("COM3", [ImagePart(0, hexf)], verify=False), RunContext(progress=prog2.append))
    assert prog2[-1] == 100


def test_avr_write_validation(avr, tmp_path):
    f = tmp_path / "a.hex"
    f.write_text("x")
    be = avr("arduino")
    with pytest.raises(FlashError, match="un solo file"):
        be.write(WriteRequest("COM3", [ImagePart(0, f), ImagePart(4, f)]), RunContext())
    with pytest.raises(FlashError, match="non trovato"):
        be.write(WriteRequest("COM3", [ImagePart(0, tmp_path / "no.hex")]), RunContext())
    with pytest.raises(FlashError, match="bootloader"):
        be.write(WriteRequest("COM3", [ImagePart(0, f)], erase_all=True), RunContext())
    with pytest.raises(FlashError, match="bootloader"):
        be.erase("COM3", RunContext())


def test_avr_sync_error_has_hint(avr, tmp_path):
    f = tmp_path / "a.hex"
    f.write_text("x")
    be = AvrdudeBackend("arduino", "m328p", command=[sys.executable, "-c", "import sys; sys.stderr.write('stk500_getsync(): not in sync: resp=0x00'); sys.exit(1)"])
    with pytest.raises(FlashError, match="arduinoisp"):
        be.write(WriteRequest("COM3", [ImagePart(0, f)]), RunContext())


def test_avr_read_backup(avr, tmp_path):
    dest = tmp_path / "d" / "bk.hex"
    avr("arduinoisp", "m162").read("COM3", dest, RunContext())
    assert dest.read_bytes() == b"\xff" * 8


def test_read_fuses_and_guards(avr):
    be = avr("arduinoisp", "m162")
    assert be.read_fuses("COM3", RunContext()) == {"lfuse": 0xE2, "hfuse": 0x99, "efuse": 0xFD, "lock": 0xFF}
    with pytest.raises(FlashError, match="SPIEN"):
        be.write_fuse("COM3", "hfuse", 0xB9, RunContext())         # bit 5 = 1 -> ISP disattivato
    with pytest.raises(FlashError, match="lock bit"):
        be.write_fuse("COM3", "lock", 0xFC, RunContext())
    with pytest.raises(FlashError, match="sconosciuto"):
        be.write_fuse("COM3", "xfuse", 1, RunContext())
    with pytest.raises(FlashError, match="intervallo"):
        be.write_fuse("COM3", "lfuse", 300, RunContext())
    be.write_fuse("COM3", "hfuse", 0x99, RunContext())              # valore sicuro: passa
    be.write_fuse("COM3", "lock", 0xFC, RunContext(), force=True)
    with pytest.raises(FlashError, match="bootloader"):
        avr("arduino").read_fuses("COM3", RunContext())


def test_esptool_default_baud_when_zero(tmp_path):
    import tornado_suite.apps.flash_core.esptool_backend as mod
    seen = []
    orig = mod.run_streaming
    mod.run_streaming = lambda argv, ctx, on_line=None, tail_lines=12: seen.append(argv) or []
    try:
        be = make()
        dest = tmp_path / "x.bin"
        dest.write_bytes(b"1")
        be.read("COM3", dest, RunContext())
        be.erase("COM3", RunContext())
    finally:
        mod.run_streaming = orig
    assert all(a[a.index("--baud") + 1] == "460800" for a in seen)


def test_default_backends_ids():
    from tornado_suite.apps.flash_core import default_backends
    assert set(default_backends()) == {"esptool", "avrdude"}


# ---------------------------------------------------------------- CLI
from tornado_suite.apps.flash_core import __main__ as cli  # noqa: E402


class FakeBackend:
    id = "esptool"

    def __init__(self):
        self.calls = []

    def probe(self, port, ctx):
        from tornado_suite.apps.flash_core import DeviceInfo
        self.calls.append(("probe", port))
        return DeviceInfo(port, "esptool", "ESP32-S3", "ESP32-S3", {"flash_size": "8MB"})

    def write(self, req, ctx):
        self.calls.append(("write", req.port, [(p.offset, str(p.path)) for p in req.parts], req.erase_all, req.verify))

    def read(self, port, dest, ctx, baud=0, chip="auto"):
        self.calls.append(("read", port, str(dest)))

    def erase(self, port, ctx, baud=0, chip="auto"):
        self.calls.append(("erase", port))


def run_cli(args, backend=None, answer="NO"):
    be = backend or FakeBackend()
    lines = []
    rc = cli.main(args, backends={"esptool": be}, out=lines.append, ask=lambda m: answer)
    return rc, lines, be


def test_cli_parse_target(tmp_path):
    assert cli.parse_target("fw.bin@0x10000") == ImagePart(0x10000, Path("fw.bin"))
    assert cli.parse_target("fw.hex") == ImagePart(0, Path("fw.hex"))
    real = tmp_path / "a@b.bin"
    real.write_bytes(b"1")
    assert cli.parse_target(str(real)).path == real
    with pytest.raises(FlashError, match="Offset"):
        cli.parse_target("fw.bin@zz")


def test_cli_probe_and_write_routing(tmp_path):
    rc, lines, be = run_cli(["probe", "-p", "COM5", "-b", "esptool"])
    assert rc == 0 and be.calls == [("probe", "COM5")] and "ESP32-S3" in lines[-1]
    rc, lines, be = run_cli(["write", "-p", "COM5", "-b", "esptool", "a.bin@0x0", "b.bin@0x10000", "--no-verify"])
    assert rc == 0 and be.calls == [("write", "COM5", [(0, "a.bin"), (0x10000, "b.bin")], False, False)]


def test_cli_esptool_requires_offset_and_erase_confirmation():
    rc, lines, be = run_cli(["write", "-p", "COM5", "-b", "esptool", "a.bin"])
    assert rc == 1 and "offset" in lines[-1] and be.calls == []
    rc, lines, be = run_cli(["write", "-p", "COM5", "-b", "esptool", "a.bin@0", "--erase-all"], answer="no")
    assert rc == 130 and be.calls == []
    rc, lines, be = run_cli(["erase", "-p", "COM5", "-b", "esptool"], answer="si")
    assert rc == 0 and be.calls == [("erase", "COM5")]
    rc, lines, be = run_cli(["erase", "-p", "COM5", "-b", "esptool", "--yes"])
    assert rc == 0


def test_cli_read_and_port_required(tmp_path):
    rc, lines, be = run_cli(["read", "-p", "COM5", "-b", "esptool", str(tmp_path / "bk.bin")])
    assert rc == 0 and be.calls[0][0] == "read"
    with pytest.raises(SystemExit):
        run_cli(["probe", "-b", "esptool"])


def test_cli_doctor_runs_without_tools(monkeypatch):
    monkeypatch.setattr(cli, "esp_find", lambda: (_ for _ in ()).throw(FlashError("esptool non trovato")))
    monkeypatch.setattr(cli, "avr_find", lambda: (_ for _ in ()).throw(FlashError("avrdude non trovato")))
    monkeypatch.setattr(cli, "list_ports", lambda: [PortInfo("COM3", 0x2341, 0x0043, "Arduino Uno")])
    lines = []
    cli.doctor(lines.append)
    text = "\n".join(lines)
    assert "Python" in text and "NON utilizzabile" in text and "COM3" in text and "2341:0043" in text and "flash_core.log" in text


def test_cli_fuses_requires_avrdude():
    rc, lines, be = run_cli(["fuses", "-p", "COM3", "-b", "esptool"])
    assert rc == 1 and "avrdude" in lines[-1]


# ---------------------------------------------------------------- ricerca esptool
@pytest.fixture
def finder(monkeypatch, tmp_path):
    monkeypatch.setattr(eb, "_cache", None)
    monkeypatch.setattr(eb, "_PIO", tmp_path / ".platformio")
    monkeypatch.setattr(eb.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(eb.shutil, "which", lambda name: None)
    monkeypatch.delattr(sys, "frozen", raising=False)
    return tmp_path / ".platformio"


def _penv(root):
    py = root / "penv" / "Scripts" / "python.exe"
    py.parent.mkdir(parents=True)
    py.write_text("")
    return py


def test_find_module_importable(finder, monkeypatch):
    monkeypatch.setattr(eb.importlib.util, "find_spec", lambda name: object())
    assert eb.find_command() == [sys.executable, "-m", "esptool"]


def test_find_skips_module_when_frozen_then_path(finder, monkeypatch):
    monkeypatch.setattr(eb.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(eb.shutil, "which", lambda name: "C:/tools/esptool.exe" if name == "esptool" else None)
    assert eb.find_command() == ["C:/tools/esptool.exe"]


def test_find_platformio_penv_module_and_cache(finder, monkeypatch):
    py = _penv(finder)
    calls = []
    monkeypatch.setattr(eb, "_runs", lambda cmd: calls.append(list(cmd)) or True)
    assert eb.find_command() == [str(py), "-m", "esptool"]
    assert eb.find_command() == [str(py), "-m", "esptool"] and len(calls) == 1     # seconda chiamata: cache


def test_find_platformio_script_when_module_missing(finder, monkeypatch):
    py = _penv(finder)
    script = finder / "packages" / "tool-esptoolpy" / "esptool.py"
    script.parent.mkdir(parents=True)
    script.write_text("")
    monkeypatch.setattr(eb, "_runs", lambda cmd: str(script) in cmd)
    assert eb.find_command() == [str(py), str(script)]


def test_find_nothing_gives_clear_error(finder, monkeypatch):
    _penv(finder)
    monkeypatch.setattr(eb, "_runs", lambda cmd: False)
    with pytest.raises(FlashError, match="pip install esptool.*PlatformIO"):
        eb.find_command()
    assert eb._cache is None


def test_runs_checks_version_exit_code():
    assert eb._runs(["/non/esiste/python"]) is False
    ok = "import sys; sys.exit(0 if sys.argv[1:] == ['version'] else 3)"
    assert eb._runs([sys.executable, "-c", ok]) is True            # '-c' ... 'version' -> argv[1] == 'version'
    assert eb._runs([sys.executable, "-c", "import sys; sys.exit(1)"]) is False
