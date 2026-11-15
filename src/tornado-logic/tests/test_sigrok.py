"""Backend sigrok: provato con un sigrok-cli finto (script) che imita --scan e la cattura binaria."""
import stat
import sys
import textwrap

import numpy as np
import pytest

from logic_core import (AcquisitionConfig, DeviceError, SigrokLogicDevice, Trigger, TriggerTimeout)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="usa uno script shell come sigrok-cli finto")

FAKE = textwrap.dedent('''\
    #!/usr/bin/env python3
    import sys, os
    a = sys.argv[1:]
    mode = os.environ.get("FAKE_MODE", "ok")
    st = os.environ.get("FAKE_STATE")
    if st and mode.startswith("flaky"):      # i primi FAKE_FAILS richiami (scan o cattura) falliscono, poi va tutto bene
        k = int(open(st).read() or 0) if os.path.exists(st) else 0
        open(st, "w").write(str(k + 1))
        if k < int(os.environ.get("FAKE_FAILS", "2")) and (("--scan" in a) == (mode == "flaky_scan")):
            if "--scan" in a:
                print("The following devices were found:")
            else:
                print("sr: fx2lafw: Failed to open device", file=sys.stderr); sys.exit(1)
            sys.exit(0)
    if "--scan" in a:
        print("sr: asix-omega-rtm-cli: Cannot execute RTM CLI process: Failed to execute helper program")
        print("The following devices were found:")
        if mode != "nodev":
            print("fx2lafw - Saleae Logic with 8 channels: D0 D1 D2 D3 D4 D5 D6 D7")
        sys.exit(0)
    if mode == "fail":
        print("sr: fx2lafw: Failed to open device", file=sys.stderr); sys.exit(1)
    n = int(a[a.index("--samples") + 1])
    out = a[a.index("-o") + 1]
    conf = dict(kv.split("=") for kv in a[a.index("--config") + 1].split(":"))
    fs = int(conf["samplerate"])
    if "--triggers" in a:
        open(os.environ.get("FAKE_ARGS", os.devnull), "w").write(" ".join(a))
        if mode == "hwtrig_unsup":
            print("sr: Error: Invalid trigger specification", file=sys.stderr); sys.exit(1)
        if mode == "hwtrig_none":
            import time; time.sleep(30); sys.exit(0)
        pre = n * int(conf["captureratio"]) // 100           # il trigger cade a captureratio % del file
        buf = bytearray(n)
        for i in range(n):
            buf[i] = (0x80 | (1 if i >= pre else 0)) if i >= pre // 2 - 5 else 0x80
            if pre // 2 - 5 <= i < pre // 2 + 5:             # un fronte di salita anticipato (ma poi torna basso)
                buf[i] = 0x81
        open(out, "wb").write(bytes(buf))
        sys.exit(0)
    # D0 = onda quadra con periodo 8 campioni a partire da 20 campioni (fronte di salita al campione 20), D7 = sempre 1
    d = np.zeros(n, dtype=np.uint8) if False else None
    import array
    buf = bytearray(n)
    for i in range(n):
        b = 0x80
        if i >= 20 and ((i - 20) // 4) % 2 == 0:
            b |= 1
        buf[i] = b
    open(out, "wb").write(bytes(buf))
''')


def _patch_sleep(monkeypatch, fn):
    """Sostituisce solo il `time` visto da sigrok.py: patchare time.sleep globale cattura anche i thread dei test GUI."""
    import time as _t
    from types import SimpleNamespace
    monkeypatch.setattr("logic_core.sigrok.time", SimpleNamespace(sleep=fn, perf_counter=_t.perf_counter))


@pytest.fixture
def fake(tmp_path, monkeypatch):
    p = tmp_path / "sigrok-cli"
    p.write_text(FAKE.replace("import sys, os", "import sys, os"))
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("TORNADO_SIGROK_CLI", str(p))
    return p


