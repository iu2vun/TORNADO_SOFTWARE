"""Avvio di un singolo modulo della suite come programma indipendente (stessa factory, stesso codice della suite).

    python -m tornado_suite.tool sparview file.s2p      oppure      python tools/sparview.py file.s2p
    python -m tornado_suite.tool --list

Ogni tool ha un proprio log (%APPDATA%\\TornadoSuite\\tools\\<id>\\logs\\app.log), la diagnostica (Ctrl+Shift+D, con i dati
del modulo se espone ``diagnostica_extra()``) e rispetta il contratto dei moduli (vedi modules/base.py):
title_changed, apri_file, salva, modificato, can_close, on_activated.
"""
from __future__ import annotations

import importlib
import os
import sys
import traceback
from typing import Optional

from . import __version__

EXCLUDED = ("claude",)                # dipende dal ponte MCP della suite: ha senso solo dentro la suite
ADMIN_DEFAULT = ("usbtree",)          # tool che chiedono l'UAC di default (gli altri solo con --admin)


def tool_ids() -> list[str]:
    """Id dei moduli utilizzabili da soli, nell'ordine del manifest (convenzione: modulo ``<id>_mod``)."""
    from .modules._manifest import MODULE_NAMES
    return [n[:-4] for n in MODULE_NAMES if n.endswith("_mod") and n[:-4] not in EXCLUDED]


def load_spec(tool_id: str):
    """Importa SOLO il modulo richiesto (avvio rapido, un modulo rotto non tocca gli altri) e ne restituisce il ModuleSpec."""
    if tool_id not in tool_ids():
        raise KeyError(f"Tool «{tool_id}» sconosciuto. Disponibili: {', '.join(tool_ids())}")
    from .core.registry import MODULES
    importlib.import_module(f"{__package__}.modules.{tool_id}_mod")
    if tool_id not in MODULES:
        raise RuntimeError(f"Il modulo {tool_id}_mod non ha registrato l'id «{tool_id}»")
    return MODULES[tool_id]


def describe_tools() -> list[tuple[str, str, str, str]]:
    """(id, nome, categoria, descrizione) di ogni tool; importa i moduli: serve per --list e per i test."""
    out = []
    for tid in tool_ids():
        try:
            s = load_spec(tid)
            out.append((tid, s.nome, s.categoria, s.descrizione))
        except Exception as e:  # noqa: BLE001
            out.append((tid, tid, "?", f"ERRORE: {e!r}"))
    return out


def _ask_save(parent, name: str) -> str:
    from PySide6.QtWidgets import QMessageBox
    r = QMessageBox.question(parent, name, f"Salvare le modifiche a «{name}»?",
                             QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
    return {QMessageBox.StandardButton.Save: "save", QMessageBox.StandardButton.Discard: "discard"}.get(r, "cancel")


def make_window(spec, widget):
    """Finestra ospite di un modulo: titolo dinamico, chiusura con le stesse regole della scheda nella suite."""
    from PySide6.QtWidgets import QMainWindow
    from .core.widgets import shortcut

    class ToolWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.spec, self.w = spec, widget
            widget._module_id = spec.id            # come fa la Shell (usato da diagnostica e strumenti)
            self.setCentralWidget(widget)
            self.setWindowTitle(spec.nome)
            self.resize(1280, 800)
            if hasattr(widget, "title_changed"):
                widget.title_changed.connect(self._retitle)
            shortcut(self, "F11", lambda: self.showNormal() if self.isFullScreen() else self.showFullScreen())
            shortcut(self, "Ctrl+Shift+D", self.diagnostica)

        def _retitle(self, t: str) -> None:
            self.setWindowTitle(f"{t} – {spec.nome}" if t and t != spec.nome else spec.nome)

        def open_path(self, path: str) -> None:
            if hasattr(widget, "apri_file"):
                try:
                    widget.apri_file(path)
                except Exception:  # noqa: BLE001
                    from PySide6.QtWidgets import QMessageBox
                    from .tornado_diag import get_logger
                    tb = traceback.format_exc()
                    get_logger(spec.id).error("apri_file(%s) fallito:\n%s", path, tb)
                    QMessageBox.critical(self, spec.nome, f"Errore aprendo\n{path}\n\n{tb}")

        def diagnostica(self) -> None:
            from .tornado_diag import collect_diagnostics
            from .tornado_diag.dialog import DiagnosticsDialog
            extra = {"tool": f"{spec.id} ({spec.nome})", "versione suite": __version__}
            if hasattr(widget, "diagnostica_extra"):
                try:
                    extra.update(widget.diagnostica_extra())
                except Exception as e:  # noqa: BLE001
                    extra["errore diagnostica_extra"] = repr(e)
            DiagnosticsDialog(self, collect_diagnostics(extra=extra), f"Diagnostica {spec.nome}",
                              "Premi «Copia diagnostica» e incolla il testo nella chat.").exec()

        def closeEvent(self, ev) -> None:  # noqa: N802
            if hasattr(widget, "can_close") and not widget.can_close():
                ev.ignore()
                return
            if hasattr(widget, "modificato") and widget.modificato():
                a = _ask_save(self, self.windowTitle())
                if a == "cancel" or (a == "save" and hasattr(widget, "salva") and not widget.salva()):
                    ev.ignore()
                    return
            try:
                widget.close()
            except Exception:  # noqa: BLE001
                pass
            ev.accept()

    return ToolWindow()


def run(tool_id: str, argv: Optional[list[str]] = None) -> int:
    from .tornado_diag import get_logger, install
    install(f"TornadoSuite/tools/{tool_id}", __version__, console=not getattr(sys, "frozen", False))
    log = get_logger()
    argv = list(sys.argv if argv is None else argv)

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication(argv)
    app.setOrganizationName("Tornado Laboratori")
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"TornadoLaboratori.Tool.{tool_id}")
        except Exception:  # noqa: BLE001
            pass
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ico = os.path.join(base, "assets", "tornado_suite.ico")
    if os.path.exists(ico):
        app.setWindowIcon(QIcon(ico))
    from .core.theme import apply_theme
    apply_theme(app)

    try:
        spec = load_spec(tool_id)
        app.setApplicationName(spec.nome)
        files = [p for p in argv[1:] if not p.startswith("-")]
        groups = [[p] for p in files] if (spec.multi and files) else [files]    # multi: una finestra per file
        windows = []
        for grp in groups:
            win = make_window(spec, spec.factory())
            win.showMaximized()
            for p in grp:
                win.open_path(p)
            windows.append(win)
    except Exception:  # noqa: BLE001
        tb = traceback.format_exc()
        log.critical("avvio di %s fallito:\n%s", tool_id, tb)
        QMessageBox.critical(None, f"Tornado – {tool_id}", f"Impossibile avviare «{tool_id}».\n\n{tb}\n\nLog: {install_log()}")
        return 1
    log.info("tool %s avviato (%d finestra/e)", tool_id, len(windows))
    return app.exec()


def install_log() -> str:
    from .tornado_diag import log_path
    return str(log_path())


def main(tool_id: str, argv: Optional[list[str]] = None) -> int:
    """Entry point comune: gestisce --admin / --no-admin, poi avvia il tool."""
    argv = list(sys.argv if argv is None else argv)
    want_admin = ("--admin" in argv) or (tool_id in ADMIN_DEFAULT and "--no-admin" not in argv)
    if want_admin and "--elevated" not in argv:
        from .core.elevate import relaunch_as_admin
        if relaunch_as_admin():
            return 0
    argv = [a for a in argv if a not in ("--admin", "--no-admin", "--elevated")]
    return run(tool_id, argv)
