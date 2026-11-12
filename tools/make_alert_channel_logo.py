"""Logo for the "South India Radar Nowcast Alerts" WhatsApp channel.

WhatsApp shows channel pictures as a circle, so everything sits inside the
central circle. Own MasRainman look (radar sweep + rain bands in the
MasRainman rainbow), deliberately not the Chennai Rains style.

    python tools/make_alert_channel_logo.py   -> assets/alert_channel_logo.jpg
"""
from pathlib import Path
import math
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
F = ROOT / "assets" / "fonts"
S = 1024
C = S / 2


def font(name, size):
    return ImageFont.truetype(str(F / name), size)


def main():
    # background: deep navy radial gradient
    y, x = np.mgrid[0:S, 0:S]
    r = np.hypot(x - C, y - C) / (S / 2)
    bg = np.zeros((S, S, 3))
    inner, outer = np.array([18, 52, 96]), np.array([6, 16, 36])
    t = np.clip(r, 0, 1)[..., None]
    bg = inner * (1 - t) + outer * t
    img = Image.fromarray(bg.astype("uint8"), "RGB")

    # radar sweep wedge (teal, fading)
    sweep = Image.new("L", (S, S), 0)
    sd = ImageDraw.Draw(sweep)
    R = 400
    for i in range(60):
        a0 = -110 + i
        sd.pieslice([C - R, C - R - 40, C + R, C + R - 40], a0, a0 + 1.2, fill=int(4 + i * 2.6))
    sweep = sweep.filter(ImageFilter.GaussianBlur(3))
    img = Image.composite(Image.new("RGB", (S, S), (40, 220, 200)), img, sweep)

    d = ImageDraw.Draw(img)
    # range rings
    for rr in (130, 260, 390):
        d.ellipse([C - rr, C - 40 - rr, C + rr, C - 40 + rr], outline=(70, 150, 190), width=3)
    d.line([C - 400, C - 40, C + 400, C - 40], fill=(50, 110, 150), width=2)
    d.line([C, C - 440, C, C + 360], fill=(50, 110, 150), width=2)

    # rain echo blobs in the MasRainman rainbow (light -> heavy)
    blobs = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bd = ImageDraw.Draw(blobs)
    rng = np.random.default_rng(7)
    cx, cy = C + 110, C - 120
    for k, col in enumerate([(70, 200, 255), (60, 220, 120), (255, 220, 50), (255, 140, 30), (240, 50, 90), (180, 60, 220)]):
        rad = 150 - k * 24
        for _ in range(9):
            ox, oy = rng.normal(0, rad * 0.35, 2)
            rx, ry = rad * rng.uniform(0.45, 0.8), rad * rng.uniform(0.3, 0.6)
            bd.ellipse([cx + ox - rx, cy + oy - ry, cx + ox + rx, cy + oy + ry], fill=col + (235,))
    blobs = blobs.filter(ImageFilter.GaussianBlur(10))
    img.paste(blobs, (0, 0), blobs)

    # alert bell badge
    d = ImageDraw.Draw(img)
    bx, by, br = C - 215, C - 195, 92
    d.ellipse([bx - br, by - br, bx + br, by + br], fill=(255, 196, 0), outline=(255, 255, 255), width=8)
    d.pieslice([bx - 46, by - 52, bx + 46, by + 40], 180, 360, fill=(20, 30, 50))
    d.rectangle([bx - 46, by - 6, bx + 46, by + 26], fill=(20, 30, 50))
    d.rounded_rectangle([bx - 60, by + 20, bx + 60, by + 36], 8, fill=(20, 30, 50))
    d.ellipse([bx - 14, by + 36, bx + 14, by + 60], fill=(20, 30, 50))
    for s_ in (-1, 1):  # ring waves
        d.arc([bx - 82, by - 82, bx + 82, by + 82], 200 if s_ < 0 else -40, 230 if s_ < 0 else -10, fill=(20, 30, 50), width=9)

    # text band
    d.rounded_rectangle([C - 360, C + 170, C + 360, C + 300], 26, fill=(255, 255, 255))
    t1 = "RADAR ALERTS"
    f1 = font("Poppins-Black.ttf", 92)
    w = d.textlength(t1, font=f1)
    d.text((C - w / 2, C + 178), t1, font=f1, fill=(200, 20, 60))
    t2 = "SOUTH INDIA  •  LIVE NOWCAST"
    f2 = font("Poppins-SemiBold.ttf", 40)
    w = d.textlength(t2, font=f2)
    d.text((C - w / 2, C + 318), t2, font=f2, fill=(230, 245, 255))
    t3 = "MasRainman"
    f3 = font("Poppins-ExtraBold.ttf", 44)
    w = d.textlength(t3, font=f3)
    d.text((C - w / 2, C + 372), t3, font=f3, fill=(255, 196, 0))

    out = ROOT / "assets" / "alert_channel_logo.jpg"
    img.save(out, quality=93)
    # circle preview (how WhatsApp shows it)
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).ellipse([0, 0, S, S], fill=255)
    prev = Image.new("RGB", (S, S), (255, 255, 255))
    prev.paste(img, (0, 0), m)
    prev.save(ROOT / "assets" / "alert_channel_logo_circle_preview.png")
    print("wrote", out)


if __name__ == "__main__":
    main()
