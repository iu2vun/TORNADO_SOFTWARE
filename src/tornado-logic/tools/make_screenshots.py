"""Genera gli screenshot del manuale (docs/img/*.png) con i simulatori, in modo offscreen.
Uso:  python tools/make_screenshots.py   (serve TORNADO_LOGIC_SIM=1, impostato qui)"""
import os, sys, tempfile, time
os.environ["TORNADO_LOGIC_SIM"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtWidgets import QApplication
from tornado_ui import apply_theme
import logic_gui.main_window as mw

OUT = os.path.join(ROOT, "docs", "img")
os.makedirs(OUT, exist_ok=True)
app = QApplication.instance() or QApplication([])
apply_theme(app)
ini = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
mw.settings = lambda: ini
w = mw.MainWindow()
w.notify_error = lambda m: print("ERR", m)
w.resize(1500, 900)
w.show()


def pump(ms=100):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()


def until(pred, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.02)
    raise TimeoutError


def idle():
    until(lambda: not w._acquiring and not w._t_dec.isActive() and not w._t_meas.isActive()
          and all(x.isFinished() for x in w._workers) and w._run is None)
    pump(250)


def shot(name):
    pump(200)
    w.grab().save(os.path.join(OUT, name + ".png"))
    print("ok", name)


def acquire():
    w.cap = None
    w.acq.btn_acquire.click()
    until(lambda: w.cap is not None)
    idle()


left = w.centralWidget().findChildren(type(w._bottom_tabs))[0]
def lefttab(i): left.setCurrentIndex(i); pump(100)
def bottom(name):
    for i in range(w._bottom_tabs.count()):
        if w._bottom_tabs.tabText(i) == name:
            w._bottom_tabs.setCurrentIndex(i)
    pump(150)

# 1. acquisizione semplice + trigger
w.acq.device.setCurrentText("Simulato")
w.acq.trig_ch.setCurrentIndex(w.acq.trig_ch.findData(7)); w.acq.trig_mode.setCurrentIndex(1)
acquire(); lefttab(0); bottom("Decoder"); shot("01_acquisizione")
# 2. decoder UART/SPI/I2C
lefttab(1); shot("02_decoder")
# 3. misure
lefttab(0); bottom("Misure"); shot("03_misure")
# 4. seriale RS-232/485 + Modbus
w.acq.trig_ch.setCurrentIndex(0)
w.acq.device.setCurrentText("Simulato (seriale)")
w.ser.mode.setCurrentIndex(w.ser.mode.findData("rs485"))
w.ser.ro.setCurrentIndex(w.ser.ro.findData(2)); w.ser.de.setCurrentIndex(w.ser.de.findData(3))
w.ser.g.setChecked(True); w.ser.modbus.setChecked(True)
acquire(); until(lambda: w.msg_table.rowCount() >= 4); idle()
lefttab(2); bottom("Seriale"); shot("04_seriale_modbus")
# 5. pinout + verifica walk
w.ser.g.setChecked(False); pump(300)
w.acq.device.setCurrentText("Simulato (walk)")
lefttab(3); w.pinout.btn_verify.click()
until(lambda: w._acquiring); until(lambda: not w._acquiring, 60); idle(); shot("05_pinout_verifica")
# 6. continua + log
lefttab(0); w.acq.device.setCurrentText("Simulato")
w.acq.chk_cont.setChecked(True); w.acq.chk_log.setChecked(True)
w.acq.log_dir.setText(tempfile.mkdtemp())
w.acq.samples.setCurrentIndex(w.acq.samples.findData(100_000))
w.acq.btn_acquire.click()
until(lambda: w._run is not None and w._run.cycle >= 3)
shot("06_continua_log")
w.acq.btn_stop.click(); idle()
# 7. tester
w.acq.chk_cont.setChecked(False)
t = w.tester; t._sim_kw = {"walk_s": 0.3}
bottom("Tester"); t.connect_tester("Simulato"); until(lambda: t.mode() == "mixed")
t.mode_btn["serial"].click(); until(lambda: t.sp_baud.isEnabled())
t.send("status"); pump(600); shot("07_tester")
t.shutdown()
