"""Convenzioni dei moduli.

Un modulo espone un QWidget (anche un QMainWindow incorporato) e può definire, opzionalmente:
  title_changed = Signal(str)     titolo della scheda
  apri_file(path)                 apre un file
  salva() -> bool                 salva (True se ok)
  modificato() -> bool            modifiche non salvate
  can_close() -> bool             veto alla chiusura
  contesto_assistente() -> str    testo passato a Claude
  on_activated()                  scheda attivata
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow, QWidget


def embed(win: QMainWindow) -> QMainWindow:
    """Rende un QMainWindow adatto a vivere dentro una scheda (niente finestra di primo livello)."""
    win.setWindowFlags(Qt.WindowType.Widget)
    return win
