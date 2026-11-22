"""Impostazioni persistenti (QSettings) senza dipendere dalla suite.

Di default usa QSettings() con organizzazione/applicazione impostate su QApplication.
Dentro la Tornado Suite si può reindirizzare al suo file .ini con ``set_settings_provider``.
"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QSettings

_provider: Optional[Callable[[], QSettings]] = None


def set_settings_provider(fn: Optional[Callable[[], QSettings]]) -> None:
    global _provider
    _provider = fn


def settings() -> QSettings:
    return _provider() if _provider else QSettings()
