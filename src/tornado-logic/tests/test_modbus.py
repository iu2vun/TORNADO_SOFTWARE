import numpy as np
import pytest

from logic_core import AcquisitionConfig, SerialCfg, SimLogicDevice, run_serial
from logic_core import sim_signals as ss
from logic_core.modbus import EXCEPTIONS, crc16, decode_modbus
from logic_core.serialbus import Message

FS = 1e6


def msg(data, t, direction="BUS", errors=0):
    return Message(int(t * FS), int(t * FS) + 100, direction, bytes(data), errors)


REQ = ss.modbus_frame(bytes([1, 3, 0, 0, 0, 2]))
RSP = ss.modbus_frame(bytes([1, 3, 4, 0, 10, 0, 11]))


def test_crc_vectors():
    assert crc16(b"123456789") == 0x4B37 and crc16(REQ[:-2]) == REQ[-2] | REQ[-1] << 8


def test_request_response_pair_with_direction():
    r = decode_modbus([msg(REQ, 0, "TX"), msg(RSP, 0.002, "RX")], FS)
    a, b = r.frames
    assert (a.kind, b.kind) == ("request", "response") and a.ok and b.ok
    assert "da 0 ×2 reg" in a.text and "risposta 2 reg: 10, 11" in b.text
    assert b.response_time == pytest.approx(0.0019, abs=1e-6) and r.stats.n_pairs == 1 and r.stats.slaves == [1]


def test_pairing_without_direction_uses_length_and_open_request():
    r = decode_modbus([msg(REQ, 0), msg(RSP, 0.002), msg(REQ, 0.02), msg(RSP, 0.022)], FS)
    assert [f.kind for f in r.frames] == ["request", "response"] * 2 and r.stats.n_pairs == 2
    assert r.stats.resp_median == pytest.approx(0.0019, abs=1e-6)


def test_coil_response_of_8_bytes_is_not_mistaken_for_request():
    req = ss.modbus_frame(bytes([2, 1, 0, 0, 0, 24]))
    rsp = ss.modbus_frame(bytes([2, 1, 3, 0xAA, 0xBB, 0xCC]))          # 8 byte come una richiesta
    r = decode_modbus([msg(req, 0), msg(rsp, 0.003)], FS)
    assert [f.kind for f in r.frames] == ["request", "response"] and "3 byte: AA BB CC" in r.frames[1].text


def test_crc_error_is_flagged():
    bad = bytearray(RSP)
    bad[-1] ^= 0x01
    r = decode_modbus([msg(REQ, 0, "TX"), msg(bytes(bad), 0.002, "RX")], FS)
    assert not r.frames[1].crc_ok and not r.frames[1].ok and "CRC errato" in r.frames[1].text
    assert r.stats.n_crc_errors == 1 and r.stats.n_pairs == 1


def test_exception_response():
    exc = ss.modbus_frame(bytes([1, 0x83, 2]))
    r = decode_modbus([msg(REQ, 0, "TX"), msg(exc, 0.002, "RX")], FS)
    f = r.frames[1]
    assert f.kind == "exception" and f.exception_code == 2 and f.ok and f.function == 3
    assert EXCEPTIONS[2] in f.text and r.stats.n_exceptions == 1 and r.stats.n_pairs == 1


def test_unanswered_request_and_broadcast():
    bcast = ss.modbus_frame(bytes([0, 6, 0, 1, 0, 5]))
    r = decode_modbus([msg(REQ, 0), msg(REQ, 0.02), msg(bcast, 0.04), msg(REQ, 0.06)], FS)
    assert r.stats.n_unanswered == 2 and r.frames[2].broadcast and "senza risposta" in r.notes[0]


def test_short_and_wrong_length_frames():
    r = decode_modbus([msg(b"\x01\x03", 0), msg(REQ[:-1], 0.01)], FS)
    assert r.frames[0].kind == "invalid" and not r.frames[1].length_ok and r.stats.n_malformed == 2


