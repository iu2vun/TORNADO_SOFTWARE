import sys

from ..core.registry import ModuleSpec, register
from .base import embed


def _factory():
    if sys.platform != "win32":
        raise RuntimeError("USB Tree View funziona solo su Windows (usa le API di hub USB via ctypes).")
    from ..apps.usbtree.usbtree import MainWindow
    return embed(MainWindow())


register(ModuleSpec("usbtree", "USB Tree View", "Albero dei dispositivi USB, descrittori e monitor di sistema", "usb",
                    "Sistema", _factory, (), multi=False, ordine=60))
