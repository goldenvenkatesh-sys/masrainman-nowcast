"""Promo poster (1080x1350, Instagram/Facebook portrait) with a scan-to-join QR
for the South India Radar Nowcast Alerts WhatsApp channel.

    python tools/make_alert_promo_poster.py  -> assets/alert_channel_promo.jpg
"""
from pathlib import Path
import qrcode
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
F = ROOT / "assets" / "fonts"
LINK = "https://whatsapp.com/channel/0029VbE2C5F9RZAanCxEFv0o"
W, H = 1080, 1350


def font(n, s):
    return ImageFont.truetype(str(F / n), s)


def centred(d, y, text, f, fill):
    d.text(((W - d.textlength(text, font=f)) / 2, y), text, font=f, fill=fill)


def main():
    img = Image.new("RGB", (W, H), (8, 22, 46))
    d = ImageDraw.Draw(img)
    for i in range(H):                                   # vertical gradient
        t = i / H
        d.line([(0, i), (W, i)], fill=(int(8 + 14 * t), int(22 + 30 * t), int(46 + 50 * t)))

    logo = Image.open(ROOT / "assets" / "alert_channel_logo.jpg").resize((300, 300))
    m = Image.new("L", logo.size, 0)
    ImageDraw.Draw(m).ellipse([0, 0, 300, 300], fill=255)
    img.paste(logo, ((W - 300) // 2, 60), m)

    centred(d, 398, "மழை உங்கள் ஊருக்கு வருகிறதா?", font("NotoSansTamil-ExtraBold.ttf", 46), (255, 255, 255))
    centred(d, 470, "Is rain coming to your town?", font("Poppins-SemiBold.ttf", 46), (150, 220, 255))
    f = font("Poppins-SemiBold.ttf", 36)
    for k, line in enumerate(["🛰 Live IMD radar · every hour", "⚡ Instant heavy-rain alerts",
                              "📍 District-wise, next 60 minutes", "Tamil + English · FREE"]):
        line = line.replace("🛰 ", "• ").replace("⚡ ", "• ").replace("📍 ", "• ")
        fnt = f
        centred(d, 560 + k * 56, line, fnt, (230, 240, 250))

    qr = qrcode.QRCode(border=2, box_size=10, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(LINK)
    q = qr.make_image(fill_color=(8, 22, 46), back_color="white").convert("RGB").resize((330, 330), Image.NEAREST)
    d.rounded_rectangle([(W - 370) // 2, 810, (W + 370) // 2, 1180], 28, fill=(255, 255, 255))
    img.paste(q, ((W - 330) // 2, 830))
    centred(d, 1200, "SCAN TO FOLLOW ON WHATSAPP", font("Poppins-Black.ttf", 44), (255, 196, 0))
    centred(d, 1262, "South India Radar Nowcast Alerts · MasRainman", font("Poppins-Regular.ttf", 30), (200, 215, 235))
    out = ROOT / "assets" / "alert_channel_promo.jpg"
    img.save(out, quality=92)
    print("wrote", out)


if __name__ == "__main__":
    main()
