import re

import numpy as np
import pytest

from logic_core import (AcquisitionConfig, Capture, DecoderConfig, SerialCfg, SimLogicDevice, UartCfg,
                        ab_to_line, decode_uart, detect_format, estimate_baud, measure_bit_timing,
                        run_decoders, run_serial)
from logic_core import sim_signals as ss

FS = 24e6
REQ = bytes.fromhex("010300000002C40B")
CH = {"tx": 0, "rx": 1, "ro": 2, "de": 3, "a": 4, "b": 5}


def serial_capture(n=1_000_000, fs=FS):
    dev = SimLogicDevice(seed=1, random_phase=False, scenario="serial")
    with dev:
        return dev.acquire(AcquisitionConfig(fs, n))


@pytest.fixture(scope="module")
def cap():
    return serial_capture()


def stream_cap(items, baud, fs=1e6, n=None, **kw):
    edges = ss.uart_stream_edges(items, baud, **kw)
    n = n or int(max(t for t, _ in items) * fs + len(items[-1][1]) * 12 / baud * fs) + 2000
    return Capture(fs, ss.render_edges(edges, fs, n))


def asciiburst(count, t0=1e-3, gap=300e-6, baud=9600, **kw):
    bits_per = 11 if kw.get("parity", "N") != "N" else 10
    items, t = [], t0
    for i in range(count):
        items.append((t, bytes([0x30 + (i * 7) % 40])))
        t += bits_per / baud + gap
    return items


# ----------------------------------- CRC / sim ----------------------------------
def test_crc16_modbus_vectors():
    assert ss.crc16_modbus(b"123456789") == 0x4B37          # valore di controllo standard
    assert ss.modbus_frame(bytes([1, 3, 0, 0, 0, 2])) == REQ  # richiesta Modbus nota (CRC C4 0B)


def test_sim_serial_scenario_decodes(cap):
    tx = decode_uart(cap, CH["tx"], 9600)
    assert bytes(f.value for f in tx[:4]) == b"GET\r"
    ro = decode_uart(cap, CH["ro"], 19200, 8, "E", 1)
    assert bytes(f.value for f in ro[:8]) == REQ and all(f.ok for f in ro)
    assert SimLogicDevice(scenario="serial").info()["signals"][2].startswith("RS-485 RO")
    with pytest.raises(ValueError):
        SimLogicDevice(scenario="boh")


# ---------------------------------- stima baud ----------------------------------
@pytest.mark.parametrize("ch,nominal", [(0, 9600), (1, 9600), (2, 19200)])
def test_estimate_baud_sim(cap, ch, nominal):
    e = estimate_baud(cap, ch)
    assert e.nominal == nominal and abs(e.error_pct) < 0.1
    assert e.baud == pytest.approx(nominal, rel=1e-3) and e.bit_time == pytest.approx(1 / nominal, rel=1e-3)


def test_estimate_baud_low_samplerate():
    e = estimate_baud(serial_capture(40_000, fs=1e6), 0)
    assert e.nominal == 9600 and abs(e.error_pct) < 1.0


def test_estimate_baud_nonstandard_and_failures():
    c = stream_cap(asciiburst(40, baud=12000), 12000, fs=2e6)
    e = estimate_baud(c, 0)
    assert e.nominal is None and e.error_pct is None and e.baud == pytest.approx(12000, rel=0.01)
    assert estimate_baud(Capture(1e6, np.zeros(10_000, np.uint8)), 0) is None  # nessun fronte
    d = np.zeros(10_000, np.uint8)
    d[::2] = 1  # impulsi da 1 campione: <2,5 campioni/bit
    assert estimate_baud(Capture(1e6, d), 0) is None


# --------------------------------- stima formato --------------------------------
def test_detect_format_sim(cap):
    f = detect_format(cap, 2, 19200)
    assert (f.data_bits, f.parity, f.stop_bits) == (8, "E", 1) and f.error_rate == 0.0 and f.frames >= 8
    assert f.alternatives == []          # 9N1 è identico a 8E1 per costruzione: non va segnalato
    g = detect_format(cap, 0, 9600)
    assert (g.data_bits, g.parity, g.stop_bits) == (8, "N", 1)


@pytest.mark.parametrize("fmt", [(7, "E", 1), (7, "O", 1), (8, "E", 1), (8, "O", 1), (8, "N", 1)])
def test_detect_format_parity_resolves_ambiguity(fmt):
    bits, par, stop = fmt
    # contenuto con parità varia; 7E1 si decodificherebbe senza errori anche come 8N1: la parità deve prevalere
    items = [(1e-3 + i * 2e-3, bytes([(i * 37 + 11) % (1 << bits)])) for i in range(40)]
    c = stream_cap(items, 9600, fs=1e6, data_bits=bits, parity=par, stop=stop)
    f = detect_format(c, 0, 9600)
    assert (f.data_bits, f.parity, f.stop_bits) == fmt and f.error_rate == 0.0


