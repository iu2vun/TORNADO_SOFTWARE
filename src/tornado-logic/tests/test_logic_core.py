import numpy as np
import pytest

from logic_core import (AcquisitionConfig, Capture, ConfigError, DeviceError, SimLogicDevice,
                        Trigger, TriggerTimeout, find_trigger)

FS = 24e6


def acquire(n=100_000, fs=FS, trigger=None, channels=tuple(range(8))):
    dev = SimLogicDevice(seed=1, random_phase=False)
    with dev:
        return dev.acquire(AcquisitionConfig(fs, n, channels, trigger))


# --- Capture -----------------------------------------------------------------
def test_capture_basics():
    cap = Capture(1e6, np.array([0, 1, 1, 0, 2, 2, 0], dtype=np.uint8))
    assert len(cap) == 7 and cap.duration == pytest.approx(7e-6)
    assert cap.channel(0).tolist() == [False, True, True, False, False, False, False]
    assert cap.edges(0).tolist() == [1, 3]
    assert cap.edges(0, falling=False).tolist() == [1]
    assert cap.edges(0, rising=False).tolist() == [3]
    assert cap.edges(1).tolist() == [4, 6]
    with pytest.raises(ValueError):
        cap.channel(8)


def test_capture_slice_trigger():
    cap = Capture(1e6, np.arange(10, dtype=np.uint8), trigger_index=5)
    s = cap.slice(3, 8)
    assert len(s) == 5 and s.trigger_index == 2
    assert cap.slice(6, 10).trigger_index is None


# --- trigger -----------------------------------------------------------------
def test_find_trigger_modes():
    d = np.array([0, 0, 1, 1, 0, 0, 1], dtype=np.uint8)
    assert find_trigger(d, Trigger(0, "rising")) == 2
    assert find_trigger(d, Trigger(0, "falling")) == 4
    assert find_trigger(d, Trigger(0, "either")) == 2
    assert find_trigger(d, Trigger(0, "high")) == 2
    assert find_trigger(d, Trigger(0, "low")) == 0
    assert find_trigger(d, Trigger(0, "rising"), start=3) == 6
    assert find_trigger(d, Trigger(0, "rising"), start=3, stop=6) is None
    assert find_trigger(np.zeros(5, np.uint8), Trigger(0, "rising")) is None


# --- segnali simulati --------------------------------------------------------
def test_pwm_1khz_duty30():
    cap = acquire()
    rise = cap.edges(7, falling=False)
    fall = cap.edges(7, rising=False)
    assert np.all(np.diff(rise) == 24000)  # 1 kHz a 24 MS/s
    assert fall[0] - rise[0] == 7200        # 30 % di 24000


def test_uart_first_frame():
    cap = acquire()
    fall = cap.edges(0, rising=False)
    rise = cap.edges(0, falling=False)
    bit = FS / 115200
    assert fall[0] == pytest.approx(20 * bit, abs=2)       # 20 bit di idle iniziali
    # 'H' = 0x48 = 0b01001000, LSB first: start + 3 zeri = 4 bit bassi consecutivi
    assert rise[0] - fall[0] == pytest.approx(4 * bit, abs=2)


def test_spi_first_transaction():
    cap = acquire()
    cs_fall = cap.edges(4, rising=False)[0]
    cs_rise = cap.edges(4, falling=False)[0]
    clk = cap.edges(1, falling=False)
    n_clk = np.count_nonzero((clk > cs_fall) & (clk < cs_rise))
    assert n_clk == 24  # 3 byte
    assert cap.channel(4)[0]


def test_i2c_start_stop():
    cap = acquire()
    scl, sda = cap.channel(5), cap.channel(6)
    sda_fall = cap.edges(6, rising=False)
    sda_rise = cap.edges(6, falling=False)
    start = sda_fall[scl[sda_fall]][0]   # SDA scende con SCL alto
    stop = sda_rise[scl[sda_rise]][0]    # SDA sale con SCL alto
    assert start < stop
    clk = cap.edges(5, falling=False)
    # 3 byte x 9 bit = 27 clock + 1 salita di SCL immediatamente prima dello STOP
    assert np.count_nonzero((clk > start) & (clk < stop)) == 28
    # SDA fissa dopo il START solo con SCL basso: nessun altro START/STOP nel mezzo
    inner = sda_fall[(sda_fall > start) & (sda_fall < stop)]
    assert not scl[inner].any()


# --- trigger sul dispositivo -------------------------------------------------
def test_trigger_pretrigger_alignment():
    cap = acquire(n=10_000, trigger=Trigger(0, "falling", pretrigger=0.1))
    t = cap.trigger_index
    assert t == 1000
    d = cap.channel(0)
    assert d[t - 1] and not d[t]


