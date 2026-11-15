"""Genera icona (.ico) e file del logo Tornado Laboratori dai dati vettoriali in tornado_suite/logo_data.py.

Icona = solo il fulmine, arancione su nero. Eseguito da build.bat quando l'icona non esiste. Richiede Pillow.
Produce: assets/TornadoSniffer.ico, assets/icon_fulmine.png, assets/logo_tornado.png, assets/logo_tornado.svg
"""
from __future__ import annotations

import os
import sys

from PIL import Image, ImageChops, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tornado_suite.logo_data import BOLT, LOGO_H, LOGO_W, TEXT  # noqa: E402

OUT = "assets/TornadoSniffer.ico"
ORANGE, BLACK = (255, 138, 31, 255), (0, 0, 0, 255)
SIZES = [16, 24, 32, 48, 64, 128, 256]


def _mask(polys, size, scale, off) -> Image.Image:
    m = Image.new("L", size, 0)
    for poly in polys:                                   # XOR = riempimento pari/dispari (fori delle lettere)
        t = Image.new("L", size, 0)
        ImageDraw.Draw(t).polygon([(x * scale + off[0], y * scale + off[1]) for x, y in poly], fill=255)
        m = ImageChops.logical_xor(m.convert("1"), t.convert("1")).convert("L")
    return m


def render_icon(size: int = 256) -> Image.Image:
    s = size * 4
    xs = [p[0] for p in BOLT]; ys = [p[1] for p in BOLT]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    sc = s * 0.66 / max(w, h)
    off = (s / 2 - (min(xs) + w / 2) * sc, s / 2 - (min(ys) + h / 2) * sc)
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.18), fill=BLACK)
    im.paste(Image.new("RGBA", (s, s), ORANGE), (0, 0), _mask([BOLT], (s, s), sc, off))
    return im.resize((size, size), Image.LANCZOS)


def render_logo(width: int = 1600) -> Image.Image:
    k = 2
    W = width * k
    H = int(W * LOGO_H / LOGO_W) + int(W * 0.12)
    sc = W * 0.88 / LOGO_W
    off = ((W - LOGO_W * sc) / 2, (H - LOGO_H * sc) / 2)
    im = Image.new("RGBA", (W, H), BLACK)
    for polys in ([BOLT], TEXT):
        im.paste(Image.new("RGBA", (W, H), ORANGE), (0, 0), _mask(polys, (W, H), sc, off))
    return im.resize((width, H // k), Image.LANCZOS)


def write_svg(path: str) -> None:
    def d(polys):
        return " ".join("M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in p) + "Z" for p in polys)
    pad = LOGO_W * 0.06
    with open(path, "w", encoding="utf-8") as f:
        f.write(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{-pad:.1f} {-pad:.1f} '
                f'{LOGO_W + 2 * pad:.1f} {LOGO_H + 2 * pad:.1f}">\n<rect x="{-pad:.1f}" y="{-pad:.1f}" '
                f'width="{LOGO_W + 2 * pad:.1f}" height="{LOGO_H + 2 * pad:.1f}" fill="#000"/>\n'
                f'<path fill="#ff8a1f" fill-rule="evenodd" d="{d([BOLT] + TEXT)}"/>\n</svg>\n')


def main() -> None:
    os.makedirs("assets", exist_ok=True)
    big = render_icon(256)
    big.save(OUT, sizes=[(s, s) for s in SIZES])
    big.save("assets/icon_fulmine.png")
    render_logo().save("assets/logo_tornado.png")
    write_svg("assets/logo_tornado.svg")
    print("Creati:", OUT, "icon_fulmine.png, logo_tornado.png, logo_tornado.svg")


if __name__ == "__main__":
    main()