def test_detect_format_too_few_frames():
    c = stream_cap([(1e-3, b"A"), (3e-3, b"B")], 9600)
    assert detect_format(c, 0, 9600) is None


# ------------------------------------ bit timing ---------------------------------
def test_bit_timing_clean(cap):
    t = measure_bit_timing(cap, 2, 19200)
    assert abs(t.error_pct) < 0.05 and t.n_runs > 50
    assert t.jitter_pp < 3 / FS        # solo quantizzazione del campionamento


def test_bit_timing_baud_error():
    items = [(1e-3 + i * 6e-3, b"UU\x00\xff") for i in range(20)]
    c = stream_cap(items, 9600 * 1.02, fs=FS, n=int(125e-3 * FS))   # il trasmettitore va il 2 % troppo veloce
    t = measure_bit_timing(c, 0, 9600)
    assert t.error_pct == pytest.approx(2.0, abs=0.1) and t.baud_actual == pytest.approx(9792, rel=2e-3)


def test_bit_timing_too_few_edges():
    assert measure_bit_timing(Capture(1e6, np.zeros(1000, np.uint8)), 0, 9600) is None


# ---------------------------------------- A/B -------------------------------------
def test_ab_to_line_logic():
    d = np.array([0b10, 0b10, 0b01, 0b01, 0b00, 0b11, 0b10], np.uint8)  # bit0 = A, bit1 = B
    line, runs = ab_to_line(Capture(1e6, d), 0, 1)
    assert line.data.tolist() == [1, 1, 0, 0, 0, 0, 1]   # mark = A basso e B alto; A == B mantiene l'ultimo livello
    assert runs.tolist() == [[4, 6]]
    sw, _ = ab_to_line(Capture(1e6, d), 0, 1, swap=True)
    assert sw.data.tolist() == [0, 0, 1, 1, 1, 1, 0]
    first, r2 = ab_to_line(Capture(1e6, np.array([0, 0, 0b10], np.uint8)), 0, 1)  # inizio non pilotato: mark
    assert first.data.tolist() == [1, 1, 1] and r2.tolist() == [[0, 2]]


# ------------------------------------ RS-232 --------------------------------------
def test_run_serial_rs232_auto(cap):
    r = run_serial(cap, SerialCfg(enabled=True, mode="rs232", ch_tx=CH["tx"], ch_rx=CH["rx"]))
    assert r.baud == 9600 and r.fmt == (8, "N", 1)
    assert r.detected_baud.nominal == 9600 and r.detected_format.error_rate == 0.0
    m = r.messages[:4]
    assert [(x.direction, x.data) for x in m] == [("TX", b"GET\r"), ("RX", b"OK\r\n"), ("TX", b"GET\r"), ("RX", b"OK\r\n")]
    assert m[1].turnaround == pytest.approx(1.8e-3, rel=0.01)      # TX -> RX
    assert m[2].turnaround == pytest.approx(9.87e-3, rel=0.01)     # RX -> TX del ciclo seguente
    assert m[0].turnaround is None and m[0].gap_before is None
    assert [l.name for l in r.lanes] == ["RS-232 TX", "RS-232 RX", "Messaggi"]
    assert r.stats.turnaround_min == pytest.approx(1.8e-3, rel=0.01) and r.stats.n_errors == 0
    assert r.stats.tx_bytes >= 8 and r.stats.rx_bytes >= 8 and r.stats.de_faults == 0
    assert abs(r.stats.bit_timing.error_pct) < 0.1
    msg_lane = r.lanes[-1].annotations
    assert msg_lane[0].text == "TX 47 45 54 0D" and not msg_lane[0].error


def test_run_serial_rs232_single_line_manual_format(cap):
    r = run_serial(cap, SerialCfg(enabled=True, mode="rs232", ch_tx=CH["tx"], ch_rx=None, baud=9600, fmt=(8, "N", 1)))
    assert {m.direction for m in r.messages} == {"TX"} and r.detected_baud is None and r.detected_format is None


def test_run_serial_inverted_line(cap):
    inv = Capture(cap.samplerate, cap.data ^ 0b11)   # RS-232 senza convertitore: livelli invertiti
    r = run_serial(inv, SerialCfg(enabled=True, mode="rs232", ch_tx=0, ch_rx=1, invert=True))
    assert r.messages[0].data == b"GET\r" and r.messages[1].data == b"OK\r\n"


