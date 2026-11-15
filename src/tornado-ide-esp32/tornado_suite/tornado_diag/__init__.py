"""tornado_diag: diagnostica per programmi Python/Qt su Windows, pensata per il debug a distanza.

Uso minimo, come PRIMA istruzione di main():

    from tornado_diag import install, show_diagnostics_dialog
    install("NomeApp", version="1.0.0")          # log su file + cattura di tutte le eccezioni

    # dopo aver creato QApplication, per un pulsante «Diagnostica» nel menu/ribbon:
    show_diagnostics_dialog(parent)

Cosa fa:
  * log a rotazione in %APPDATA%/<NomeApp>/logs/app.log (cartella temporanea se non Windows);
  * sys.excepthook, threading.excepthook, unraisablehook e qInstallMessageHandler (Qt) scrivono nel log;
  * collect_diagnostics() produce un testo unico da incollare in chat: versioni, SO, moduli, porte COM,
    risorse VISA, ultime righe del log;
  * un'eccezione non gestita mostra un dialogo con «Copia diagnostica» (se Qt è disponibile).
"""
from .core import (collect_diagnostics, get_logger, install, log_dir, log_path, tail_log)

__all__ = ["install", "get_logger", "collect_diagnostics", "log_dir", "log_path", "tail_log", "show_diagnostics_dialog"]


def show_diagnostics_dialog(parent=None, app_name: str = ""):
    from .dialog import show_diagnostics_dialog as _s
    return _s(parent, app_name)
