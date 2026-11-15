"""Database vendor/prodotto USB (usb.ids) con cache locale."""
from __future__ import annotations

import os
import re
import urllib.request

URL = "http://www.linux-usb.org/usb.ids"


def cache_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "usbtree")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "usb.ids")


class UsbIds:
    def __init__(self) -> None:
        self.vendors: dict[int, str] = {}
        self.products: dict[tuple[int, int], str] = {}
        self.load()

    @property
    def available(self) -> bool:
        return bool(self.vendors)

    def load(self) -> bool:
        path = cache_path()
        if not os.path.isfile(path):
            return False
        vendors: dict[int, str] = {}
        products: dict[tuple[int, int], str] = {}
        cur = None
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line or line.startswith("#") or line.startswith("\t\t"):
                    continue
                if line.startswith("\t"):
                    m = re.match(r"\t([0-9a-f]{4})\s+(.*)", line)
                    if m and cur is not None:
                        products[(cur, int(m[1], 16))] = m[2]
                    continue
                m = re.match(r"([0-9a-f]{4})\s+(.*)", line)
                if not m:
                    break  # fine sezione vendor (iniziano le classi)
                cur = int(m[1], 16)
                vendors[cur] = m[2]
        self.vendors, self.products = vendors, products
        return bool(vendors)

    def update(self) -> int:
        """Scarica usb.ids e ricarica. Ritorna il numero di vendor."""
        with urllib.request.urlopen(URL, timeout=30) as r:
            data = r.read()
        tmp = cache_path() + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, cache_path())
        self.load()
        return len(self.vendors)

    def vendor(self, vid: int) -> str:
        return self.vendors.get(vid, "")

    def product(self, vid: int, pid: int) -> str:
        return self.products.get((vid, pid), "")