# ------------------------------------ RS-485 --------------------------------------
def test_run_serial_rs485_with_de(cap):
    r = run_serial(cap, SerialCfg(enabled=True, mode="rs485", ch_ro=CH["ro"], ch_de=CH["de"]))
    assert r.baud == 19200 and r.fmt == (8, "E", 1)
    m = r.messages[:4]
    assert [x.direction for x in m] == ["TX", "RX", "TX", "RX"]
    assert m[0].data == REQ and m[1].data == ss.modbus_frame(bytes([1, 3, 4, 0, 10, 0, 11]))
    assert m[3].data == ss.modbus_frame(bytes([1, 3, 4, 0, 11, 0, 11]))      # il contatore cresce a ogni ciclo
    assert m[1].turnaround == pytest.approx(1.5e-3, rel=0.01)
    for x in (m[0], m[2]):
        assert x.de_lead == pytest.approx(100e-6, abs=2 / FS) and x.de_lag == pytest.approx(60e-6, abs=2 / FS)
        assert not x.de_fault
    assert m[1].de_lead is None and m[1].de_lag is None                       # i messaggi RX non hanno temporizzazione DE
    assert r.stats.de_faults == 0 and r.stats.n_errors == 0
    assert r.stats.de_lead_min == pytest.approx(100e-6, abs=2 / FS) and r.stats.de_lag_max == pytest.approx(60e-6, abs=2 / FS)
    assert [l.name for l in r.lanes] == ["RS-485 TX", "RS-485 RX", "Messaggi"]
    assert r.notes == []


def test_run_serial_rs485_without_de(cap):
    r = run_serial(cap, SerialCfg(enabled=True, mode="rs485", ch_ro=CH["ro"]))
    assert r.messages[0].direction == "BUS" and any("DE non collegato" in n for n in r.notes)
    assert len(r.messages[0].data) == 17            # pausa 1,5 ms < 3,5 caratteri (2 ms): richiesta e risposta uniti
    assert r.messages[0].turnaround is None
    r2 = run_serial(cap, SerialCfg(enabled=True, mode="rs485", ch_ro=CH["ro"], gap_chars=2.0))
    assert [len(x.data) for x in r2.messages[:2]] == [8, 9]
    assert r2.messages[1].gap_before == pytest.approx(1.5e-3, rel=0.01) and r2.messages[1].turnaround is None
    assert [l.name for l in r2.lanes] == ["RS-485", "Messaggi"]


def test_run_serial_rs485_ab(cap):
    cfg = SerialCfg(enabled=True, mode="rs485_ab", ch_a=CH["a"], ch_b=CH["b"], ch_de=CH["de"])
    r = run_serial(cap, cfg)
    ref = run_serial(cap, SerialCfg(enabled=True, mode="rs485", ch_ro=CH["ro"], ch_de=CH["de"]))
    assert [(m.direction, m.data) for m in r.messages] == [(m.direction, m.data) for m in ref.messages]
    free = next(l for l in r.lanes if l.name == "Bus libero")
    assert len(free.annotations) == 2                       # una finestra per ciclo (la cattura ne contiene 2): i 1-2 campioni di skew sono scartati
    us = [int(re.search(r"(\d+) µs", a.detail).group(1)) for a in free.annotations]
    assert all(abs(u - 1250) <= 3 for u in us) and r.stats.undriven_events == 2
    # sigle A/B scambiate: swap + linea invertita ricostruiscono lo stesso traffico
    r2 = run_serial(cap, SerialCfg(enabled=True, mode="rs485_ab", ch_a=CH["a"], ch_b=CH["b"], ch_de=CH["de"],
                                   swap_ab=True, invert=True))
    assert [m.data for m in r2.messages] == [m.data for m in r.messages]


def test_run_serial_de_late_and_early(cap):
    base = SerialCfg(enabled=True, mode="rs485", ch_ro=CH["ro"], ch_de=CH["de"])
    de = cap.channel(CH["de"])
    k = int(200e-6 * FS)

    def with_de(sig):
        data = (cap.data & np.uint8(~(1 << CH["de"]) & 0xFF)) | (sig.astype(np.uint8) << CH["de"])
        return Capture(cap.samplerate, data)

    late = run_serial(with_de(np.concatenate((np.zeros(k, bool), de[:-k]))), base)
    m = late.messages[0]
    assert m.direction == "TX" and m.data == REQ                 # il primo byte non finisce tra gli RX
    assert m.de_lead == pytest.approx(-100e-6, abs=2 / FS) and m.de_fault and late.stats.de_faults >= 1
    assert late.lanes[-1].annotations[0].error and "ritardo" in late.lanes[-1].annotations[0].detail

    early = run_serial(with_de(np.concatenate((de[k:], np.zeros(k, bool)))), base)
    m = early.messages[0]
    assert m.direction == "TX" and m.data == REQ
    assert m.de_lag == pytest.approx(-140e-6, abs=2 / FS) and m.de_fault and m.de_lead > 0
    assert "prima della fine" in early.lanes[-1].annotations[0].detail


