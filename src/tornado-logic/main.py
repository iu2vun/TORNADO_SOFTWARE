"""Tornado Logic - controllo, misura e analisi per analizzatore logico USB 8 canali."""
import argparse
import multiprocessing
import os
import sys

if sys.stdout is None:                      # modalita' finestra (PyInstaller --windowed)
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

APP_NAME = "Tornado Logic"
APP_VERSION = "0.1"


def main():
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument("--no-splash", action="store_true", help="Salta lo splash screen di avvio")
    args, _ = ap.parse_known_args()

    from logic_gui.branding import app_icon
    from tornado_ui import run_app

    def factory():
        import time
        splash = None
        t0 = time.monotonic()
        if not args.no_splash:
            from logic_gui.splash import SplashScreen
            splash = SplashScreen(APP_VERSION)
            splash.show()
            splash.set_progress(0.0)
        from logic_gui.main_window import MainWindow
        win = MainWindow()
        win.setWindowIcon(app_icon())
        if splash is not None:
            splash.run(10.0, t0)          # il programma si apre dopo 10 s dall'avvio
        return win

    sys.exit(run_app(factory, APP_NAME, APP_VERSION))


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
