import numpy as np
import pytest

from logic_core import (AcquisitionConfig, Capture, DecoderConfig, SimLogicDevice, SpiCfg, UartCfg,
                        decode_i2c, decode_spi, export_csv, export_vcd, i2c_annotations, load_npz,
                        run_decoders, run_measures, save_npz, spi_annotations, uart_annotations,
                        decode_uart)

FS = 24e6


def sim_capture(n=100_000, trig=None):
    dev = SimLogicDevice(seed=1, random_phase=False)
    with dev:
        return dev.acquire(AcquisitionConfig(FS, n, trigger=trig))


# ------------------------- indici di fine parola/byte ----------------------------
def test_end_indices():
    cap = sim_capture()
    w = decode_spi(cap, 1, 2, 3, 4)[0].words
    assert all(x.end > x.index for x in w) and w[0].end < w[1].index
    t = decode_i2c(cap, 5, 6)[0]
    assert t.start < t.addr_end < t.data[0].index < t.data[0].end < t.data[1].index < t.data[1].end < t.stop


# ------------------------------- annotazioni -------------------------------------
def test_uart_annotations():
    cap = sim_capture()
    ann = uart_annotations(decode_uart(cap, 0, 115200))
    assert [a.text for a in ann[:7]] == ["48 H", "65 e", "6C l", "6C l", "6F o", "0D \\r", "0A \\n"]
    assert not any(a.error for a in ann)
    assert all(a.start < a.end for a in ann)


def test_spi_annotations():
    cap = sim_capture()
    tr = decode_spi(cap, 1, 2, 3, 4)
    mosi = spi_annotations(tr[:1], "mosi")
    miso = spi_annotations(tr[:1], "miso")
    assert [a.text for a in mosi] == ["9F", "00", "00"]
    assert [a.text for a in miso] == ["00", "EF", "40"]
    assert spi_annotations(tr[:1], "mosi", bits=16)[0].text == "009F"  # larghezza esadecimale da bits
    with pytest.raises(ValueError):
        spi_annotations(tr, "cs")


def test_i2c_annotations_and_nack_rules():
    cap = sim_capture()
    ann = i2c_annotations(decode_i2c(cap, 5, 6)[:1])
    assert [a.text for a in ann] == ["50 W", "10", "3C"]
    assert not any(a.error for a in ann)

    from logic_core import I2cByte, I2cTransaction
    # lettura: NACK finale del master = normale; NACK del primo byte = errore; NACK su indirizzo = errore
    t = I2cTransaction(0, 100, 0x2A, True, True,
                       [I2cByte(10, 0xDE, True, 20), I2cByte(30, 0xAD, False, 40)], addr_end=9)
    a = i2c_annotations([t])
    assert [x.error for x in a] == [False, False, False] and a[2].text == "AD NACK"
    t2 = I2cTransaction(0, 100, 0x2A, False, False, [I2cByte(10, 1, False, 20)], addr_end=9)
    assert [x.error for x in i2c_annotations([t2])] == [True, True]


# --------------------------------- analisi ---------------------------------------
def test_run_decoders_default_sim_map():
    res = run_decoders(sim_capture(), DecoderConfig())
    assert [l.name for l in res.lanes] == ["UART", "SPI MOSI", "SPI MISO", "I2C"]
    assert res.errors == [] and all(l.annotations for l in res.lanes)


def test_run_decoders_errors_and_disabled():
    cap = sim_capture()
    cfg = DecoderConfig(uart=UartCfg(baud=12e6), spi=SpiCfg(mosi=None, miso=None))
    cfg.i2c.enabled = False
    res = run_decoders(cap, cfg)
    assert res.lanes == []
    assert any(e.startswith("UART") for e in res.errors) and any(e.startswith("SPI") for e in res.errors)
    assert len(res.errors) == 2


def test_run_decoders_spi_only_mosi():
    cfg = DecoderConfig(uart=UartCfg(enabled=False), spi=SpiCfg(miso=None))
    cfg.i2c.enabled = False
    assert [l.name for l in run_decoders(sim_capture(), cfg).lanes] == ["SPI MOSI"]