def test_trigger_random_phase_is_repeatable():
    dev = SimLogicDevice(seed=3, random_phase=True)
    with dev:
        cfg = AcquisitionConfig(FS, 20_000, trigger=Trigger(7, "rising", pretrigger=0.25))
        a, b = dev.acquire(cfg), dev.acquire(cfg)
    for cap in (a, b):
        assert cap.trigger_index == 5000
        assert not cap.channel(7)[4999] and cap.channel(7)[5000]


def test_trigger_timeout():
    class Flat(SimLogicDevice):
        def _render(self, length, fs, t0):
            return np.zeros(length, dtype=np.uint8)

    dev = Flat()
    with dev, pytest.raises(TriggerTimeout):
        dev.acquire(AcquisitionConfig(FS, 1000, trigger=Trigger(0, "rising")))


def test_channel_mask():
    cap = acquire(n=50_000, channels=(0, 7))
    assert np.all(cap.data & 0b01111110 == 0)
    assert cap.channel(7).any() and cap.channel(0).any()


def test_config_errors():
    dev = SimLogicDevice()
    with pytest.raises(DeviceError):  # non aperto
        dev.acquire(AcquisitionConfig(FS, 100))
    with dev:
        for cfg in (AcquisitionConfig(23e6, 100),
                    AcquisitionConfig(FS, 0),
                    AcquisitionConfig(FS, 100, channels=(0, 9)),
                    AcquisitionConfig(FS, 100, channels=(0,), trigger=Trigger(1))):
            with pytest.raises(ConfigError):
                dev.acquire(cfg)
    with pytest.raises(ConfigError):
        Trigger(0, "boh")
    with pytest.raises(ConfigError):
        Trigger(0, pretrigger=1.0)


def test_low_samplerate_runs():
    for fs in (20e3, 1e6, 24e6):
        cap = acquire(n=5000, fs=fs)
        assert len(cap) == 5000


def test_trigger_channel_and_mode_roundtrip(tmp_path):
    from logic_core import AcquisitionConfig, SimLogicDevice, Trigger, load_npz, save_npz
    dev = SimLogicDevice(seed=3)
    with dev:
        cap = dev.acquire(AcquisitionConfig(24e6, 200_000, trigger=Trigger(7, "falling", 0.2)))
    assert (cap.trigger_channel, cap.trigger_mode) == (7, "falling")
    save_npz(cap, str(tmp_path / "c.npz"))
    back = load_npz(str(tmp_path / "c.npz"))
    assert (back.trigger_channel, back.trigger_mode, back.trigger_index) == (7, "falling", cap.trigger_index)
    sub = cap.slice(cap.trigger_index - 100, cap.trigger_index + 100)
    assert (sub.trigger_channel, sub.trigger_mode) == (7, "falling")
    out = cap.slice(0, cap.trigger_index - 1)           # il trigger resta fuori: nessun marcatore
    assert out.trigger_index is None and out.trigger_channel is None and out.trigger_mode is None
    with dev:
        free = dev.acquire(AcquisitionConfig(24e6, 1000))
    assert free.trigger_channel is None and free.trigger_mode is None


def test_pinout_data():
    from logic_core import (DEFAULT_PINOUT, FUNCTIONS, WIRE_COLORS, channel_index, check_pinout, pin_position,
                            software_name)
    assert len(DEFAULT_PINOUT) == len(WIRE_COLORS) == 10 and check_pinout(DEFAULT_PINOUT) == []
    assert [pin_position(p) for p in (1, 2, 3, 10)] == [(0, 0), (0, 1), (1, 0), (4, 1)]
    with pytest.raises(ValueError):
        pin_position(11)
    assert software_name("CH0") == "CH1" and software_name("CH7") == "CH8" and software_name("GND") == "GND"
    assert channel_index("CH5") == 5 and channel_index("GND") is None and channel_index("CH9") is None
    assert "CH3 non assegnato" in check_pinout(("CH0", "CH1", "CH2", "NC", "CH4", "CH5", "CH6", "CH7", "GND", "GND"))
    assert any("più pin" in m for m in check_pinout(("CH0", "CH0", "CH2", "CH3", "CH4", "CH5", "CH6", "CH7", "GND", "GND")))
    assert any("nessun pin GND" in m for m in check_pinout(("CH0", "CH1", "CH2", "CH3", "CH4", "CH5", "CH6", "CH7", "NC", "NC")))
    assert any("sconosciuta" in m for m in check_pinout(("CH0", "CH1", "CH2", "CH3", "CH4", "CH5", "CH6", "CH7", "GND", "X")))
    assert check_pinout(("CH0",)) == ["servono 10 pin, ricevuti 1"] and "NC" in FUNCTIONS