def test_open_not_found(monkeypatch):
    monkeypatch.setenv("TORNADO_SIGROK_CLI", "/non/esiste")
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(DeviceError, match="sigrok-cli non trovato"):
        SigrokLogicDevice().open()


def test_open_no_device(fake, monkeypatch):
    _patch_sleep(monkeypatch, lambda s: None)
    monkeypatch.setenv("FAKE_MODE", "nodev")
    with pytest.raises(DeviceError, match="non trovato"):
        SigrokLogicDevice().open()


def test_info_and_rates(fake):
    with SigrokLogicDevice() as d:
        i = d.info()
        assert i["channels"] == 8 and i["max_samplerate"] == 24e6 and "fx2lafw" in i["device"]
        assert max(d.supported_samplerates()) == 24e6


def test_acquire_plain(fake):
    with SigrokLogicDevice() as d:
        cap = d.acquire(AcquisitionConfig(samplerate=1e6, n_samples=100))
    assert len(cap) == 100 and cap.trigger_index is None
    assert np.all(cap.channel(7) == 1)               # D7 sempre alto
    assert cap.channel(0)[19] == 0 and cap.channel(0)[20] == 1 and cap.channel(0)[24] == 0


def test_channel_mask(fake):
    with SigrokLogicDevice() as d:
        cap = d.acquire(AcquisitionConfig(samplerate=1e6, n_samples=50, channels=(0,)))
    assert not np.any(cap.channel(7))                # canali non abilitati azzerati


def test_acquire_trigger_rising(fake):
    cfg = AcquisitionConfig(samplerate=1e6, n_samples=40, trigger=Trigger(0, "rising", pretrigger=0.25))
    with SigrokLogicDevice() as d:
        cap = d.acquire(cfg)
    assert cap.trigger_index == 10 and cap.trigger_channel == 0 and cap.trigger_mode == "rising"
    assert cap.channel(0)[cap.trigger_index] == 1 and cap.channel(0)[cap.trigger_index - 1] == 0


def test_trigger_timeout(fake):
    cfg = AcquisitionConfig(samplerate=1e6, n_samples=40, trigger=Trigger(3, "rising"))   # CH4 sempre 0
    with SigrokLogicDevice() as d:
        with pytest.raises(TriggerTimeout):
            d.acquire(cfg)


def test_capture_failure_reports_stderr(fake, monkeypatch):
    with SigrokLogicDevice() as d:
        monkeypatch.setenv("FAKE_MODE", "fail")
        _patch_sleep(monkeypatch, lambda s: None)
        with pytest.raises(DeviceError, match="Failed to open device"):
            d.acquire(AcquisitionConfig(samplerate=1e6, n_samples=10))


def test_invalid_rate_rejected(fake):
    with SigrokLogicDevice() as d:
        with pytest.raises(DeviceError):
            d.acquire(AcquisitionConfig(samplerate=48e6, n_samples=10))


def test_capture_retries_then_succeeds_and_reports_stats(fake, monkeypatch, tmp_path):
    _patch_sleep(monkeypatch, lambda s: None)
    with SigrokLogicDevice() as d:
        monkeypatch.setenv("FAKE_MODE", "flaky")
        monkeypatch.setenv("FAKE_STATE", str(tmp_path / "n"))
        monkeypatch.setenv("FAKE_FAILS", "2")
        cap = d.acquire(AcquisitionConfig(samplerate=1e6, n_samples=40))
    assert len(cap) == 40 and cap.meta["attempts"] == 3 and len(cap.meta["notes"]) == 2
    assert "Failed to open device" in cap.meta["notes"][0] and cap.meta["seconds"] >= 0


def test_capture_gives_up_after_all_retries(fake, monkeypatch, tmp_path):
    sleeps = []
    _patch_sleep(monkeypatch, sleeps.append)
    with SigrokLogicDevice() as d:
        monkeypatch.setenv("FAKE_MODE", "flaky")
        monkeypatch.setenv("FAKE_STATE", str(tmp_path / "n"))
        monkeypatch.setenv("FAKE_FAILS", "99")
        with pytest.raises(DeviceError, match="dopo 4 tentativi"):
            d.acquire(AcquisitionConfig(samplerate=1e6, n_samples=10))
    assert sleeps == [0.3, 0.7, 1.5] and d.stats["attempts"] == 4


