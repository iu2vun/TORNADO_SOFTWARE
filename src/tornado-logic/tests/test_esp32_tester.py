"""Il C++ del tester ESP32 (esp32_tester/src/scenario.cpp) compilato sul PC: le forme d'onda generate
vengono campionate a 24 MS/s e decodificate da logic_core, come farebbe l'analizzatore reale."""
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from logic_core import (Capture, DecoderConfig, I2cCfg, SerialCfg, SpiCfg, UartCfg, decode_uart, run_decoders,
                        run_serial)
from logic_core import sim_signals as ss

ROOT = Path(__file__).resolve().parent.parent / "esp32_tester"
FS = 24e6
REQ = bytes.fromhex("010300000002C40B")

pytestmark = pytest.mark.skipif(shutil.which("g++") is None, reason="g++ non disponibile")


@pytest.fixture(scope="module")
def dump_bin(tmp_path_factory):
    exe = tmp_path_factory.mktemp("tester") / "host_dump"
    subprocess.run(["g++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", "-o", str(exe),
                    str(ROOT / "host" / "host_dump.cpp"), str(ROOT / "src" / "scenario.cpp")], check=True)
    return str(exe)


def tape(dump_bin, scenario, cycles, *args):
    out = subprocess.run([dump_bin, scenario, str(cycles), *args], capture_output=True, text=True, check=True).stdout
    period = idle = None
    cycles_meta, events = [], []
    for line in out.splitlines():
        p = line.split()
        if p[0] == "P":
            period = int(p[1])
        elif p[0] == "I":
            idle = int(p[1])
        elif p[0] == "C":
            cycles_meta.append(tuple(int(x) for x in p[1:]))
        elif p[0] == "E":
            events.append((int(p[1]), int(p[2]), int(p[3])))
    return period, idle, cycles_meta, events


def capture(dump_bin, scenario, cycles, *args, pwm=True):
    period, idle, meta, events = tape(dump_bin, scenario, cycles, *args)
    n = int(cycles * period * 1e-6 * FS)
    data = np.zeros(n, np.uint8)
    for ch in range(7):
        ts = [t * 1e-9 for t, s, c in events if (s | c) >> ch & 1]
        lv = [1 if s >> ch & 1 else 0 for t, s, c in events if (s | c) >> ch & 1]
        data |= ss.render_edges(((idle >> ch) & 1, np.array(ts), np.array(lv, np.uint8)), FS, n) << ch
    if pwm:
        data |= ss.render_edges(ss.pwm_edges(n / FS + 1e-3), FS, n) << 7   # CH8 = PWM hardware (LEDC)
    return Capture(FS, data), meta, events


# ----------------------------------- struttura -----------------------------------
@pytest.mark.parametrize("scenario,period", [("mixed", 5000), ("serial", 20000)])
def test_tape_invariants(dump_bin, scenario, period):
    p, idle, meta, events = tape(dump_bin, scenario, 5)
    assert p == period and idle & 0x80 == 0
    for c, n, length, i0, end in meta:
        assert length == period * 1000 and end == i0 == idle       # ogni ciclo torna ai livelli di riposo
        assert 20 < n < 1024
    ts = [e[0] for e in events]
    assert ts == sorted(ts) and max(ts) < 5 * period * 1000
    assert all(s & c == 0 and (s | c) & 0x80 == 0 for _, s, c in events)  # nessun conflitto; CH8 mai toccato


def test_tape_levels_consistent_with_idle(dump_bin):
    """Ogni evento cambia davvero il livello di un canale (nessun fronte ridondante)."""
    p, idle, meta, events = tape(dump_bin, "serial", 3)
    cur = idle
    for _, s, c in events:
        assert (~cur & s) | (cur & c), "evento senza effetto"
        cur = (cur | s) & ~c & 0xFF
    assert cur == idle


# ------------------------------------- misto --------------------------------------
def test_mixed_decodes_like_the_simulator(dump_bin):
    cap, _, _ = capture(dump_bin, "mixed", 4)
    uart = decode_uart(cap, 0, 115200)
    msgs = bytes(f.value for f in uart if f.ok)
    assert msgs == b"Hello\r\n" * 4
    cfg = DecoderConfig(uart=UartCfg(True, 0, 115200), spi=SpiCfg(True, 1, 2, 3, 4), i2c=I2cCfg(True, 5, 6))
    res = run_decoders(cap, cfg)
    assert res.errors == []
    lanes = {l.name: l.annotations for l in res.lanes}
    mosi = [a.text for a in lanes["SPI MOSI"]]
    miso = [a.text for a in lanes["SPI MISO"]]
    assert mosi[:3] == ["9F", "00", "00"] and miso[:3] == ["00", "EF", "40"] and len(mosi) >= 12
    assert lanes["I2C"] and "50" in lanes["I2C"][0].text
    assert not any(a.error for l in res.lanes for a in l.annotations)


def test_mixed_i2c_acks_and_data(dump_bin):
    from logic_core import decode_i2c
    cap, _, _ = capture(dump_bin, "mixed", 2)
    tr = decode_i2c(cap, 5, 6)
    assert len(tr) == 2
    for t in tr:
        assert t.address == 0x50 and not t.read and t.addr_ack and t.ended_with == "stop"
        assert [b.value for b in t.data] == [0x10, 0x3C] and all(b.ack for b in t.data)


# ------------------------------------ seriale -------------------------------------
@pytest.fixture(scope="module")
def serial_cap(dump_bin):
    return capture(dump_bin, "serial", 3)[0]


