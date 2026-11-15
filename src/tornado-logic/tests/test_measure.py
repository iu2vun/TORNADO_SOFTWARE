import numpy as np
import pytest

from logic_core import (AcquisitionConfig, Capture, SimLogicDevice, find_glitches,
                        measure_channel, pulse_widths)

FS = 24e6


def sim_capture(n=240_000, fs=FS):
    dev = SimLogicDevice(seed=1, random_phase=False)
    with dev:
        return dev.acquire(AcquisitionConfig(fs, n))


def test_pwm_measure():
    st = measure_channel(sim_capture(), 7)
    assert st.frequency == pytest.approx(1000.0)
    assert st.period == pytest.approx(1e-3)
    assert st.duty == pytest.approx(0.30)
    assert st.period_pp == 0.0 and st.period_std == 0.0
    assert st.high_min == st.high_max == pytest.approx(300e-6)
    assert st.low_min == st.low_max == pytest.approx(700e-6)
    assert st.n_rising >= 9 and st.resolution == pytest.approx(1 / FS)


def test_spi_clock_frequency():
    st = measure_channel(sim_capture(), 1)
    # il periodo mediano è quello del clock a 1 MHz (50 % duty)
    assert st.frequency == pytest.approx(1e6, rel=1e-3)
    assert st.duty == pytest.approx(0.5, abs=0.01)


def test_i2c_scl_frequency():
    st = measure_channel(sim_capture(), 5)
    assert st.frequency == pytest.approx(100e3, rel=1e-3)


def test_constant_channel():
    cap = Capture(1e6, np.zeros(100, np.uint8))
    st = measure_channel(cap, 0)
    assert st.n_rising == st.n_falling == 0
    assert st.frequency is None and st.duty is None and st.high_min is None
    assert pulse_widths(cap, 0)[0].size == 0


def test_single_edge_has_no_complete_pulses():
    cap = Capture(1e6, np.array([0, 0, 1, 1, 1], np.uint8))
    hi, lo = pulse_widths(cap, 0)
    assert hi.size == lo.size == 0


def test_pulse_widths_known():
    d = np.array([0] * 3 + [1] * 5 + [0] * 2 + [1] * 4 + [0] * 6, dtype=np.uint8)
    hi, lo = pulse_widths(Capture(1e6, d), 0)
    assert hi.tolist() == pytest.approx([5e-6, 4e-6])
    assert lo.tolist() == pytest.approx([2e-6])


def test_jitter_detected():
    # periodi 10, 10, 12 campioni
    d = np.zeros(60, np.uint8)
    for r in (5, 15, 25, 37):
        d[r:r + 3] = 1
    st = measure_channel(Capture(1e6, d), 0)
    assert st.period == pytest.approx(10e-6)
    assert st.period_pp == pytest.approx(2e-6)


def test_find_glitches():
    cap = sim_capture()
    data = cap.data.copy()
    # glitch alto di 1 campione in una zona bassa del PWM e glitch basso in una zona alta
    low_pos, hi_pos = 20_000, 3_000   # PWM alto tra 2400 e 9600, basso dopo
    assert not (data[low_pos] >> 7) & 1 and (data[hi_pos] >> 7) & 1
    data[low_pos] |= 1 << 7
    data[hi_pos] &= ~np.uint8(1 << 7)
    g = find_glitches(Capture(FS, data), 7, 100e-9)
    assert sorted((x.index, x.level) for x in g) == [(hi_pos, 0), (low_pos, 1)]
    assert all(x.width == pytest.approx(1 / FS) for x in g)
    assert find_glitches(cap, 7, 100e-9) == []