def test_open_retries_scan(fake, monkeypatch, tmp_path):
    sleeps = []
    _patch_sleep(monkeypatch, sleeps.append)
    monkeypatch.setenv("FAKE_MODE", "flaky_scan")
    monkeypatch.setenv("FAKE_STATE", str(tmp_path / "n"))
    monkeypatch.setenv("FAKE_FAILS", "2")
    d = SigrokLogicDevice()
    d.open()                                   # il dispositivo compare solo al terzo --scan
    assert d.is_open and sleeps == [1.0, 1.0]


def _hw_cfg(n=1000, pre=0.3, **kw):
    return AcquisitionConfig(1e6, n, trigger=Trigger(0, "rising", pre), hw_trigger=True, **kw)


def test_hw_trigger_passes_sigrok_arguments_and_places_trigger(fake, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_MODE", "hwtrig")
    monkeypatch.setenv("FAKE_ARGS", str(tmp_path / "args"))
    with SigrokLogicDevice() as d:
        cap = d.acquire(_hw_cfg())
    args = (tmp_path / "args").read_text()
    assert "--triggers D0=r" in args and "--wait-trigger" in args and "samplerate=1000000:captureratio=30" in args
    assert "--samples 1000" in args                      # niente finestra doppia
    assert len(cap) == 1000 and cap.trigger_index == 300 and cap.meta["trigger"] == "sigrok"
    assert cap.trigger_channel == 0 and cap.trigger_mode == "rising"


@pytest.mark.parametrize("mode,code", [("rising", "r"), ("falling", "f"), ("either", "e"), ("high", "1"), ("low", "0")])
def test_hw_trigger_condition_codes(fake, monkeypatch, tmp_path, mode, code):
    monkeypatch.setenv("FAKE_MODE", "hwtrig")
    monkeypatch.setenv("FAKE_ARGS", str(tmp_path / "args"))
    with SigrokLogicDevice() as d:
        d.acquire(AcquisitionConfig(1e6, 500, trigger=Trigger(3, mode, 0.1), hw_trigger=True))
    assert f"--triggers D3={code}" in (tmp_path / "args").read_text()


def test_hw_trigger_falls_back_to_software_when_rejected(fake, monkeypatch):
    _patch_sleep(monkeypatch, lambda s: None)
    monkeypatch.setenv("FAKE_MODE", "hwtrig_unsup")
    # dopo il rifiuto il finto sigrok-cli genera la cattura normale (senza --triggers): rilancio con modo ok
    from logic_core import sigrok as sg
    real = sg.SigrokLogicDevice._run_capture

    def run(self, fs, n, timeout, trigger=None):
        if trigger is None:
            monkeypatch.setenv("FAKE_MODE", "ok")
        return real(self, fs, n, timeout, trigger)

    monkeypatch.setattr(sg.SigrokLogicDevice, "_run_capture", run)
    with SigrokLogicDevice() as d:
        cap = d.acquire(_hw_cfg(n=100, pre=0.1))
    assert cap.meta["trigger"] == "software (ripiego)" and "Invalid trigger" in cap.meta["notes"][0]
    assert cap.trigger_index == 10 and len(cap) == 100


def test_hw_trigger_timeout_is_trigger_timeout_not_error(fake, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "hwtrig_none")
    with SigrokLogicDevice() as d:
        with pytest.raises(TriggerTimeout):
            d.acquire(_hw_cfg(n=100, timeout_s=-9.5))


def test_software_trigger_unchanged_without_hw_flag(fake, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_MODE", "ok")
    with SigrokLogicDevice() as d:
        cap = d.acquire(AcquisitionConfig(1e6, 40, trigger=Trigger(0, "rising", 0.1)))
    assert cap.trigger_index == 4 and "trigger" not in cap.meta