def test_write_functions_described():
    w6 = ss.modbus_frame(bytes([1, 6, 0, 7, 0x12, 0x34]))
    w16 = ss.modbus_frame(bytes([1, 16, 0, 0, 0, 2, 4, 0, 1, 0, 2]))
    w5 = ss.modbus_frame(bytes([1, 5, 0, 3, 0xFF, 0]))
    r = decode_modbus([msg(w6, 0, "TX"), msg(w16, 0.01, "TX"), msg(w5, 0.02, "TX")], FS)
    assert "reg 7 = 4660" in r.frames[0].text and "×2 reg = 1, 2" in r.frames[1].text and "coil 3 = ON" in r.frames[2].text


def test_run_serial_modbus_on_simulator():
    dev = SimLogicDevice(seed=1, random_phase=False, scenario="serial")
    with dev:
        cap = dev.acquire(AcquisitionConfig(24e6, 1_000_000))
    off = run_serial(cap, SerialCfg(enabled=True, mode="rs485", ch_ro=2, ch_de=3))
    assert off.modbus is None and not any(l.name == "Modbus" for l in off.lanes)
    r = run_serial(cap, SerialCfg(enabled=True, mode="rs485", ch_ro=2, ch_de=3, modbus=True))
    mb = r.modbus
    assert mb.stats.n_crc_errors == 0 and mb.stats.n_pairs >= 1 and mb.stats.n_unanswered == 0
    assert mb.stats.resp_median == pytest.approx(1.5e-3, rel=0.02)
    assert [f.kind for f in mb.frames[:4]] == ["request", "response", "request", "response"]
    lane = next(l for l in r.lanes if l.name == "Modbus")
    assert len(lane.annotations) == len(mb.frames) and not any(a.error for a in lane.annotations)


def test_run_serial_modbus_detects_corrupted_crc():
    dev = SimLogicDevice(seed=1, random_phase=False, scenario="serial")
    with dev:
        cap = dev.acquire(AcquisitionConfig(24e6, 1_000_000))
    r0 = run_serial(cap, SerialCfg(enabled=True, mode="rs485", ch_ro=2, ch_de=3))
    m = r0.messages[1]                                    # prima risposta: si inverte un bit del CRC sulla linea
    data = cap.data.copy()
    # ultimo byte della risposta: si forza RO alto (idle) sull'intero ultimo carattere finche' il decoder vede 0xFF
    seg = slice(m.end - int(11 * 24e6 / 19200) + 2, m.end - 2)
    data[seg] |= 0b100
    r = run_serial(type(cap)(cap.samplerate, data), SerialCfg(enabled=True, mode="rs485", ch_ro=2, ch_de=3, modbus=True))
    assert r.modbus.stats.n_crc_errors >= 1 or r.modbus.stats.n_malformed >= 1


def test_frame_cut_by_capture_edge_is_not_an_error():
    r = decode_modbus([msg(REQ, 0, "TX"), msg(RSP[:3], 0.002, "RX")], FS, n_samples=int(0.002 * FS) + 150,
                      edge_margin=100)
    assert r.frames[1].truncated and r.frames[1].ok and "troncato" in r.frames[1].text
    assert r.stats.n_malformed == 0 and r.stats.n_crc_errors == 0
    r = decode_modbus([msg(REQ, 0, "TX"), msg(RSP[:3], 0.002, "RX"), msg(REQ, 0.02, "TX")], FS,
                      n_samples=100000, edge_margin=100)
    assert not r.frames[1].truncated and r.stats.n_malformed == 1      # in mezzo alla cattura e' un vero errore


def test_warns_when_traffic_is_not_modbus():
    r = decode_modbus([msg(b"GET\r", 0, "TX"), msg(b"OK\r\n", 0.002, "RX")], FS)
    assert r.stats.n_crc_errors == 2 and any("non è Modbus" in n for n in r.notes)
    ok = decode_modbus([msg(REQ, 0, "TX"), msg(RSP, 0.002, "RX")], FS)
    assert not any("non è Modbus" in n for n in ok.notes)