def test_run_measures_full_and_range():
    cap = sim_capture(240_000)
    full = run_measures(cap, channels=(7, 1))
    assert [s.channel for s in full.stats] == [7, 1]
    assert full.stats[0].frequency == pytest.approx(1000.0)
    assert full.glitches == []
    # solo un intervallo senza fronti PWM: nessuna frequenza
    sub = run_measures(cap, channels=(7,), sample_range=(10_000, 12_000))
    assert sub.stats[0].n_rising == 0 and sub.stats[0].frequency is None


def test_run_measures_glitch_offset_is_absolute():
    cap = sim_capture(240_000)
    data = cap.data.copy()
    data[20_000] |= 1 << 7
    g = run_measures(Capture(FS, data), channels=(7,), glitch_s=100e-9, sample_range=(15_000, 30_000))
    assert [(c, x.index, x.level) for c, x in g.glitches] == [(7, 20_000, 1)]


def test_glitch_truncation():
    d = np.zeros(100_000, np.uint8)
    d[1::4] = 1  # 25k impulsi da 1 campione
    res = run_measures(Capture(FS, d), channels=(0,), glitch_s=100e-9)
    assert res.glitches_truncated and len(res.glitches) == 10_000


# ------------------------------ file ed export ------------------------------------
def test_npz_roundtrip(tmp_path):
    cap = sim_capture(5000, trig=None)
    cap.trigger_index = 123
    cap.channel_names[0] = "TX"
    p = str(tmp_path / "c.npz")
    save_npz(cap, p)
    back = load_npz(p)
    assert back.samplerate == cap.samplerate and back.trigger_index == 123
    assert np.array_equal(back.data, cap.data) and back.channel_names[0] == "TX"
    cap.trigger_index = None
    save_npz(cap, p)
    assert load_npz(p).trigger_index is None


def test_npz_invalid(tmp_path):
    p = str(tmp_path / "x.npz")
    np.savez(p, foo=np.zeros(3))
    with pytest.raises(ValueError):
        load_npz(p)


def test_csv_export(tmp_path):
    d = np.array([0, 0, 1, 1, 0, 0, 0, 1], np.uint8)
    p = str(tmp_path / "o.csv")
    n = export_csv(Capture(1e6, d), p, channels=(0, 1))
    lines = open(p).read().splitlines()
    # righe: campione 0 e cambi a 2, 4, 7 (l'ultimo campione coincide con un cambio)
    assert lines[0] == "tempo_s,CH1,CH2" and n == 4 and len(lines) == 5
    assert lines[1] == "0,0,0" and lines[2] == "2e-06,1,0" and lines[-1] == "7e-06,1,0"


def test_vcd_export_roundtrip(tmp_path):
    cap = sim_capture(20_000)
    p = str(tmp_path / "o.vcd")
    n = export_vcd(cap, p, channels=(0, 7))
    txt = open(p).read()
    assert "$timescale 1 ns $end" in txt and "$var wire 1 ! CH1 $end" in txt and "$enddefinitions" in txt
    # rilettura: ricostruisce i fronti del canale 7 e li confronta
    times = {"!": [], '"': []}
    t = 0
    body = txt.split("$enddefinitions $end")[1].split("$end", 1)[1]
    for line in body.splitlines():
        if line.startswith("#"):
            t = int(line[1:])
        elif line and line[0] in "01" and line[1:] in times:
            times[line[1:]].append((t, int(line[0])))
    pwm = [(tt, v) for tt, v in times['"']]
    edges = cap.edges(7)
    assert len(pwm) == len(edges)
    assert [tt for tt, _ in pwm] == [int(round(i * 1e9 / FS)) for i in edges.tolist()]
    assert n == cap.edges(0).size + edges.size - np.count_nonzero(
        np.isin(cap.edges(0), edges))  # cambi contemporanei contati una volta


def test_export_bad_channels(tmp_path):
    with pytest.raises(ValueError):
        export_csv(sim_capture(100), str(tmp_path / "x.csv"), channels=(9,))
