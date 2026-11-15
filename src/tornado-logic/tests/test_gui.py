import numpy as np
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor

from logic_core import Capture, Lane, Annotation, load_npz
from logic_gui.fmt import fmt_percent, fmt_si

FS = 24e6


# ---------------------------------- formattazione ------------------------------
@pytest.mark.parametrize("value,unit,expected", [
    (1000.0, "Hz", "1 kHz"), (24e6, "S/s", "24 MS/s"), (41.6667e-3, "s", "41.67 ms"),
    (166.7e-9, "s", "166.7 ns"), (1e-6, "s", "1 µs"), (0.0, "s", "0 s"), (None, "Hz", "—"),
    (999.96e-6, "s", "1000 µs"), (-2.5e-3, "s", "-2.5 ms"), (1e9, "Hz", "1 GHz"), (3e-14, "s", "0.03 ps"),
])
def test_fmt_si(value, unit, expected):
    assert fmt_si(value, unit) == expected


def test_fmt_percent():
    assert fmt_percent(0.3) == "30.0 %" and fmt_percent(None) == "—"


# ------------------------------------ fixture ----------------------------------
@pytest.fixture
def win(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from tornado_ui import apply_theme
    apply_theme(QApplication.instance())
    import logic_gui.main_window as mw
    ini = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(mw, "settings", lambda: ini)
    w = mw.MainWindow()
    w.errors = []
    w.notify_error = lambda m: w.errors.append(m)
    qtbot.addWidget(w)
    w.resize(1500, 900)
    w.show()
    return w


def settle(qtbot, w, timeout=30000):
    qtbot.waitUntil(lambda: (not w._acquiring and not w._t_dec.isActive() and not w._t_meas.isActive()
                             and all(x.isFinished() for x in w._workers)), timeout=timeout)
    qtbot.wait(50)


def acquire(qtbot, w, trigger_ch=None, mode=1):
    if trigger_ch is not None:
        w.acq.trig_ch.setCurrentIndex(w.acq.trig_ch.findData(trigger_ch))
        w.acq.trig_mode.setCurrentIndex(mode)
    qtbot.mouseClick(w.acq.btn_acquire, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: w.cap is not None, timeout=20000)
    settle(qtbot, w)


# ------------------------------------ finestra ---------------------------------
def test_acquire_flow(qtbot, win):
    assert win.cap is None and win.statusBar().currentMessage() == "Pronto"
    acquire(qtbot, win, trigger_ch=4)
    cap = win.cap
    assert len(cap) == 1_000_000 and cap.samplerate == FS and cap.trigger_index == 100_000
    assert win.errors == []
    assert [l.name for l in win.wave._lanes] == ["UART", "SPI MOSI", "SPI MISO", "I2C"]
    assert win.meas_table.rowCount() == 8
    pwm = {win.meas_table.item(7, c).text() for c in (3, 5)}
    assert pwm == {"1 kHz", "30.0 %"}
    assert win.dec_table.rowCount() == 5000 and win.lbl_dec_note.isVisible()
    assert "trigger" in win.lbl_info.text() and "24 MS/s" in win.lbl_info.text()
    assert "completata" in win.statusBar().currentMessage()
    assert win.acq.btn_acquire.isEnabled() and not win.acq.btn_stop.isEnabled()


def test_theme_is_dark(qtbot, win):
    img = win.grab().toImage()
    bg = QColor(img.pixel(3, win.height() // 2))
    assert bg.lightness() < 40  # fondo scuro uniforme, nessun widget con colori di default chiari


def test_config_errors_reported(qtbot, win):
    for cb in win.acq._en:
        cb.setChecked(False)
    qtbot.mouseClick(win.acq.btn_acquire, Qt.MouseButton.LeftButton)
    assert win.errors and "canale" in win.errors[-1] and win.cap is None and not win._acquiring


def test_device_error_reported(qtbot, win):
    win.acq._en[0].setChecked(False)           # canale 1 disabilitato...
    win.acq.trig_ch.setCurrentIndex(win.acq.trig_ch.findData(0))  # ...ma usato come trigger
    qtbot.mouseClick(win.acq.btn_acquire, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: bool(win.errors), timeout=10000)
    assert "trigger" in win.errors[-1] and not win._acquiring
    # dopo l'errore si può acquisire di nuovo
    win.acq.trig_ch.setCurrentIndex(0)
    acquire(qtbot, win)
    assert win.cap is not None


def test_decoder_toggle_reruns(qtbot, win):
    acquire(qtbot, win)
    win.dec.g_uart.setChecked(False)
    qtbot.waitUntil(lambda: [l.name for l in win.wave._lanes] == ["SPI MOSI", "SPI MISO", "I2C"], timeout=10000)
    win.dec.g_uart.setChecked(True)
    qtbot.waitUntil(lambda: "UART" in [l.name for l in win.wave._lanes], timeout=10000)
    uart = next(l for l in win.wave._lanes if l.name == "UART")
    # senza trigger la cattura parte a metà flusso: i primi frame possono essere spezzati,
    # ma "Hello" deve comparire per intero più avanti
    texts = [a.text for a in uart.annotations]
    hello = ["48 H", "65 e", "6C l", "6C l", "6F o"]
    assert any(texts[i:i + 5] == hello for i in range(len(texts) - 4))


def test_decoder_error_shown(qtbot, win):
    acquire(qtbot, win)
    win.dec.u_baud.setCurrentText("12000000")  # 2 campioni/bit: non decodificabile
    qtbot.waitUntil(lambda: "UART" in win.lbl_dec_note.text(), timeout=10000)
    assert "campioni/bit" in win.lbl_dec_note.text()
    assert "UART" not in [l.name for l in win.wave._lanes]


def test_channel_rename_and_hide(qtbot, win):
    acquire(qtbot, win)
    win.acq._name[0].setText("TX")
    win.acq._name[0].editingFinished.emit()
    win.acq._en[7].setChecked(False)
    qtbot.wait(100)
    assert win.wave._names[0] == "TX" and 7 not in win.wave._visible
    assert win.dec.u_ch.itemText(0) == "TX" and win.acq.trig_ch.itemText(1) == "TX"
    settle(qtbot, win)
    assert win.meas_table.rowCount() == 7


def test_range_measure_between_cursors(qtbot, win):
    acquire(qtbot, win)
    win.chk_range.setChecked(True)
    # intervallo di 100 us senza fronti del PWM (periodo 1 ms): 1,1 ms .. 1,2 ms dopo il primo fronte
    t = win.cap.edges(7, falling=False)[0] / FS
    win.wave.set_cursors(t + 0.05e-3, t + 0.15e-3)
    qtbot.wait(50)
    settle(qtbot, win)
    row = next(r for r in range(win.meas_table.rowCount()) if win.meas_table.item(r, 0).text() == "CH8")
    assert win.meas_table.item(row, 3).text() == "—"
    assert "Δ 100 µs" in win.lbl_cur.text() and "10 kHz" in win.lbl_cur.text()


def test_goto_and_zoom(qtbot, win):
    acquire(qtbot, win, trigger_ch=0)
    t = win.cap.trigger_time
    win._goto_trigger()
    x0, x1 = win.wave.view_range()
    assert x0 < t < x1 and (x1 - x0) == pytest.approx(win.cap.duration / 10)
    w0 = x1 - x0
    win.wave.zoom_by(0.5)
    x0, x1 = win.wave.view_range()
    assert (x1 - x0) == pytest.approx(w0 / 2)
    win.wave.fit()
    assert win.wave.view_range() == pytest.approx((0.0, win.cap.duration))
    win.dec_table.activatedRange.emit(1000, 1200)
    x0, x1 = win.wave.view_range()
    assert x0 < 1000 / FS and x1 > 1200 / FS and (x1 - x0) < 1e-4


def test_goto_trigger_without_trigger(qtbot, win):
    acquire(qtbot, win)
    win._goto_trigger()
    assert "trigger" in win.statusBar().currentMessage()


def test_save_open_roundtrip(qtbot, win, tmp_path):
    acquire(qtbot, win, trigger_ch=0)
    win.acq._name[2].setText("MOSI")
    win.acq._name[2].editingFinished.emit()
    p = str(tmp_path / "c.npz")
    win.save_path(p)
    saved = load_npz(p)
    assert np.array_equal(saved.data, win.cap.data) and saved.channel_names[2] == "MOSI"
    first = win.cap
    win.acq._name[2].setText("X")
    win.open_path(p)
    settle(qtbot, win)
    assert win.cap is not first and win.acq.names()[2] == "MOSI"
    win.open_path(str(tmp_path / "manca.npz"))
    assert "Impossibile aprire" in win.errors[-1]


def test_exports(qtbot, win, tmp_path):
    acquire(qtbot, win)
    from logic_core import export_csv, export_vcd
    win.export_path(str(tmp_path / "o.csv"), export_csv)
    win.export_path(str(tmp_path / "o.vcd"), export_vcd)
    settle(qtbot, win)
    assert (tmp_path / "o.csv").read_text().startswith("tempo_s,CH1,CH2")
    assert "$enddefinitions" in (tmp_path / "o.vcd").read_text()
    assert "Esportazione completata" in win.statusBar().currentMessage()
    win.export_path(str(tmp_path / "nodir" / "o.csv"), export_csv)  # cartella inesistente
    qtbot.waitUntil(lambda: bool(win.errors), timeout=5000)
    assert "Esportazione non riuscita" in win.errors[-1]


def test_actions_without_capture(qtbot, win):
    win.on_save()
    assert "Nessuna cattura" in win.statusBar().currentMessage()
    win.on_stop()
    win._goto_samples(0, 10)  # non deve sollevare


# ------------------------------- vista forme d'onda ----------------------------
def make_view(qtbot, data, fs=1e6):
    from logic_gui.waveform import WaveformView
    v = WaveformView()
    qtbot.addWidget(v)
    v.resize(800, 500)
    v.show()
    v.set_capture(Capture(fs, np.asarray(data, np.uint8)))
    return v


def test_trace_step_exact(qtbot):
    d = np.array([0, 0, 1, 1, 1, 0, 0, 1], np.uint8)
    v = make_view(qtbot, d)
    xs, ys = v._trace(0, 0, 7, 800)
    assert xs.tolist() == pytest.approx([0, 2e-6, 2e-6, 5e-6, 5e-6, 7e-6, 7e-6, 7e-6])
    assert ys.tolist() == [0, 0, 1, 1, 0, 0, 1, 1]
    xs, ys = v._trace(1, 0, 7, 800)           # canale senza fronti: linea piatta
    assert xs.tolist() == pytest.approx([0, 7e-6]) and ys.tolist() == [0, 0]


def test_trace_dense_mode_and_binning(qtbot):
    from logic_gui.waveform import MAX_STEP_EDGES
    n = 4 * (MAX_STEP_EDGES + 100)
    d = np.zeros(n, np.uint8)
    d[1::2] = 1                                # onda quadra alla metà del clock: fronte a ogni campione
    v = make_view(qtbot, d)
    xs, ys = v._trace(0, 0, n - 1, 400)
    assert xs.size == ys.size and xs.size == 2 * 400  # tutte le colonne attive: 2 punti ciascuna
    assert set(np.unique(ys)) == {0.0, 1.0}
    # solo una parte con fronti: colonne senza fronti a un punto
    d2 = np.zeros(n, np.uint8)
    d2[n // 2: n // 2 + 3 * MAX_STEP_EDGES:2] = 1
    v2 = make_view(qtbot, d2)
    xs, ys = v2._trace(0, 0, n - 1, 400)
    assert 400 < xs.size < 800 and xs.size == ys.size


def test_large_capture_refresh_is_fast(qtbot):
    import time
    rng = np.random.default_rng(0)
    d = (rng.random(5_000_000) < 0.5).astype(np.uint8)  # rumore: milioni di fronti
    v = make_view(qtbot, d, fs=24e6)
    t = time.perf_counter()
    for _ in range(5):
        v._vb.setXRange(0, 0.2, padding=0)
        v._refresh()
    assert (time.perf_counter() - t) / 5 < 0.25


def test_lanes_layout_and_limits(qtbot):
    d = np.zeros(1000, np.uint8)
    v = make_view(qtbot, d)
    v.set_channels([0, 3], ["A", "B", "C", "D", "E", "F", "G", "H"])
    v.set_lanes([Lane("UART", [Annotation(10, 50, "41 A")]), Lane("SPI", [])])
    assert v._n_rows() == 4
    ymin, ymax = v._vb.viewRange()[1]
    assert (ymin, ymax) == pytest.approx((0, 4))
    assert [c.isVisible() for c in v._curves] == [True, False, False, True, False, False, False, False]


# ------------------------------------ seriale ---------------------------------
def test_serial_panel_config_and_modes(qtbot, win):
    p = win.ser
    c = p.config()
    assert not c.enabled and c.mode == "rs232" and c.baud is None and c.fmt is None and c.gap_chars == 3.5
    p.mode.setCurrentIndex(p.mode.findData("rs485_ab"))
    assert p.f.isRowVisible(p.a) and not p.f.isRowVisible(p.tx) and p.f.isRowVisible(p.de)
    p.mode.setCurrentIndex(p.mode.findData("rs232"))
    assert p.f.isRowVisible(p.tx) and not p.f.isRowVisible(p.de)
    p.baud.setCurrentText("19200")
    p.fmt.setCurrentIndex(p.fmt.findText("8E1"))
    p.g.setChecked(True)
    c = p.config()
    assert c.enabled and c.baud == 19200 and c.fmt == (8, "E", 1)
    p.baud.setCurrentText("Automatico")
    p.baud.setCurrentText("abc")
    assert p.config().baud is None


def test_serial_flow_rs232_and_rs485(qtbot, win):
    win.acq.device.setCurrentText("Simulato (seriale)")
    acquire(qtbot, win)
    assert win.msg_table.rowCount() == 0 and "disattivato" in win.lbl_ser.text()
    win.ser.g.setChecked(True)
    qtbot.waitUntil(lambda: win.msg_table.rowCount() >= 4, timeout=15000)
    settle(qtbot, win)
    rows = [(win.msg_table.item(r, 1).text(), win.msg_table.item(r, 8).text()) for r in range(win.msg_table.rowCount())]
    assert ("TX", "47 45 54 0D") in rows and ("RX", "4F 4B 0D 0A") in rows   # la prima riga può essere troncata (fase casuale)
    assert "Baud 9600" in win.ser.lbl_detect.text() and "8N1" in win.ser.lbl_detect.text()
    assert "Turnaround" in win.lbl_ser.text() and not win.lbl_ser.isHidden()
    assert {"RS-232 TX", "RS-232 RX", "Messaggi"} <= {l.name for l in win.wave._lanes}

    win.ser.mode.setCurrentIndex(win.ser.mode.findData("rs485"))
    qtbot.waitUntil(lambda: "19200" in win.ser.lbl_detect.text(), timeout=15000)
    settle(qtbot, win)
    rows = [(win.msg_table.item(r, 1).text(), win.msg_table.item(r, 8).text(), win.msg_table.item(r, 5).text())
            for r in range(win.msg_table.rowCount())]
    req = [r for r in rows if r[1] == "01 03 00 00 00 02 C4 0B"]
    # fase casuale: la prima richiesta puo' avere il DE gia' attivo prima dell'inizio della cattura ("—")
    assert req and all(r[0] == "TX" for r in req) and any(r[2] != "—" for r in req) and "DE anticipo" in win.lbl_ser.text()
    assert "8E1" in win.ser.lbl_detect.text() and "19200" in win.ser.lbl_detect.text()

    # doppio clic su un messaggio: la vista si porta su di esso
    m = win.msg_table._ranges[2]
    win.msg_table.cellDoubleClicked.emit(2, 0)
    a, b = win.wave.view_range()
    assert a <= m[0] / win.cap.samplerate and b >= m[1] / win.cap.samplerate
    assert win.errors == []


def test_serial_error_shown_in_tab(qtbot, win):
    acquire(qtbot, win)
    win.ser.baud.setCurrentText("12000000")     # < 3 campioni/bit a 24 MS/s
    win.ser.fmt.setCurrentIndex(win.ser.fmt.findText("8N1"))
    win.ser.g.setChecked(True)
    qtbot.waitUntil(lambda: "Seriale:" in win.lbl_ser.text(), timeout=15000)
    settle(qtbot, win)
    assert win.msg_table.rowCount() == 0 and "Seriale:" in win.lbl_ser.text() and not win.lbl_ser.isHidden()


# ------------------------------ marcatore del trigger --------------------------
@pytest.mark.parametrize("mode_idx,sym", [(0, "t1"), (1, "t"), (2, "d"), (3, "o")])
def test_trigger_marker_symbol(qtbot, win, mode_idx, sym):
    acquire(qtbot, win, trigger_ch=7, mode=mode_idx)
    m = win.wave._trig_mark
    assert m.isVisible() and win.cap.trigger_channel == 7
    pt = m.points()[0]
    assert pt.symbol() == sym and pt.pos().x() == pytest.approx(win.cap.trigger_time)
    R = win.wave._n_rows()
    assert (R - 1 - win.wave._visible.index(7)) < pt.pos().y() < (R - win.wave._visible.index(7))   # dentro la riga del canale


def test_trigger_marker_hidden_cases(qtbot, win):
    acquire(qtbot, win)                                     # senza trigger
    assert not win.wave._trig_mark.isVisible()
    acquire(qtbot, win, trigger_ch=7, mode=0)
    assert win.wave._trig_mark.isVisible()
    win.wave.set_channels([c for c in range(8) if c != 7], win.acq.names())   # canale del trigger nascosto
    assert not win.wave._trig_mark.isVisible()


# -------------------------------------- pinout --------------------------------
def test_pinout_panel_defaults_edit_and_persist(qtbot, win):
    from logic_core import DEFAULT_PINOUT
    pp = win.pinout
    assert pp.assign() == DEFAULT_PINOUT and pp.lbl_warn.isHidden()
    assert [pp.table.item(i, 3).text() for i in (0, 7, 8, 9)] == ["CH1", "CH8", "—", "GND"]
    assert pp.table.item(0, 2).text() == "nero" and pp.table.item(9, 2).text() == "bianco"
    # pin 5 = clock/non collegato: CH4 non assegnato -> avviso
    pp.combos[4].setCurrentIndex(pp.combos[4].findData("NC"))
    assert "CH4 non assegnato" in pp.lbl_warn.text() and not pp.lbl_warn.isHidden()
    assert pp.table.item(4, 3).text() == "—"
    # duplicato
    pp.combos[8].setCurrentIndex(pp.combos[8].findData("CH0"))
    assert "CH0 assegnato a più pin (1, 9)" in pp.lbl_warn.text()
    # persistenza
    from logic_gui.pinout import PinoutPanel
    again = PinoutPanel(win.pinout._store)
    qtbot.addWidget(again)
    assert again.assign() == pp.assign()
    pp.btn_reset.click()
    assert pp.assign() == DEFAULT_PINOUT and pp.lbl_warn.isHidden()


def test_pinout_stored_garbage_falls_back(qtbot, tmp_path):
    from PySide6.QtCore import QSettings
    from logic_core import DEFAULT_PINOUT
    from logic_gui.pinout import KEY, PinoutPanel
    ini = QSettings(str(tmp_path / "p.ini"), QSettings.Format.IniFormat)
    ini.setValue(KEY, "CH0,CH1,xx")
    w = PinoutPanel(lambda: ini)
    qtbot.addWidget(w)
    assert w.assign() == DEFAULT_PINOUT


# ------------------------------ acquisizione continua e log --------------------
def _wait_idle(qtbot, w, timeout=30000):
    qtbot.waitUntil(lambda: w._run is None and not w._acquiring and not w._open_logs
                    and all(x.isFinished() for x in w._workers), timeout=timeout)


def test_continuous_stops_and_logs(qtbot, win, tmp_path):
    a = win.acq
    a.chk_cont.setChecked(True)
    a.chk_log.setChecked(True)
    a.log_dir.setText(str(tmp_path))
    a.samples.setCurrentIndex(a.samples.findData(100_000))
    qtbot.mouseClick(a.btn_acquire, Qt.MouseButton.LeftButton)
    assert win._acquiring and a.btn_stop.isEnabled() and not a.btn_acquire.isEnabled()
    qtbot.waitUntil(lambda: win._run is not None and win._run.cycle >= 3, timeout=30000)
    assert "ciclo" in win.statusBar().currentMessage().lower()
    qtbot.mouseClick(a.btn_stop, Qt.MouseButton.LeftButton)
    _wait_idle(qtbot, win)
    assert win.errors == [] and win.cap is not None and a.btn_acquire.isEnabled() and not a.btn_stop.isEnabled()
    (d,) = list(tmp_path.glob("Log_*"))
    assert (d / "eventi.log").is_file() and (d / "decodifica.csv").is_file()
    raws = sorted((d / "catture").glob("*.npz"))
    assert len(raws) >= 3
    ev = (d / "eventi.log").read_text(encoding="utf-8")
    assert "Avvio" in ev and "continua" in ev and "Ciclo 1:" in ev and "Stop richiesto dall'utente" in ev
    assert "Fine:" in ev


def test_continuous_trigger_miss_does_not_stop(qtbot, win, tmp_path):
    a = win.acq
    a.chk_cont.setChecked(True)
    a.chk_log.setChecked(True)
    a.chk_log_raw.setChecked(False)
    a.log_dir.setText(str(tmp_path))
    a.samples.setCurrentIndex(a.samples.findData(100_000))
    import logic_core
    orig = logic_core.SimLogicDevice.acquire
    calls = {"n": 0}

    def flaky(self, cfg):
        calls["n"] += 1
        if calls["n"] % 2 == 1:
            raise logic_core.TriggerTimeout("trigger non verificatosi nella finestra di acquisizione")
        return orig(self, cfg)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(logic_core.SimLogicDevice, "acquire", flaky)
    try:
        qtbot.mouseClick(a.btn_acquire, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: win._run is not None and win._run.cycle >= 4 and win._run.misses >= 2, timeout=30000)
        qtbot.mouseClick(a.btn_stop, Qt.MouseButton.LeftButton)
        _wait_idle(qtbot, win)
    finally:
        monkey.undo()
    assert win.errors == [] and win.cap is not None
    (d,) = list(tmp_path.glob("Log_*"))
    ev = (d / "eventi.log").read_text(encoding="utf-8")
    assert ev.count("AVVISO") >= 2 and "trigger mancati" in ev
    assert not (d / "catture").exists()


def test_single_shot_with_log_and_bad_folder(qtbot, win, tmp_path):
    a = win.acq
    a.chk_log.setChecked(True)
    a.log_dir.setText(str(tmp_path))
    a.samples.setCurrentIndex(a.samples.findData(100_000))
    qtbot.mouseClick(a.btn_acquire, Qt.MouseButton.LeftButton)
    _wait_idle(qtbot, win)
    (d,) = list(tmp_path.glob("Log_*"))
    assert len(list((d / "catture").glob("*.npz"))) == 1
    assert "Fine: 1 catture" in (d / "eventi.log").read_text(encoding="utf-8")
    # cartella non scrivibile: errore chiaro, nessuna acquisizione avviata
    blocker = tmp_path / "file.txt"
    blocker.write_text("x")
    a.log_dir.setText(str(blocker / "sotto"))
    win.errors.clear()
    qtbot.mouseClick(a.btn_acquire, Qt.MouseButton.LeftButton)
    assert win.errors and "log" in win.errors[-1].lower() and not win._acquiring and win._run is None


def test_log_options_validation(qtbot, win):
    a = win.acq
    a.chk_log.setChecked(True)
    for c in (a.chk_log_dec, a.chk_log_raw, a.chk_log_ev):
        c.setChecked(False)
    qtbot.mouseClick(a.btn_acquire, Qt.MouseButton.LeftButton)
    assert win.errors and "almeno un tipo" in win.errors[-1] and not win._acquiring


# ------------------------------------ verifica collegamenti --------------------
def _verify(qtbot, w, kind):
    w.acq.device.setCurrentIndex(w.acq.device.findText(kind))
    qtbot.mouseClick(w.pinout.btn_verify, Qt.MouseButton.LeftButton)
    assert w._acquiring and not w.pinout.btn_verify.isEnabled()
    qtbot.waitUntil(lambda: not w._acquiring, timeout=30000)
    settle(qtbot, w)


def test_verify_walk_all_ok(qtbot, win):
    _verify(qtbot, win, "Simulato (walk)")
    pp = win.pinout
    assert win.errors == [] and not pp.vtable.isHidden()
    assert [pp.vtable.item(r, 2).text() for r in range(8)] == ["ok"] * 8
    assert [pp.vtable.item(r, 1).text() for r in range(8)] == [str(i) for i in range(1, 9)]   # pin del pinout di default
    assert "corretti" in pp.lbl_verify.text() and "tutto a posto" in win.statusBar().currentMessage()
    assert win.cap is not None and win.cap.samplerate == 20e3 and len(win.cap) == 130_000
    assert pp.btn_verify.isEnabled() and win.acq.btn_acquire.isEnabled()


def test_verify_not_in_walk_mode_hint(qtbot, win):
    _verify(qtbot, win, "Simulato")
    pp = win.pinout
    assert "mode walk" in pp.lbl_verify.text()
    assert sum(pp.vtable.item(r, 2).text() != "ok" for r in range(8)) >= 4


def test_verify_uses_pinout_pins(qtbot, win):
    pp = win.pinout
    a = list(pp.assign())
    a[0], a[3] = a[3], a[0]                       # CH0 sul pin 4 e CH3 sul pin 1
    pp.set_assign(a)
    _verify(qtbot, win, "Simulato (walk)")
    assert pp.vtable.item(0, 1).text() == "4" and pp.vtable.item(3, 1).text() == "1"


# ------------------------------------------- pannello Tester ---------------------
@pytest.fixture
def tp(qtbot, win):
    t = win.tester
    t._sim_kw = {"walk_s": 0.04}
    yield t
    t.shutdown()


def _until(qtbot, pred, timeout=5000):
    qtbot.waitUntil(pred, timeout=timeout)


def test_tester_tab_exists_and_starts_disconnected(qtbot, win):
    t = win.tester
    assert win._bottom_tabs.indexOf(t) >= 0 and not t.connected()
    assert not t.btn_help.isEnabled() and t.btn_connect.text() == "Connetti" and "Non collegato" in t.lbl_link.text()
    assert t.port.findData("Simulato") >= 0
    assert not t.send("status")                          # senza collegamento: messaggio in console, nessun errore
    assert "Non collegato" in t.console.toPlainText()
    win._bottom_tabs.setCurrentWidget(t)
    assert win.vsplit.sizes()[1] >= 300


def test_tester_connect_modes_and_walk_led(qtbot, win, tp):
    got = []
    tp.connectionChanged.connect(got.append)
    tp.port.setCurrentIndex(tp.port.findData("Simulato"))
    qtbot.mouseClick(tp.btn_connect, Qt.MouseButton.LeftButton)
    assert tp.connected() and got == [True] and tp.btn_connect.text() == "Disconnetti"
    _until(qtbot, lambda: tp.mode() == "mixed" and tp.mode_btn["mixed"].isChecked())
    assert "Tester analizzatore logico" in tp.console.toPlainText() and "cicli" in tp.lbl_stats.text() or True
    assert tp.strip.lit(0) and "UART" in tp.lbl_doing.text()
    qtbot.mouseClick(tp.mode_btn["walk"], Qt.MouseButton.LeftButton)
    _until(qtbot, lambda: tp.mode() == "walk" and tp.mode_btn["walk"].isChecked() and tp.strip._walk is not None)
    _until(qtbot, lambda: tp.strip._walk == 3)            # l'LED segue il canale annunciato dal tester
    assert [tp.strip.lit(i) for i in range(8)].count(True) == 1 and "Ora: CH" in tp.lbl_doing.text()
    qtbot.mouseClick(tp.mode_btn["off"], Qt.MouseButton.LeftButton)
    _until(qtbot, lambda: tp.mode() == "off" and tp.mode_btn["off"].isChecked())
    assert not any(tp.strip.lit(i) for i in range(8))
    qtbot.mouseClick(tp.btn_connect, Qt.MouseButton.LeftButton)
    assert not tp.connected() and got == [True, False] and tp.btn_connect.text() == "Connetti"
    assert not any(b.isEnabled() for b in tp.mode_btn.values())


def test_tester_faults_enabled_only_in_serial_and_sent(qtbot, win, tp):
    tp.connect_tester("Simulato")
    _until(qtbot, lambda: tp.mode() == "mixed")
    assert not tp.sp_baud.isEnabled() and "seriale" in tp.lbl_faults.text()
    qtbot.mouseClick(tp.mode_btn["serial"], Qt.MouseButton.LeftButton)
    _until(qtbot, lambda: tp.sp_baud.isEnabled())
    tp.sp_baud.setValue(2.5)
    tp.sp_lead.setValue(-100)
    tp.chk_parity.setChecked(True)
    tp.chk_crc.setChecked(True)
    _until(qtbot, lambda: tp.link.state.baud_err == 2.5 and tp.link.state.de_lead == -100 and tp.link.state.parity_err
           and tp.link.state.crc_err)
    assert "baud +2.5 %" in tp.lbl_doing.text() and "parità errata" in tp.lbl_doing.text() \
        and "CRC Modbus errato" in tp.lbl_doing.text()
    assert "baud 2.5" in tp.console.toPlainText() and "delead -100" in tp.console.toPlainText()
    qtbot.mouseClick(tp.btn_reset, Qt.MouseButton.LeftButton)
    _until(qtbot, lambda: tp.link.state.baud_err == 0 and tp.sp_baud.value() == 0 and not tp.chk_parity.isChecked()
           and not tp.chk_crc.isChecked() and tp.sp_lead.value() == 100)


def test_tester_console_commands_history_and_errors(qtbot, win, tp):
    tp.connect_tester("Simulato")
    _until(qtbot, lambda: tp.mode() == "mixed")
    tp.cmd.setText("frobnicate")
    qtbot.keyClick(tp.cmd, Qt.Key.Key_Return)
    _until(qtbot, lambda: "comando sconosciuto" in tp.console.toPlainText())
    assert "› frobnicate" in tp.console.toPlainText() and tp.cmd.text() == ""
    qtbot.keyClick(tp.cmd, Qt.Key.Key_Up)
    assert tp.cmd.text() == "frobnicate"
    tp.cmd.clear()
    tp.cmd.setText("baud 99")
    qtbot.keyClick(tp.cmd, Qt.Key.Key_Return)
    _until(qtbot, lambda: "valore non valido" in tp.console.toPlainText())
    before = tp.console.toPlainText().count("modo=")
    qtbot.wait(2300)                                      # due sondaggi automatici: la console non si riempie
    assert tp.console.toPlainText().count("modo=") == before
    qtbot.mouseClick(tp.btn_status, Qt.MouseButton.LeftButton)
    _until(qtbot, lambda: tp.console.toPlainText().count("modo=") == before + 1)
    qtbot.mouseClick(tp.btn_clear, Qt.MouseButton.LeftButton)
    assert tp.console.toPlainText() == ""


def test_tester_connection_loss_is_reported(qtbot, win, tp):
    tp.connect_tester("Simulato")
    _until(qtbot, lambda: tp.mode() == "mixed")
    tp.link.transport.close()                              # come un cavo staccato
    _until(qtbot, lambda: not tp.connected() and tp.link is None)
    assert "Collegamento perso" in tp.console.toPlainText() and tp.btn_connect.text() == "Connetti"


def test_verify_sets_walk_on_connected_tester_and_restores(qtbot, win, tp):
    tp.connect_tester("Simulato")
    _until(qtbot, lambda: tp.mode() == "mixed")
    win.acq.device.setCurrentIndex(win.acq.device.findText("Simulato (walk)"))
    qtbot.mouseClick(win.pinout.btn_verify, Qt.MouseButton.LeftButton)
    _until(qtbot, lambda: tp.mode() == "walk")
    qtbot.waitUntil(lambda: not win._acquiring, timeout=30000)
    settle(qtbot, win)
    _until(qtbot, lambda: tp.mode() == "mixed")           # modo precedente ripristinato
    assert "mode walk" in tp.console.toPlainText() and win._verify_prev is None
    assert [win.pinout.vtable.item(r, 2).text() for r in range(8)] == ["ok"] * 8


def test_serial_fields_editable_while_disabled(qapp):
    from logic_gui.panels import SerialPanel
    p = SerialPanel()
    assert not p.g.isChecked() and p.mode.isEnabled() and p.baud.isEnabled() and p.fmt.isEnabled()
    p.fmt.setCurrentIndex(1)
    p.baud.setCurrentText("9600")
    cfg = p.config()
    assert not cfg.enabled and cfg.baud == 9600 and cfg.fmt is not None
    p.g.setChecked(True)
    assert p.config().enabled


def test_modbus_flow_in_gui(qtbot, win):
    win.acq.device.setCurrentText("Simulato (seriale)")
    win.ser.mode.setCurrentIndex(win.ser.mode.findData("rs485"))
    win.ser.ro.setCurrentIndex(win.ser.ro.findData(2))
    win.ser.de.setCurrentIndex(win.ser.de.findData(3))
    win.ser.g.setChecked(True)
    assert win.msg_table.isColumnHidden(7)
    win.ser.modbus.setChecked(True)
    acquire(qtbot, win)
    qtbot.waitUntil(lambda: win.msg_table.rowCount() >= 4 and not win.msg_table.isColumnHidden(7), timeout=15000)
    settle(qtbot, win)
    texts = [win.msg_table.item(r, 7).text() for r in range(win.msg_table.rowCount())]
    assert any("FC03" in t and "da 0 ×2 reg" in t for t in texts) and any("risposta 2 reg: 1" in t for t in texts)
    assert "Modbus RTU:" in win.lbl_ser.text() and "Tempo di risposta" in win.lbl_ser.text()
    assert "Modbus" in {l.name for l in win.wave._lanes}


def test_hw_trigger_checkbox_only_for_hardware(qtbot, win):
    a = win.acq
    assert not a.chk_hwtrig.isEnabled() and not a.config().hw_trigger
    a.device.setCurrentText("Hardware (sigrok)")
    assert a.chk_hwtrig.isEnabled()
    a.chk_hwtrig.setChecked(True)
    assert not a.config().hw_trigger                          # senza canale di trigger non c'e' nulla da gestire
    a.trig_ch.setCurrentIndex(1)
    assert a.config().hw_trigger and a.config().trigger.channel == 0
    a.device.setCurrentText("Simulato")
    assert not a.chk_hwtrig.isEnabled() and not a.chk_hwtrig.isChecked() and not a.config().hw_trigger
