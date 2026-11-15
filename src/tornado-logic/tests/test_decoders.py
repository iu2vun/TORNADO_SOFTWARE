import numpy as np
import pytest

from logic_core import (AcquisitionConfig, Capture, SimLogicDevice, decode_i2c, decode_spi,
                        decode_uart)
from logic_core import sim_signals as ss

FS = 24e6
MSG = b"Hello\r\n"


def sim_capture(n=100_000, fs=FS):
    dev = SimLogicDevice(seed=1, random_phase=False)
    with dev:
        return dev.acquire(AcquisitionConfig(fs, n))


def wave(edges, fs, n):
    return ss.render_edges(edges, fs, n)


def custom_capture(fs, n, **chan_edges):
    """Cattura da fronti: chan_edges = {'0': edges, '1': edges, ...} (chiave = indice canale)."""
    data = np.zeros(n, dtype=np.uint8)
    for ch, e in chan_edges.items():
        data |= wave(e, fs, n) << int(ch[1:])
    return Capture(fs, data)


def uart_bits(values, data_bits=8, parity="N", stop=1, lsb=True, bad_stop_at=None, idle=10, gap=0):
    bits = [1] * idle
    for i, v in enumerate(values):
        bits += [1] * gap if i else []
        d = [(v >> k) & 1 for k in range(data_bits)]
        if not lsb:
            d = d[::-1]
        bits += [0] + d
        if parity != "N":
            ones = sum(d)
            bits.append(ones % 2 if parity == "E" else 1 - ones % 2)
        bits += [0 if bad_stop_at == i else 1] * stop
    return bits + [1] * idle


def uart_cap(values, baud, fs, **kw):
    bits = uart_bits(values, **{k: v for k, v in kw.items() if k in
                                ("data_bits", "parity", "stop", "lsb", "bad_stop_at", "gap")})
    edges = ss.bits_to_edges(bits, 1.0 / baud)
    n = int(len(bits) / baud * fs) + 10
    return custom_capture(fs, n, c0=edges)


# ---------------------------------- UART ------------------------------------
def test_uart_sim_stream():
    cap = sim_capture()
    frames = decode_uart(cap, 0, 115200)
    assert len(frames) >= 7 * 4
    got = bytes(f.value for f in frames)
    assert got.startswith(MSG * 4)
    assert all(f.ok for f in frames)
    assert frames[0].start < frames[0].end <= frames[1].start  # frame adiacenti: end == start successivo


def test_uart_low_samplerate():
    cap = sim_capture(n=20_000, fs=1e6)  # ~8,7 campioni/bit
    frames = decode_uart(cap, 0, 115200)
    assert bytes(f.value for f in frames).startswith(MSG * 3)


@pytest.mark.parametrize("parity", ["N", "E", "O"])
def test_uart_parity_ok(parity):
    vals = [0x00, 0xFF, 0x55, 0xA3]
    cap = uart_cap(vals, 9600, 1e6, parity=parity)
    fr = decode_uart(cap, 0, 9600, parity=parity)
    assert [f.value for f in fr] == vals and all(f.ok for f in fr)


def test_uart_parity_error():
    cap = uart_cap([0x01, 0x02], 9600, 1e6, parity="E")
    fr = decode_uart(cap, 0, 9600, parity="O")  # parità opposta -> errori
    assert [f.value for f in fr] == [0x01, 0x02]
    assert all(f.parity_error for f in fr)


def test_uart_framing_error_and_resync():
    # gap tra i frame: con stop basso e start subito dopo non c'è fronte di discesa da cui risincronizzarsi
    cap = uart_cap([0x41, 0x42, 0x43], 9600, 1e6, bad_stop_at=1, gap=2)
    fr = decode_uart(cap, 0, 9600)
    assert [f.value for f in fr] == [0x41, 0x42, 0x43]
    assert [f.framing_error for f in fr] == [False, True, False]


def test_uart_two_stop_msb_7bit():
    vals = [0x41, 0x7F, 0x00]
    cap = uart_cap(vals, 9600, 1e6, data_bits=7, stop=2, lsb=False)
    fr = decode_uart(cap, 0, 9600, data_bits=7, stop_bits=2, lsb_first=False)
    assert [f.value for f in fr] == vals and all(f.ok for f in fr)


def test_uart_invert():
    cap = sim_capture()
    inv = Capture(cap.samplerate, cap.data ^ 1)
    fr = decode_uart(inv, 0, 115200, invert=True)
    assert bytes(f.value for f in fr).startswith(MSG * 4)