def test_run_serial_de_active_low(cap):
    inv = Capture(cap.samplerate, cap.data ^ (1 << CH["de"]))
    r = run_serial(inv, SerialCfg(enabled=True, mode="rs485", ch_ro=CH["ro"], ch_de=CH["de"], de_active_high=False))
    assert r.messages[0].direction == "TX" and r.messages[0].de_lead == pytest.approx(100e-6, abs=2 / FS)


def test_run_serial_framing_errors_flagged(cap):
    r = run_serial(cap, SerialCfg(enabled=True, mode="rs485", ch_ro=CH["ro"], ch_de=CH["de"], baud=19200, fmt=(8, "O", 1)))
    assert r.stats.n_errors > 0 and r.messages[0].errors > 0
    assert r.lanes[-1].annotations[0].error           # messaggio con byte errati: riquadro rosso


# --------------------------------- errori / integrazione --------------------------
@pytest.mark.parametrize("cfg,msg", [
    (SerialCfg(mode="x"), "non valido"),
    (SerialCfg(mode="rs232", ch_tx=None, ch_rx=None), "almeno un canale"),
    (SerialCfg(mode="rs485_ab", ch_a=4, ch_b=4), "diversi"),
    (SerialCfg(mode="rs232", ch_tx=6, ch_rx=None), "baud non rilevabile"),       # canale fisso, nessun fronte
    (SerialCfg(mode="rs232", ch_tx=0, ch_rx=None, baud=12e6, fmt=(8, "N", 1)), "campioni/bit"),
])
def test_run_serial_errors(cap, cfg, msg):
    with pytest.raises(ValueError, match=msg):
        run_serial(cap, cfg)


def test_run_serial_format_not_detectable():
    c = stream_cap([(1e-3, b"A"), (3e-3, b"B"), (5e-3, b"C")], 9600)
    with pytest.raises(ValueError, match="formato non rilevabile"):
        run_serial(c, SerialCfg(mode="rs232", ch_tx=0, ch_rx=None, baud=9600))


def test_run_decoders_includes_serial(cap):
    cfg = DecoderConfig(uart=UartCfg(enabled=False), serial=SerialCfg(enabled=True, mode="rs485", ch_ro=2, ch_de=3))
    cfg.spi.enabled = cfg.i2c.enabled = False
    res = run_decoders(cap, cfg)
    assert [l.name for l in res.lanes] == ["RS-485 TX", "RS-485 RX", "Messaggi"] and res.serial is not None
    bad = DecoderConfig(serial=SerialCfg(enabled=True, mode="rs232", ch_tx=6, ch_rx=None))
    bad.uart.enabled = bad.spi.enabled = bad.i2c.enabled = False
    res = run_decoders(cap, bad)
    assert res.serial is None and res.errors and res.errors[0].startswith("Seriale:")
    assert run_decoders(cap, DecoderConfig(uart=UartCfg(enabled=False))).serial is None   # disattivato di default


# --------------------------- impulsi spuri (diafonia / rimbalzi) ---------------------------
def _with_burst(cap, ch, pos, n, step=3):
    d = cap.data.copy()
    for k in range(n):
        d[pos + step * k] ^= 1 << ch
    return Capture(cap.samplerate, d)


def test_baud_detection_survives_glitch_burst_on_ro(cap):
    g = _with_burst(cap, 2, 5000, 7)
    assert estimate_baud(g, 2) is None                         # senza filtro la raffica falsa la stima
    r = run_serial(g, SerialCfg(enabled=True, mode="rs485", ch_ro=2, ch_de=3))
    assert r.baud == 19200 and r.fmt == (8, "E", 1) and r.stats.n_errors == 0
    assert r.messages[0].data == REQ and any("impulsi spuri" in n for n in r.notes)


def test_glitch_filter_leaves_clean_capture_untouched(cap):
    r = run_serial(cap, SerialCfg(enabled=True, mode="rs485", ch_ro=2, ch_de=3))
    assert not any("impulsi spuri" in n for n in r.notes)


def test_deglitch_bits_cases():
    from logic_core.serialbus import deglitch_bits
    b = np.array([1] * 10 + [0] + [1] * 10, np.int8)           # impulso isolato su livello alto
    out, n = deglitch_bits(b, 2)
    assert n == 1 and out.min() == 1
    b = np.array([1] * 10 + [0, 1, 0] + [0] * 10, np.int8)     # raffica che passa da alto a basso: transizione a meta'
    out, n = deglitch_bits(b, 2)
    assert n == 1 and out[:10].min() == 1 and out[-10:].max() == 0 and (np.diff(out) != 0).sum() == 1
    b = np.array([0] + [1] * 10, np.int8)                      # il primo tratto della cattura non si tocca
    assert deglitch_bits(b, 2)[1] == 0