def test_serial_rs232(serial_cap):
    r = run_serial(serial_cap, SerialCfg(enabled=True, mode="rs232", ch_tx=0, ch_rx=1))
    assert r.baud == 9600 and r.fmt == (8, "N", 1)
    m = r.messages[:4]
    assert [(x.direction, x.data) for x in m] == [("TX", b"GET\r"), ("RX", b"OK\r\n")] * 2
    assert m[1].turnaround == pytest.approx(1.8e-3, rel=0.01)
    assert r.stats.n_errors == 0 and abs(r.stats.bit_timing.error_pct) < 0.1


def test_serial_rs485_with_de_modbus(serial_cap):
    r = run_serial(serial_cap, SerialCfg(enabled=True, mode="rs485", ch_ro=2, ch_de=3))
    assert r.baud == 19200 and r.fmt == (8, "E", 1)
    m = r.messages
    assert [x.direction for x in m] == ["TX", "RX"] * 3
    assert m[0].data == REQ
    for k in range(3):                                   # il contatore del registro cresce a ogni ciclo
        assert m[2 * k + 1].data == ss.modbus_frame(bytes([1, 3, 4, 0, 10 + k, 0, 11]))
        assert m[2 * k].de_lead == pytest.approx(100e-6, abs=2 / FS)
        assert m[2 * k].de_lag == pytest.approx(60e-6, abs=2 / FS)
    assert m[1].turnaround == pytest.approx(1.5e-3, rel=0.01)
    assert r.stats.de_faults == 0 and r.stats.n_errors == 0


def test_serial_rs485_ab_and_free_window(serial_cap):
    r = run_serial(serial_cap, SerialCfg(enabled=True, mode="rs485_ab", ch_a=4, ch_b=5, ch_de=3))
    ref = run_serial(serial_cap, SerialCfg(enabled=True, mode="rs485", ch_ro=2, ch_de=3))
    assert [(x.direction, x.data) for x in r.messages] == [(x.direction, x.data) for x in ref.messages]
    assert r.stats.undriven_events == 3                  # una finestra per ciclo, ~1,25 ms
    free = next(l for l in r.lanes if l.name == "Bus libero")
    assert all(abs((a.end - a.start) / FS - 1.25e-3) < 4 / FS for a in free.annotations)


# --------------------------------- difetti iniettati --------------------------------
def test_fault_baud_error(dump_bin):
    cap, _, _ = capture(dump_bin, "serial", 3, "baud=2")
    r = run_serial(cap, SerialCfg(enabled=True, mode="rs232", ch_tx=0, ch_rx=1, baud=9600, fmt=(8, "N", 1)))
    assert r.stats.bit_timing.error_pct == pytest.approx(2.0, abs=0.15)


def test_fault_de_late_and_early(dump_bin):
    late, _, _ = capture(dump_bin, "serial", 3, "delead=-100")
    r = run_serial(late, SerialCfg(enabled=True, mode="rs485", ch_ro=2, ch_de=3))
    assert r.messages[0].de_lead == pytest.approx(-100e-6, abs=2 / FS) and r.stats.de_faults == 3
    assert r.messages[0].data == REQ                      # il primo byte non finisce tra gli RX
    early, _, _ = capture(dump_bin, "serial", 3, "delag=-150")
    r = run_serial(early, SerialCfg(enabled=True, mode="rs485", ch_ro=2, ch_de=3))
    assert r.messages[0].de_lag == pytest.approx(-150e-6, abs=2 / FS) and r.stats.de_faults == 3


def test_fault_parity_errors(dump_bin):
    cap, _, _ = capture(dump_bin, "serial", 3, "parity=1")
    r = run_serial(cap, SerialCfg(enabled=True, mode="rs485", ch_ro=2, ch_de=3, baud=19200, fmt=(8, "E", 1)))
    assert r.stats.n_errors == 51 and all(m.errors == len(m.data) for m in r.messages)   # (8 + 9) byte x 3 cicli


def test_fault_crc_corrupts_every_other_response(dump_bin):
    cap, _, _ = capture(dump_bin, "serial", 4, "crc=1")
    r = run_serial(cap, SerialCfg(enabled=True, mode="rs485", ch_ro=2, ch_de=3, modbus=True))
    rsp = [f for f in r.modbus.frames if f.kind == "response"]
    assert [f.crc_ok for f in rsp] == [True, False, True, False]        # cicli 0, 2 buoni; 1, 3 con CRC errato
    assert r.modbus.stats.n_crc_errors == 2 and r.stats.n_errors == 0   # il bus e' integro: sbaglia solo il CRC
    clean, _, _ = capture(dump_bin, "serial", 4)
    rc = run_serial(clean, SerialCfg(enabled=True, mode="rs485", ch_ro=2, ch_de=3, modbus=True))
    assert rc.modbus.stats.n_crc_errors == 0 and rc.modbus.stats.n_pairs == 4


def test_compose_overflow_reported(dump_bin):
    out = subprocess.run([dump_bin, "serial", "1", "delead=999999999"], capture_output=True, text=True)
    assert out.returncode in (0, 1)                       # input estremo: nessun crash


def test_main_cpp_syntax_against_arduino_stub():
    """Controlla sintassi e tipi di main.cpp contro uno stub dell'API Arduino (NON è una compilazione per ESP32)."""
    subprocess.run(["g++", "-std=c++17", "-fsyntax-only", "-Wall", "-Wextra", "-Werror", f"-I{ROOT / 'host' / 'stub'}",
                    f"-I{ROOT / 'include'}", f"-I{ROOT / 'src'}", str(ROOT / "src" / "main.cpp")], check=True)
