"""Two extra card styles (10 Nov 2026), sent ONLY to the admin's private
Telegram bot chat -- never the channel, forward channel, WhatsApp or Facebook.

  B  district card : region header, radar map, table of towns with rain
                     "now" and "next 60 min" as coloured chips
  C  LIVE card     : red LIVE strip, the town getting the heaviest rain in
                     big letters, close radar map, motion / speed / next town

Both reuse the regular card's map panel (card.render_panel) and colours,
so the radar picture looks exactly like the existing cards.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

from . import config
from .alerts import DIRS_TA, Nowcast, tier
from .card import (GREY, INK, LOGO, PAPER, W, H, band_color, card_palette, fit_font, font, hex_rgb,
                   make_default_logo, mixed_text, render_panel)

BLACK = (17, 17, 17)
YELLOW = (255, 214, 10)
RED = (200, 16, 46)
LINE = (213, 220, 228)
SOFT = (238, 243, 248)
LIVE_MIN_DBZ = float(os.environ.get("LIVE_CARD_MIN_DBZ", "40"))   # LIVE card only for heavy rain now

CHIP_TA = [(50, "தீவிர மழை"), (45, "மிக கனமழை"), (40, "கனமழை"), (30, "மிதமான"), (20, "லேசான")]
DIRS_EN_FULL = ["North", "Northeast", "East", "Southeast", "South", "Southwest", "West", "Northwest"]


def _chip_text(dbz: float) -> str:
    for k, s in CHIP_TA:
        if dbz >= k:
            return s
    return "மழை இல்லை"


def _text_on(rgb) -> tuple:
    r, g, b = rgb
    return (255, 255, 255) if (0.299 * r + 0.587 * g + 0.114 * b) < 150 else INK


def _clock(nc: Nowcast) -> tuple[str, str]:
    try:
        dt = datetime.strptime(nc.generated_ist.replace(" IST", ""), "%d %b %Y %H:%M")
        return dt.strftime("%I:%M %p").lstrip("0"), dt.strftime("%d %b %Y")
    except Exception:
        return nc.layer_times.get(0, ""), ""


def _paste_rounded(card: Image.Image, panel: Image.Image, xy, radius=20, border=LINE):
    m = Image.new("L", panel.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, panel.size[0] - 1, panel.size[1] - 1], radius, fill=255)
    card.paste(panel.convert("RGB"), xy, m)
    x, y = xy
    ImageDraw.Draw(card).rounded_rectangle([x, y, x + panel.size[0], y + panel.size[1]], radius,
                                           outline=border, width=3)


def _colour_bar(card, engine, x0, x1, y, h=16):
    pal = card_palette(engine)
    seg = (x1 - x0) / len(pal)
    bar = Image.new("RGBA", (int(x1 - x0), h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bar)
    for i, hc in enumerate(pal):
        bd.rectangle([i * seg, 0, (i + 1) * seg, h], fill=hex_rgb(hc) + (255,))
    m = Image.new("L", bar.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, bar.size[0] - 1, h - 1], h // 2, fill=255)
    card.paste(bar, (int(x0), int(y)), m)


def _brand_footer(card, d, y, right_text: str):
    if not LOGO.exists():
        make_default_logo()
    logo = Image.open(LOGO).convert("RGBA").resize((72, 72), Image.LANCZOS)
    card.paste(logo, (48, y), logo)
    d.text((132, y + 6), config.BRAND_NAME, font=font("xbold", 34), fill=(11, 92, 173))
    d.text((133, y + 50), "WEATHER FOR A SAFER TOMORROW", font=font("semi", 14), fill=GREY)
    mixed_text(d, (W - 48 - _mixed_w(d, right_text, 24), y + 36), right_text, 24, INK)


def _mixed_w(d, text, size):
    import re
    runs = [r for r in re.split(r"([A-Za-z0-9&@#%+./()-]+(?: [A-Za-z0-9&@#%+./()-]+)*)", text) if r]
    return sum(d.textlength(r, font=font("semi" if re.match(r"[A-Za-z0-9&@#%+./()-]", r) else "ta", size))
               for r in runs)


def _disclaimer(d):
    s = "Experimental nowcast from IMD radar, not an official forecast. Follow IMD warnings. Basemap © Esri"
    d.text((W / 2, H - 22), s, font=fit_font(d, s, "reg", 15, W - 96), fill=GREY, anchor="mm")


# --------------------------------------------------------- B: district ----
def render_district_card(nc: Nowcast, engine, center, view_lat_deg, out_path: Path, max_rows: int = 5) -> Path:
    card = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(card)
    t_now, day = _clock(nc)

    # header
    # header: black normally (clean, stands out on white feeds); turns red
    # only when heavy rain (40+ dBZ) is falling or due -- red stays an alarm.
    peak = max([p.now_dbz for p in nc.raining] + [p.soon_dbz for p in nc.raining + nc.coming] + [0])
    heavy = peak >= LIVE_MIN_DBZ
    head_bg, kicker, sub = (RED, (255, 255, 255), (255, 222, 226)) if heavy else (BLACK, YELLOW, (205, 205, 205))
    d.rectangle([0, 0, W, 196], fill=head_bg)
    d.text((48, 30), "MASRAINMAN NOWCAST", font=font("semi", 22), fill=kicker)
    title = f"{nc.region_ta} மழை நிலவரம்"
    mixed_text(d, (48, 100), title, 50, (255, 255, 255), latin="xbold", tamil="ta_x", max_w=720)
    d.text((49, 152), f"{nc.region_en} • rain now and next 60 minutes",
           font=fit_font(d, f"{nc.region_en} • rain now and next 60 minutes", "semi", 22, 720), fill=sub)
    d.text((W - 48, 52), t_now, font=font("xbold", 46), fill=(255, 255, 255), anchor="rm")
    d.text((W - 48, 110), f"{day} • IST", font=font("semi", 22), fill=sub, anchor="rm")

    # radar map (same panel as the regular cards)
    mw, mh = W - 96, 540
    panel, _ = render_panel(nc, engine, center, view_lat_deg, size=(mw, mh))
    _paste_rounded(card, panel, (48, 222))
    _colour_bar(card, engine, 48, W - 48, 782)
    d.text((48, 816), "லேசான", font=font("ta", 18), fill=GREY, anchor="lm")
    d.text((W - 48, 816), "அதி தீவிரம்", font=font("ta", 18), fill=GREY, anchor="rm")

    # table: one row per town, raining-now first (lists are already ranked)
    rows, seen = [], set()
    for p in list(nc.raining) + list(nc.coming):
        if p.en in seen:
            continue
        seen.add(p.en)
        now = p.now_dbz
        nxt = p.soon_dbz
        rows.append((p, now, nxt))
    rows = rows[:max_rows]
    cols = [48, 470, 750, W - 48]
    top, hh, rh = 840, 58, 62
    d.rounded_rectangle([cols[0], top, cols[3], top + hh + rh * max(1, len(rows))], 16, outline=LINE, width=2)
    d.rounded_rectangle([cols[0] + 1, top + 1, cols[3] - 1, top + hh], 15, fill=SOFT)
    d.rectangle([cols[0] + 1, top + hh - 16, cols[3] - 1, top + hh], fill=SOFT)
    for x, s in zip(cols, ["ஊர்", "இப்போது", "அடுத்த 60 நிமி."]):
        mixed_text(d, (x + 22, top + hh / 2), s, 26, INK, tamil="ta_x")
    for i, (p, now, nxt) in enumerate(rows):
        y = top + hh + i * rh
        if i:
            d.line([cols[0] + 2, y, cols[3] - 2, y], fill=(227, 232, 238), width=1)
        name = f"{p.ta} அருகே" if p.near else p.ta
        mixed_text(d, (cols[0] + 22, y + 26), name, 28, INK, tamil="ta_x", max_w=cols[1] - cols[0] - 30)
        d.text((cols[0] + 22, y + 52), ("Near " if p.near else "") + p.en, font=font("semi", 16), fill=GREY, anchor="lm")
        for j, (dbz, eta) in enumerate(((now, ""), (nxt, p.eta_clock if p not in nc.raining else ""))):
            x0 = cols[1 + j] + 18
            if dbz < 20 and j == 1 and p in nc.raining:
                s, bg = "—", (227, 232, 238)        # no projection for this town: neutral dash
            else:
                s = _chip_text(dbz)
                bg = band_color(engine, dbz) if dbz >= 20 else (227, 232, 238)
            fw = _mixed_w(d, s, 24)
            d.rounded_rectangle([x0, y + 14, x0 + fw + 32, y + 52], 19, fill=bg)
            mixed_text(d, (x0 + 16, y + 33), s, 24, _text_on(bg) if dbz >= 20 else (63, 76, 92), tamil="ta_x")
            if eta:
                d.text((x0 + fw + 40, y + 33), f"~{eta}", font=font("semi", 17), fill=GREY, anchor="lm")

    _brand_footer(card, d, H - 114, "பகிருங்கள் | பாதுகாப்பாக இருங்கள்")
    _disclaimer(d)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    card.save(out_path, "JPEG", quality=92, optimize=True)
    return out_path


# ------------------------------------------------------------- C: LIVE ----
def live_place(nc: Nowcast):
    """The town to headline on the LIVE card, or None if nothing heavy is falling now."""
    hits = [p for p in nc.raining if p.now_dbz >= LIVE_MIN_DBZ]
    if not hits:
        return None
    return max(hits, key=lambda p: (p.now_dbz, -p.priority))


def render_live_card(nc: Nowcast, engine, place, view_lat_deg, out_path: Path) -> Path:
    card = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(card)
    t_now, day = _clock(nc)

    # red LIVE strip
    d.rectangle([0, 0, W, 84], fill=RED)
    d.ellipse([48, 31, 70, 53], fill=(255, 255, 255))
    mixed_text(d, (86, 42), "LIVE  |  இப்போது மழை", 32, (255, 255, 255), latin="xbold", tamil="ta_x")
    d.text((W - 48, 42), f"{t_now} • {day}", font=font("semi", 26), fill=(255, 255, 255), anchor="rm")

    # the town, very big
    t = tier(place.now_dbz)
    name = f"{place.ta} அருகே" if place.near else place.ta
    mixed_text(d, (44, 170), name, 118, INK, latin="black", tamil="ta_x", max_w=W - 96)
    mixed_text(d, (48, 262), t[2], 44, RED, tamil="ta_x", max_w=W - 96)
    en = f"{t[1]} {'near' if place.near else 'at'} {place.en} right now"
    d.text((48, 308), en, font=fit_font(d, en, "semi", 26, W - 96), fill=GREY, anchor="lm")

    # full-width close radar map centred on the town
    mw, mh = W, 680
    panel, _ = render_panel(nc, engine, (place.lat, place.lon), view_lat_deg, size=(mw, mh))
    card.paste(panel.convert("RGB"), (0, 338))
    d.line([0, 338, W, 338], fill=LINE, width=3)
    d.line([0, 338 + mh, W, 338 + mh], fill=LINE, width=3)

    # motion / speed / next town
    if nc.motion_ok and nc.speed_kmh and nc.bearing_deg is not None and nc.speed_kmh >= 5:
        k = int(((nc.bearing_deg % 360) + 22.5) // 45) % 8
        mv_ta, mv_en = f"{DIRS_TA[k]} நோக்கி", f"moving {DIRS_EN_FULL[k].lower()}"
        sp_ta, sp_en = f"~{nc.speed_kmh:.0f} கி.மீ/மணி", "speed"
    else:
        mv_ta, mv_en, sp_ta, sp_en = "—", "motion not yet known", "—", "speed"
    nxt = next((p for p in nc.coming if p.en != place.en), None)
    nx_ta = nxt.ta if nxt else "—"
    nx_en = (f"next ~{nxt.eta_clock or str(nxt.eta_min) + ' min'}" if nxt else "no new town in 60 min")
    boxes = [("நகர்வு", mv_ta, mv_en), ("வேகம்", sp_ta, sp_en), ("அடுத்து", nx_ta, nx_en)]
    bw = (W - 96 - 32) / 3
    y0 = 338 + mh + 26
    for i, (lab, big, sub) in enumerate(boxes):
        x0 = 48 + i * (bw + 16)
        d.rounded_rectangle([x0, y0, x0 + bw, y0 + 140], 16, outline=LINE, width=2)
        mixed_text(d, (x0 + 20, y0 + 28), lab, 22, GREY, tamil="ta")
        mixed_text(d, (x0 + 20, y0 + 74), big, 34, INK, latin="xbold", tamil="ta_x", max_w=bw - 36)
        d.text((x0 + 20, y0 + 116), sub, font=fit_font(d, sub, "semi", 17, bw - 36), fill=GREY, anchor="lm")

    _brand_footer(card, d, H - 114, "பகிருங்கள் | பாதுகாப்பாக இருங்கள்")
    _disclaimer(d)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    card.save(out_path, "JPEG", quality=92, optimize=True)
    return out_path
