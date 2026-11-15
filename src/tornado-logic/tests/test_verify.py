import numpy as np
import pytest

from logic_core import AcquisitionConfig, Capture, SimLogicDevice
from logic_core.sim_signals import render_walk
from logic_core.verify import MUTE, OK, SHORT, STUCK_HIGH, SWAPPED, UNSURE, verify_walk, walk_summary

FS = 20e3


def walk(phase=0.0, seconds=6.0):
    return render_walk(int(FS * seconds), FS, phase)


def remap(data, mapping):
    """mapping[out] = canale dell'analizzatore a cui è collegata l'uscita out (0..7); None = scollegata."""
    out = np.zeros_like(data)
    for src, dst in mapping.items():
        if dst is not None:
            out |= (((data >> src) & 1) << dst).astype(np.uint8)
    return out


def cap(data):
    return Capture(FS, data)


@pytest.mark.parametrize("phase", [0.0, 0.3, 1.7, 2.45, 3.99])
def test_all_ok_any_phase(phase):
    res = verify_walk(cap(walk(phase)))
    assert [r.status for r in res] == [OK] * 8
    assert [r.source for r in res] == list(range(1, 9))
    assert all(abs(r.duration - 0.5) < 0.01 for r in res)
    assert walk_summary(res)[0]


def test_muted_channel_and_stuck_high():
    d = walk(1.1)
    d = remap(d, {i: (None if i == 2 else i) for i in range(8)})
    d[:] |= np.uint8(1 << 5)                       # CH6 sempre alto
    res = verify_walk(cap(d))
    assert res[2].status == MUTE and res[5].status == STUCK_HIGH
    assert [r.status for i, r in enumerate(res) if i not in (2, 5)] == [OK] * 6
    ok, text = walk_summary(res)
    assert not ok and "CH3" in text and "CH6" in text


def test_swapped_pair():
    m = {i: i for i in range(8)}
    m[3], m[4] = 4, 3                              # uscite 4 e 5 con i fili scambiati
    res = verify_walk(cap(remap(walk(0.8), m)))
    assert res[3].status == SWAPPED and res[4].status == SWAPPED
    assert res[3].source == 5 and res[4].source == 4 and res[3].partner == 4 and res[4].partner == 3
    assert "scambiato con CH5" in res[3].message
    assert [r.status for i, r in enumerate(res) if i not in (3, 4)] == [OK] * 6


def test_shifted_wires_are_swapped_not_ok():
    m = {i: (i + 1) % 8 for i in range(8)}        # tutti i fili slittati di uno
    res = verify_walk(cap(remap(walk(2.2), m)))
    assert all(r.status == OK for r in res) or all(r.status == SWAPPED for r in res)
    # slittamento uniforme: è indistinguibile da una fase diversa del giro; il test del tester resta il pinout


def test_short_between_two_channels():
    d = walk(0.4)
    both = (((d >> 1) | (d >> 6)) & 1).astype(np.uint8)        # uscite 2 e 7 in corto
    d = d & ~np.uint8((1 << 1) | (1 << 6))
    d |= both << 1
    d |= both << 6
    res = verify_walk(cap(d))
    assert res[1].status == SHORT and res[6].status == SHORT and res[1].partner == 6 and res[6].partner == 1
    assert [r.status for i, r in enumerate(res) if i not in (1, 6)] == [OK] * 6


def test_not_in_walk_mode_is_unsure():
    with SimLogicDevice(seed=1) as dev:
        c = dev.acquire(AcquisitionConfig(24e6, 1_000_000))
    c = Capture(FS, c.data[:120_000])                           # segnale "mixed" letto come se fossero 6 s a 20 kS/s
    res = verify_walk(c)
    assert sum(r.status == UNSURE for r in res) >= 4
    assert "mode walk" in walk_summary(res)[1]


def test_capture_too_short_and_channel_subset():
    with pytest.raises(ValueError, match="troppo corta"):
        verify_walk(cap(walk(0, seconds=3.0)))
    res = verify_walk(cap(walk(0.9)), channels=(0, 4))
    assert [r.channel for r in res] == [0, 4] and all(r.ok for r in res)


def test_sim_walk_scenario_roundtrip():
    with SimLogicDevice(scenario="walk", seed=3) as dev:
        c = dev.acquire(AcquisitionConfig(20e3, 120_000))
    assert dev.name.endswith("(walk)")
    assert all(r.ok for r in verify_walk(c))
