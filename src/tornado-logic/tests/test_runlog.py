import csv
from datetime import datetime

import numpy as np
import pytest

from logic_core import (AcquisitionConfig, Capture, DecoderConfig, LogOptions, RunLog, SimLogicDevice, Trigger,
                        load_npz)


def _cap(trigger=True):
    with SimLogicDevice() as d:
        return d.acquire(AcquisitionConfig(24e6, 1_000_000, trigger=Trigger(1, "rising", 0.1) if trigger else None))


def _rows(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f, delimiter=";"))


def test_options_validation(tmp_path):
    with pytest.raises(ValueError):
        RunLog(LogOptions(str(tmp_path), False, False, False))
    with pytest.raises(ValueError):
        RunLog(LogOptions(str(tmp_path), max_raw=-1))


def test_full_session_files_and_content(tmp_path):
    t0 = datetime(2026, 11, 15, 16, 40, 0)
    log = RunLog(LogOptions(str(tmp_path)), now=t0)
    assert log.dir.name == "Log_20261115_164000"
    cap = _cap()
    log.event("INFO", "Avvio")
    log.log_capture(1, t0, cap, DecoderConfig())
    log.miss(2, t0, "trigger non avvenuto")
    log.log_capture(3, t0, cap, DecoderConfig())
    log.event("INFO", "Fine")
    log.close()
    log.close()                                   # idempotente
    log.event("INFO", "dopo la chiusura")         # ignorato, nessuna eccezione

    ev = (log.dir / "eventi.log").read_text(encoding="utf-8").splitlines()
    assert ev[0].startswith("2026-") and "INFO" in ev[0] and ev[0].endswith("Avvio")
    assert any("Ciclo 1: 1000000 campioni a 24 MS/s" in l and "trigger a 4.167 ms" in l and "glitch" in l for l in ev)
    assert any("AVVISO" in l and "Ciclo 2" in l for l in ev) and ev[-1].endswith("Fine")
    assert "dopo la chiusura" not in "\n".join(ev)

    dec = _rows(log.dir / "decodifica.csv")
    assert tuple(dec[0]) == ("ciclo", "ora", "t_s", "decoder", "dato", "dettaglio", "errore")
    body = dec[1:]
    assert {r[0] for r in body} == {"1", "3"} and {r[3] for r in body} >= {"UART", "SPI MOSI", "I2C"}
    uart = [r for r in body if r[3] == "UART" and r[0] == "1"]
    assert "H" in "".join(r[4] for r in uart) and float(uart[0][2]) >= 0
    assert all(float(a[2]) <= float(b[2]) for a, b in zip(uart, uart[1:]))

    idx = _rows(log.dir / "cicli.csv")
    assert [r[0] for r in idx[1:]] == ["1", "3"] and idx[1][8] == "catture/cattura_000001.npz"
    back = load_npz(str(log.dir / idx[1][8]))
    assert np.array_equal(back.data, cap.data) and back.trigger_index == cap.trigger_index
    assert log.cycles == 2 and log.misses == 1


def test_only_selected_outputs(tmp_path):
    log = RunLog(LogOptions(str(tmp_path), decoded=False, raw=False, events=True))
    log.log_capture(1, datetime.now(), _cap(), DecoderConfig())
    log.close()
    names = sorted(p.name for p in log.dir.iterdir())
    assert names == ["cicli.csv", "eventi.log"]
    log2 = RunLog(LogOptions(str(tmp_path), decoded=True, raw=False, events=False))
    log2.event("INFO", "x")
    log2.log_capture(1, datetime.now(), _cap(), DecoderConfig())
    log2.close()
    assert sorted(p.name for p in log2.dir.iterdir()) == ["cicli.csv", "decodifica.csv"]


def test_raw_rotation_and_unique_folder(tmp_path):
    t0 = datetime(2026, 11, 15, 16, 40, 0)
    a = RunLog(LogOptions(str(tmp_path), decoded=False, events=False, max_raw=2), now=t0)
    b = RunLog(LogOptions(str(tmp_path), decoded=False, events=False), now=t0)
    assert a.dir != b.dir and b.dir.name.endswith("_2")
    small = Capture(1e6, np.zeros(100, dtype=np.uint8))
    for n in range(1, 5):
        a.log_capture(n, t0, small, DecoderConfig(), (0,))
    a.close()
    b.close()
    assert sorted(p.name for p in (a.dir / "catture").iterdir()) == ["cattura_000003.npz", "cattura_000004.npz"]
    assert len(_rows(a.dir / "cicli.csv")) == 5      # l'indice resta completo anche dopo la rotazione


def test_error_annotations_counted(tmp_path):
    from logic_core import UartCfg
    # linea UART sempre a 0: errore di framing a ogni byte
    data = np.zeros(200_000, dtype=np.uint8)
    cap = Capture(24e6, data)
    cfg = DecoderConfig(uart=UartCfg(ch=0), )
    log = RunLog(LogOptions(str(tmp_path), raw=False))
    log.log_capture(1, datetime.now(), cap, cfg, (0,))
    log.close()
    row = _rows(log.dir / "cicli.csv")[1]
    assert int(row[5]) >= int(row[6])


def test_events_include_timings_and_retries(tmp_path):
    cap = Capture(1e6, np.zeros(1000, dtype=np.uint8))
    cap.meta = {"seconds": 2.41, "attempts": 3, "notes": ["Failed to open device", "codice 1"]}
    log = RunLog(LogOptions(str(tmp_path), decoded=False, raw=False))
    log.log_capture(7, datetime.now(), cap, DecoderConfig(), (0,))
    log.note(7, "attesa lunga")
    log.close()
    ev = (log.dir / "eventi.log").read_text(encoding="utf-8")
    assert "acquisizione 2.41 s" in ev and "analisi " in ev
    assert "3 tentativi (Failed to open device; codice 1)" in ev and "Ciclo 7: attesa lunga" in ev


def test_events_report_sigrok_trigger_and_fallback(tmp_path):
    cap = Capture(1e6, np.zeros(1000, dtype=np.uint8))
    cap.meta = {"seconds": 0.5, "attempts": 1, "notes": [], "trigger": "sigrok"}
    log = RunLog(LogOptions(str(tmp_path), decoded=False, raw=False))
    log.log_capture(1, datetime.now(), cap, DecoderConfig(), (0,))
    cap.meta = {"seconds": 0.5, "attempts": 1, "trigger": "software (ripiego)",
                "notes": ["trigger sigrok non riuscito (Invalid trigger): uso il trigger software"]}
    log.log_capture(2, datetime.now(), cap, DecoderConfig(), (0,))
    log.close()
    ev = (log.dir / "eventi.log").read_text(encoding="utf-8")
    assert "Ciclo 1:" in ev and "trigger sigrok" in ev
    assert "AVVISO" in ev and "Ciclo 2: trigger sigrok non riuscito (Invalid trigger)" in ev