def test_uart_errors():
    cap = sim_capture(n=1000)
    with pytest.raises(ValueError):
        decode_uart(cap, 0, 115200, parity="X")
    with pytest.raises(ValueError):
        decode_uart(cap, 0, 24e6 / 2)  # 2 campioni/bit
    assert decode_uart(Capture(1e6, np.ones(1000, np.uint8)), 0, 9600) == []


# ----------------------------------- SPI ------------------------------------
def test_spi_sim_mode0():
    cap = sim_capture()
    tr = decode_spi(cap, clk=1, mosi=2, miso=3, cs=4)
    assert len(tr) >= 5
    for t in tr[:-1]:
        assert t.mosi == [0x9F, 0x00, 0x00]
        assert t.miso == [0x00, 0xEF, 0x40]
        assert t.partial_bits == 0
        assert t.words[0].index >= t.start and t.words[-1].index < t.end
    last = tr[-1]  # l'ultima transazione è tagliata dalla fine della cattura
    assert last.end == len(cap) and len(last.words) < 3


def test_spi_without_cs_single_transfer():
    cap = sim_capture(n=2000)  # solo la prima transazione
    tr = decode_spi(cap, clk=1, mosi=2)
    assert len(tr) == 1 and tr[0].mosi[:3] == [0x9F, 0, 0]
    assert tr[0].miso == []


def test_spi_lsb_first():
    cap = sim_capture()
    tr = decode_spi(cap, 1, 2, 3, 4, msb_first=False)[0]
    assert tr.mosi[0] == 0xF9  # 0x9F con bit invertiti


def test_spi_cpol1_cpha0_with_inverted_clock():
    cap = sim_capture()
    inv = Capture(cap.samplerate, cap.data ^ 0b10)  # CLK invertito: idle alto
    tr = decode_spi(inv, 1, 2, 3, 4, cpol=1, cpha=0)[0]
    assert tr.mosi == [0x9F, 0, 0]


def test_spi_wrong_phase_differs():
    cap = sim_capture()
    good = decode_spi(cap, 1, 2, 3, 4)[0].mosi
    bad = decode_spi(cap, 1, 2, 3, 4, cpha=1)[0].mosi
    assert good != bad


def test_spi_16bit_words_and_partial():
    cap = sim_capture()
    tr = decode_spi(cap, 1, 2, 3, 4, bits=16)[0]  # 24 bit = 1 parola da 16 + 8 residui
    assert len(tr.words) == 1 and tr.partial_bits == 8
    assert tr.words[0].mosi == 0x9F00


def test_spi_errors():
    cap = sim_capture(n=1000)
    with pytest.raises(ValueError):
        decode_spi(cap, 1)
    with pytest.raises(ValueError):
        decode_spi(cap, 1, 2, cpol=2)


# ----------------------------------- I2C ------------------------------------
def test_i2c_sim_write():
    cap = sim_capture()
    tx = decode_i2c(cap, 5, 6)
    assert len(tx) >= 10
    for t in tx[:-1]:
        assert t.address == 0x50 and not t.read and t.addr_ack
        assert [(b.value, b.ack) for b in t.data] == [(0x10, True), (0x3C, True)]
        assert t.ended_with == "stop" and not t.truncated and t.stop > t.start


def i2c_cap(fs=2e6, n=3000, **kw):
    e = ss.i2c_edges(n / fs, **kw)
    return custom_capture(fs, n, c5=e["scl"], c6=e["sda"])


def test_i2c_read_and_nack():
    cap = i2c_cap(addr=0x2A, data=(0xDE, 0xAD, 0xBE), read=True, acks=(0, 0, 0, 1))
    t = decode_i2c(cap, 5, 6)[0]
    assert t.address == 0x2A and t.read and t.addr_ack
    assert [(b.value, b.ack) for b in t.data] == [(0xDE, True), (0xAD, True), (0xBE, False)]


def test_i2c_address_nack():
    cap = i2c_cap(addr=0x77, data=(), acks=(1,))
    t = decode_i2c(cap, 5, 6)[0]
    assert t.address == 0x77 and not t.addr_ack and t.data == []


def test_i2c_capture_ends_mid_transaction():
    cap = sim_capture()
    # taglia dopo il primo START ma prima dello STOP
    first = decode_i2c(cap, 5, 6)[0]
    cut = cap.slice(0, first.start + (first.stop - first.start) // 2)
    t = decode_i2c(cut, 5, 6)[0]
    assert t.ended_with == "end" and t.stop is None
    assert t.address == 0x50


def test_i2c_idle_bus_has_no_transactions():
    assert decode_i2c(Capture(1e6, np.full(1000, 0b01100000, np.uint8)), 5, 6) == []
