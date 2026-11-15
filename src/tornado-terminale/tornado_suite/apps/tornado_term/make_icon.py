"""Genera icon.ico multi-risoluzione: tornado stilizzato + prompt, tema scuro."""
from PIL import Image, ImageDraw, ImageFont
import math

def draw_icon(size):
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # sfondo arrotondato scuro
    r = int(s * 0.18)
    d.rounded_rectangle([0, 0, s-1, s-1], radius=r, fill=(20, 20, 22, 255))
    # bordo sottile
    d.rounded_rectangle([0, 0, s-1, s-1], radius=r, outline=(42, 42, 46, 255), width=max(1, s//64))

    # imbuto del tornado: spire orizzontali che si restringono verso il basso,
    # gradiente ciano->blu (stesso del titolo splash)
    cx = s * 0.5
    top_y = s * 0.20
    bot_y = s * 0.74
    top_w = s * 0.40
    bot_w = s * 0.045
    n = 9
    grad = [(0x14,0xff,0xff),(0x14,0xf0,0xff),(0x1a,0xcf,0xff),(0x1a,0x8f,0xff),
            (0x12,0x73,0xf0),(0x0d,0x5f,0xe0),(0x12,0x4f,0xd0),(0x1a,0x3f,0xc0),(0x1a,0x33,0xb0)]
    for i in range(n):
        t = i / (n - 1)
        y = top_y + (bot_y - top_y) * t
        w = top_w * (1 - t) + bot_w * t
        h = max(2, (s * 0.055) * (1 - t) + (s*0.02) * t)
        # ondeggiamento laterale per dare movimento
        off = math.sin(t * 3.0) * s * 0.03 * (1 - t)
        col = grad[i] + (255,)
        d.ellipse([cx - w/2 + off, y - h/2, cx + w/2 + off, y + h/2], fill=col)

    # prompt ">_" in basso a destra, verde terminale
    try:
        fnt = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", int(s*0.26))
    except Exception:
        fnt = ImageFont.load_default()
    if s >= 32:
        d.text((s*0.60, s*0.66), ">_", font=fnt, fill=(0x19, 0xcb, 0x00, 255))
    return img

sizes = [16, 24, 32, 48, 64, 128, 256]
imgs = [draw_icon(x) for x in sizes]
imgs[0].save("icon.ico", format="ICO", sizes=[(x, x) for x in sizes],
             append_images=imgs[1:])
# anteprima PNG grande
draw_icon(256).save("icon_preview.png")
print("icon.ico creato con risoluzioni:", sizes)
