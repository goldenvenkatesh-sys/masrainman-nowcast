"""The MasRainman nowcast card (1080x1350, 4:5 -- fits Facebook, WhatsApp
and Telegram feeds). Close-up, white Leaflet-style basemap, in the style of
a classic radar nowcast plot:

  * coloured blocks  = rain on radar NOW (the engine's 2 km grid, coarsened)
  * dashed outline   = where that rain is projected to be in ~90 min
  * arrowheads       = storm-cell motion
  * dotted circles   = radar range rings
  * bold labels      = towns getting rain now / soon (EN + TA, with ETA)
  * district / state / coast lines drawn on top
  * neon colour pill (EN + TA tier names) under the map
"""
from __future__ import annotations

import io
import json
import math
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont

from . import config
from .alerts import Nowcast

W, H = 1080, 1350
MAP_BOX = (40, 236, 1040, 1086)          # left, top, right, bottom of the map panel
LOGO = config.ASSETS / "masrainman_logo.png"
TILE_CACHE = config.ROOT / "tiles_cache" / "esri_lightgray"   # new folder: old CARTO tiles had "API KEY REQUIRED" on them
BOUNDARIES = config.ASSETS / "boundaries.json"

INK = (17, 20, 28)
GREY = (90, 98, 112)
WHITE = (255, 255, 255)
ACCENT = (225, 0, 160)

# White/light-grey Leaflet basemap that needs no API key (Esri World Light
# Gray Canvas, 256 px tiles). Override with BASEMAP_URL (+ BASEMAP_TILE_PX).
BASEMAP_URL = os.environ.get("BASEMAP_URL") or (
    "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}")
TILE_PX = int(os.environ.get("BASEMAP_TILE_PX", "256"))
COARSEN = int(os.environ.get("CARD_BLOCK", "2"))     # 2 x 0.02 deg = ~4.4 km blocks

LEGEND_EN = ["Drizzle", "Light", "Moderate", "Mod+", "Heavy", "V.Heavy", "Intense", "Extreme"]
LEGEND_TA = ["தூறல்", "லேசான", "மிதமான", "மிதம்+", "கனமழை", "மிக கனம்", "தீவிரம்", "அதி தீவிரம்"]
SHORT_EN = {50: "Intense", 45: "Very heavy", 40: "Heavy", 30: "Moderate", 20: "Light"}


# ---------------------------------------------------------------- fonts ----
def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    files = {"black": "Poppins-Black.ttf", "xbold": "Poppins-ExtraBold.ttf",
             "semi": "Poppins-SemiBold.ttf", "reg": "Poppins-Regular.ttf",
             "ta": "NotoSansTamil-Bold.ttf", "ta_x": "NotoSansTamil-ExtraBold.ttf",
             "ta_reg": "NotoSansTamil-Regular.ttf"}
    path = config.FONTS / files[name]
    try:   # RAQM shaping is what joins Tamil vowel signs correctly
        return ImageFont.truetype(str(path), size, layout_engine=ImageFont.Layout.RAQM)
    except Exception:
        return ImageFont.truetype(str(path), size)


def text_w(draw, s, f):
    b = draw.textbbox((0, 0), s, font=f)
    return b[2] - b[0]


def fit_font(draw, s, name, size, max_w, min_size=10):
    while size > min_size:
        f = font(name, size)
        if text_w(draw, s, f) <= max_w:
            return f
        size -= 1
    return font(name, min_size)


def hex_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def band_color(engine, dbz: float):
    return hex_rgb(engine.DBZ_BAND_COLORS[max(0, min(int((dbz - 20) // 5), len(engine.DBZ_BAND_COLORS) - 1))])


# ------------------------------------------------------------ geometry ----
def _world(lon, lat):
    """Web-Mercator 'world pixel' coords at zoom 0 (0..256)."""
    lat = max(min(lat, 85.05), -85.05)
    x = (lon + 180.0) / 360.0 * 256.0
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * 256.0
    return x, y


class Frame:
    """lon/lat -> pixel inside a w x h panel centred on (lat, lon)."""

    def __init__(self, center, view_lat_deg, w, h):
        lat0, lon0 = center
        _, top = _world(lon0, lat0 + view_lat_deg / 2)
        _, bot = _world(lon0, lat0 - view_lat_deg / 2)
        self.w, self.h = w, h
        self.s = h / (bot - top)                       # pixels per world unit
        cx, cy = _world(lon0, lat0)
        self.x0, self.y0 = cx - w / 2 / self.s, cy - h / 2 / self.s

    def px(self, lon, lat):
        x, y = _world(lon, lat)
        return (x - self.x0) * self.s, (y - self.y0) * self.s

    def lonlat(self, px, py):
        x, y = px / self.s + self.x0, py / self.s + self.y0
        return x / 256 * 360 - 180, math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / 256))))

    def bounds(self):
        (w_, n), (e, s) = self.lonlat(0, 0), self.lonlat(self.w, self.h)
        return s, w_, n, e


# ------------------------------------------------------------ basemap ----
_tile_fail = {"n": 0}


def _tile(z, x, y) -> Image.Image | None:
    path = TILE_CACHE / str(z) / str(x) / f"{y}.png"
    if path.exists():
        return Image.open(path).convert("RGB")
    if _tile_fail["n"] >= 3:
        return None
    try:
        r = requests.get(BASEMAP_URL.format(s="abcd"[(x + y) % 4], z=z, x=x, y=y), timeout=15,
                         headers={"User-Agent": "MasRainman-nowcast/1.0"})
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path, optimize=True)
        return img
    except Exception as e:
        _tile_fail["n"] += 1
        print(f"[card] tile {z}/{x}/{y} failed: {type(e).__name__}")
        return None


def basemap(frame: Frame, engine) -> Image.Image:
    out = Image.new("RGB", (frame.w, frame.h), (250, 250, 248))
    z = int(max(5, min(13, round(math.log2(frame.s * 256 / TILE_PX)))))
    n = 2 ** z
    tw = 256.0 / n                                              # one tile, in world units
    tx0, ty0 = int(frame.x0 // tw), int(frame.y0 // tw)
    tx1, ty1 = int((frame.x0 + frame.w / frame.s) // tw), int((frame.y0 + frame.h / frame.s) // tw)
    size = int(math.ceil(tw * frame.s)) + 1
    got = 0
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            t = _tile(z, tx % n, ty)
            if t is None:
                continue
            got += 1
            out.paste(t.resize((size, size), Image.LANCZOS),
                      (int(round((tx * tw - frame.x0) * frame.s)), int(round((ty * tw - frame.y0) * frame.s))))
    if got:
        return out
    # Fallback: the engine's own (lower-resolution) basemap, washed out to near-white.
    print("[card] basemap tiles unavailable -- using the engine's cached basemap")
    mosaic = engine.fetch_basemap_mosaic()
    (lon_l, lat_t), (lon_r, lat_b) = frame.lonlat(0, 0), frame.lonlat(frame.w, frame.h)
    x0, y0 = engine.mosaic_lonlat_to_px(lon_l, lat_t)
    x1, y1 = engine.mosaic_lonlat_to_px(lon_r, lat_b)
    pad = 2000                                       # areas outside the cached mosaic stay white
    big = Image.new("RGB", (mosaic.width + 2 * pad, mosaic.height + 2 * pad), (250, 250, 248))
    big.paste(mosaic, (pad, pad))
    crop = big.crop((int(x0) + pad, int(y0) + pad, int(math.ceil(x1)) + pad, int(math.ceil(y1)) + pad)).resize(
        (frame.w, frame.h), Image.BICUBIC)
    g = np.asarray(crop.convert("L"), dtype=float)
    a = np.asarray(crop, dtype=float)
    wash = 0.55 * a + 0.45 * g[..., None]          # desaturate
    wash = 255 - (255 - wash) * 0.55                # lighten towards white
    return Image.fromarray(wash.clip(0, 255).astype(np.uint8))


# ------------------------------------------------------------- layers ----
def coarsen(layer: np.ndarray, k: int) -> np.ndarray:
    """k x k blocks, each taking the SECOND-highest of its nodes (255 = no
    coverage counts as 0). Plain max-pooling let one hot pixel (a clutter
    speck, or a column-max echo high up) paint a whole 4 km block, on top of
    the engine already taking the max across radars -- the card ran hotter
    and wider than IMD's own images. Needing two nodes to agree drops lone
    pixels but keeps real cores, which are always several pixels wide.
    No blurring, so peak intensities are not washed out."""
    a = layer.astype(float)
    a[a == 255] = 0
    if k <= 1:
        return a
    r, c = (a.shape[0] // k) * k, (a.shape[1] // k) * k
    blocks = a[:r, :c].reshape(r // k, k, c // k, k).transpose(0, 2, 1, 3).reshape(r // k, c // k, k * k)
    return np.sort(blocks, axis=2)[:, :, -2]


def despeckle(a: np.ndarray) -> np.ndarray:
    """Weak blocks need >= 3 rainy neighbours; drops IMD range-ring streaks."""
    from scipy import ndimage
    echo = (a >= 20).astype(int)
    nb = ndimage.convolve(echo, np.ones((3, 3), int), mode="constant") - echo
    return np.where((a >= 30) | ((a >= 20) & (nb >= 3)), a, 0)


def projected_areas(a: np.ndarray, min_blocks: int = 6) -> np.ndarray:
    """Where rain >= ALERT_MIN_DBZ is projected, as tidy areas: gaps closed,
    specks (< min_blocks blocks) dropped -- so the dashed outlines trace
    rain areas instead of boxing every stray block."""
    from scipy import ndimage
    m = ndimage.binary_closing(a >= config.ALERT_MIN_DBZ, structure=np.ones((3, 3)))
    m = ndimage.binary_fill_holes(m)
    lab, n = ndimage.label(m)
    if n == 0:
        return m
    sizes = ndimage.sum(m, lab, np.arange(1, n + 1))
    keep = np.isin(lab, 1 + np.nonzero(sizes >= min_blocks)[0])
    return keep


def block_geometry(g, k, frame, shape):
    """Pixel x of every block column edge and y of every block row edge."""
    step = g["step_deg"] * k
    half = g["step_deg"] / 2
    lats = g["lat_north"] + half - np.arange(shape[0] + 1) * step
    lons = g["lon_west"] - half + np.arange(shape[1] + 1) * step
    ys = np.array([frame.px(lons[0], la)[1] for la in lats])
    xs = np.array([frame.px(lo, lats[0])[0] for lo in lons])
    return xs, ys


def draw_blocks(img, a, xs, ys, engine):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for r, c in zip(*np.nonzero(a >= 20)):
        x0, x1, y0, y1 = xs[c], xs[c + 1], ys[r], ys[r + 1]
        if x1 < 0 or y1 < 0 or x0 > img.width or y0 > img.height:
            continue
        d.rectangle([x0, y0, x1, y1], fill=band_color(engine, a[r, c]) + (235,))
    img.alpha_composite(layer)


def draw_dashed_outline(d, mask, xs, ys, w, h, color=INK, width=3):
    """Stair-step outline of a block mask, dashed (each block edge is one
    dash, which gives the classic dashed-polygon look)."""
    pad = np.pad(mask, 1)
    for r, c in zip(*np.nonzero(mask)):
        x0, x1, y0, y1 = xs[c], xs[c + 1], ys[r], ys[r + 1]
        if x1 < -5 or y1 < -5 or x0 > w + 5 or y0 > h + 5:
            continue
        edges = []
        if not pad[r, c + 1]:
            edges.append(((x0, y0), (x1, y0)))
        if not pad[r + 2, c + 1]:
            edges.append(((x0, y1), (x1, y1)))
        if not pad[r + 1, c]:
            edges.append(((x0, y0), (x0, y1)))
        if not pad[r + 1, c + 2]:
            edges.append(((x1, y0), (x1, y1)))
        for (ax, ay), (bx, by) in edges:
            d.line([(ax + (bx - ax) * .15, ay + (by - ay) * .15), (ax + (bx - ax) * .85, ay + (by - ay) * .85)],
                   fill=color, width=width)


def dotted_circle(d, frame, lat, lon, km, color=INK):
    n = 240
    for i in range(0, n, 2):
        th = 2 * math.pi * i / n
        x, y = frame.px(lon + km / (111.0 * math.cos(math.radians(lat))) * math.sin(th),
                        lat + km / 111.0 * math.cos(th))
        d.ellipse([x - 1.8, y - 1.8, x + 1.8, y + 1.8], fill=color)


def arrowhead(d, x, y, bearing, size=17):
    """Navigation-style arrowhead pointing where the cell is moving."""
    th = math.radians(bearing)
    ux, uy = math.sin(th), -math.cos(th)        # screen-space unit vector (north = up)
    px_, py_ = -uy, ux
    tip = (x + ux * size, y + uy * size)
    left = (x - ux * size * .7 + px_ * size * .75, y - uy * size * .7 + py_ * size * .75)
    notch = (x - ux * size * .25, y - uy * size * .25)
    right = (x - ux * size * .7 - px_ * size * .75, y - uy * size * .7 - py_ * size * .75)
    d.polygon([tip, left, notch, right], fill=INK, outline=WHITE, width=2)


def draw_boundaries(d, frame):
    if not BOUNDARIES.exists():
        return
    b = json.loads(BOUNDARIES.read_text())
    s, w_, n, e = frame.bounds()
    for key, width, color in (("districts", 2, (70, 74, 84)), ("outline", 4, (25, 28, 34))):
        for line in b.get(key, []):
            lo_, la_ = [p[0] for p in line], [p[1] for p in line]
            if max(lo_) < w_ - .2 or min(lo_) > e + .2 or max(la_) < s - .2 or min(la_) > n + .2:
                continue
            d.line([frame.px(lo, la) for lo, la in line], fill=color, width=width, joint="curve")


def mixed_text(d, xy, text, size, fill, latin="semi", tamil="ta", anchor="lm", max_w=None):
    """Draw text that mixes Tamil and Latin (Noto Sans Tamil has no Latin
    letters, Poppins has no Tamil): split into runs and use the right font
    for each. anchor: 'lm' (left-middle) or 'mm' (centre-middle)."""
    import re
    runs = [r for r in re.split(r"([A-Za-z0-9&@#%+./()-]+(?: [A-Za-z0-9&@#%+./()-]+)*)", text) if r]
    while True:
        fonts = [font(latin if re.match(r"[A-Za-z0-9&@#%+./()-]", r) else tamil, size) for r in runs]
        widths = [d.textlength(r, font=f) for r, f in zip(runs, fonts)]
        if max_w is None or sum(widths) <= max_w or size <= 10:
            break
        size -= 1
    x, y = xy
    if anchor == "mm":
        x -= sum(widths) / 2
    for r, f, w in zip(runs, fonts, widths):
        d.text((x, y), r, font=f, fill=fill, anchor="lm")
        x += w


def halo_text(d, xy, s, f, fill=INK, stroke=5, anchor="mm"):
    d.text(xy, s, font=f, fill=fill, stroke_width=stroke, stroke_fill=WHITE, anchor=anchor)


# ------------------------------------------------------------- the card ----
def make_default_logo(path: Path = LOGO, size: int = 512):
    """Placeholder badge (gradient disc + raindrop + MR). Replace
    assets/masrainman_logo.png with the real logo any time."""
    yy, xx = np.mgrid[0:size, 0:size]
    t = ((xx + yy) / (2 * size))[..., None]
    rgb = (np.array([0, 168, 255]) * (1 - t) + np.array([255, 0, 200]) * t).astype(np.uint8)
    mask = ((xx - size / 2) ** 2 + (yy - size / 2) ** 2) <= (size / 2 - 4) ** 2
    img = Image.fromarray(np.dstack([rgb, (mask * 255).astype(np.uint8)]), "RGBA")
    d = ImageDraw.Draw(img)
    s = size
    d.ellipse([s * .33, s * .30, s * .67, s * .64], fill=WHITE)
    d.polygon([(s * .5, s * .10), (s * .345, s * .42), (s * .655, s * .42)], fill=WHITE)
    f = font("black", int(s * .17))
    d.text(((s - text_w(d, "MR", f)) / 2, s * .66), "MR", font=f, fill=WHITE)
    d.ellipse([s * .42, s * .40, s * .58, s * .56], fill=(0, 140, 230))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return img


def render_card(nc: Nowcast, engine, center, view_lat_deg, title_en, title_ta, out_path: Path) -> Path:
    from .places import PLACES
    from .regions import describe

    arr, g = nc.grid
    names = [L["name"] for L in g["layers"]]
    obs = arr[names.index("observed")]
    proj_name = max((n for n in names if n.startswith("plus_")), key=lambda n: int(n[5:]), default=None)
    proj = arr[names.index(proj_name)] if proj_name else None
    proj_lead = int(proj_name[5:]) if proj_name else None

    mw, mh = MAP_BOX[2] - MAP_BOX[0], MAP_BOX[3] - MAP_BOX[1]
    frame = Frame(center, view_lat_deg, mw, mh)
    panel = basemap(frame, engine).convert("RGBA")

    a_now = despeckle(coarsen(obs, COARSEN))
    xs, ys = block_geometry(g, COARSEN, frame, a_now.shape)
    draw_blocks(panel, a_now, xs, ys, engine)
    d = ImageDraw.Draw(panel)
    draw_boundaries(d, frame)
    if proj is not None:
        draw_dashed_outline(d, projected_areas(coarsen(proj, COARSEN)), xs, ys, mw, mh)

    # radar range rings + site symbols
    s_, w_, n_, e_ = frame.bounds()
    try:
        doc = json.loads((config.OUTPUT_DIR / "nowcast_bot.json").read_text())
    except Exception:
        doc = {}
    rings = set()
    for r in doc.get("radars", []):
        if r.get("used"):
            site = engine.site_for(r["product"])
            rings.add((site["site_lat"], site["site_lon"], engine.PRODUCTS[r["product"]]["range_km"]))
    for lat, lon, km in sorted(rings):
        if lat + km / 111 > s_ and lat - km / 111 < n_:
            dotted_circle(d, frame, lat, lon, km)
    for lat, lon in {(r[0], r[1]) for r in rings}:
        x, y = frame.px(lon, lat)
        if 0 <= x <= mw and 0 <= y <= mh:
            d.ellipse([x - 11, y - 11, x + 11, y + 11], fill=WHITE, outline=INK, width=3)
            d.ellipse([x - 4, y - 4, x + 4, y + 4], fill=INK)

    # motion arrowheads
    for c in doc.get("cells", []):
        if c.get("motion_reliable") and c.get("bearing_deg") is not None:
            x, y = frame.px(c["lon"], c["lat"])
            if 15 < x < mw - 15 and 15 < y < mh - 15:
                arrowhead(d, x, y, c["bearing_deg"])

    # town labels: affected places big (EN / TA / intensity + ETA), others small
    occupied = []

    def free(r, m=6):
        return 0 < r[0] and r[2] < mw and 0 < r[1] and r[3] < mh and all(
            r[2] + m < o[0] or r[0] - m > o[2] or r[3] + m < o[1] or r[1] - m > o[3] for o in occupied)

    f_name, f_ta, f_int = font("xbold", 27), font("ta", 21), font("xbold", 23)
    affected = [(p, "now") for p in nc.raining] + [(p, "soon") for p in nc.coming]
    affected.sort(key=lambda t: (t[0].priority, -(t[0].now_dbz if t[1] == "now" else t[0].soon_dbz)))
    shown = set()
    for p, kind in affected:
        x, y = frame.px(p.lon, p.lat)
        if not (0 < x < mw and 0 < y < mh):
            continue
        dbz = p.now_dbz if kind == "now" else p.soon_dbz
        lvl = max([k for k in SHORT_EN if dbz >= k] + [20])
        line3 = SHORT_EN[lvl] + ("" if kind == "now" else f" ~{p.eta_clock or str(p.eta_min) + ' min'}")
        name_en, name_ta = (f"Near {p.en}", f"{p.ta} அருகே") if getattr(p, "near", False) else (p.en, p.ta)
        lines = [(name_en, f_name, INK), (name_ta, f_ta, (40, 44, 56)), (line3, f_int, INK)]
        bw, bh = max(text_w(d, s, f) for s, f, _ in lines) + 10, 88
        box = None
        for cy in (y + 6 + bh / 2, y - 6 - bh / 2):           # below the dot, else above
            cand = (x - bw / 2, cy - bh / 2, x + bw / 2, cy + bh / 2)
            if free(cand):
                box = cand
                break
        if box is None:
            continue
        occupied.append(box)
        shown.add(p.en)
        d.ellipse([x - 5, y - 5, x + 5, y + 5], fill=INK, outline=WHITE, width=2)
        ty = box[1] + 15
        for s, f, col in lines:
            halo_text(d, (x, ty), s, f, fill=col, stroke=5)
            ty += 29
    f_small = font("semi", 19)
    for en, ta, lat, lon, pri, st in sorted(PLACES, key=lambda t: t[4]):
        if en in shown:
            continue
        x, y = frame.px(lon, lat)
        tw_ = text_w(d, en, f_small)
        box = (x - tw_ / 2 - 4, y - 30, x + tw_ / 2 + 4, y + 4)
        if not free(box, 4):
            continue
        occupied.append(box)
        d.ellipse([x - 4, y - 4, x + 4, y + 4], fill=INK)
        halo_text(d, (x, y - 14), en, f_small, fill=(30, 34, 44), stroke=4)

    # ------------------------------------------------------- full card ----
    card = Image.new("RGBA", (W, H), WHITE)
    card.alpha_composite(panel, (MAP_BOX[0], MAP_BOX[1]))
    d = ImageDraw.Draw(card)
    d.rectangle(MAP_BOX, outline=INK, width=3)

    # header
    if not LOGO.exists():
        make_default_logo()
    card.alpha_composite(Image.open(LOGO).convert("RGBA").resize((86, 86), Image.LANCZOS), (40, 24))
    d.text((140, 20), config.BRAND_NAME.upper(), font=font("black", 34), fill=INK)
    d.text((141, 64), "Experimental Rainfall Nowcast based on IMD Radar", font=font("semi", 20), fill=GREY)
    mixed_text(d, (141, 102), "IMD ரேடார் அடிப்படையிலான மழை முன்னறிவிப்பு (சோதனை)", 17, GREY)
    t_now = nc.layer_times.get(0) or ""
    if not t_now:
        try:
            t_now = datetime.strptime(nc.generated_ist.replace(" IST", ""), "%d %b %Y %H:%M").strftime("%I:%M %p").lstrip("0")
        except Exception:
            t_now = ""
    t_proj = nc.layer_times.get(proj_lead, "") if proj_lead else ""
    try:
        day = datetime.strptime(nc.generated_ist.replace(" IST", ""), "%d %b %Y %H:%M").strftime("%d %b").upper()
    except Exception:
        day = ""
    fb, fs = font("xbold", 30), font("semi", 16)
    cw = max(text_w(d, t_now, fb), text_w(d, day + " • IST", fs) + 16) + 36
    d.rounded_rectangle([W - 40 - cw, 26, W - 40, 106], 18, fill=INK)
    d.text((W - 40 - cw + 18, 28), t_now, font=fb, fill=WHITE)
    d.ellipse([W - 40 - cw + 18, 80, W - 40 - cw + 28, 90], fill=(255, 40, 80))
    d.text((W - 40 - cw + 34, 74), day + " • IST", font=fs, fill=(200, 205, 215))

    peak = max([p.now_dbz for p in nc.raining] + [p.soon_dbz for p in nc.coming] + [0])
    den, dta = describe(peak)
    t1, t2 = f"{title_en}: {den}", f"{title_ta}: {dta}"
    d.text((W / 2, 140), t1, font=fit_font(d, t1, "xbold", 30, 1000), fill=INK, anchor="mm")
    mixed_text(d, (W / 2, 175), t2, 21, (200, 0, 120), anchor="mm", max_w=1000)
    key = (f"Colours: now {t_now}     Dashed: ~{t_proj}     Arrows: motion" if t_proj
           else f"Colours: rain on radar at {t_now}  (motion needs one more radar frame)")
    d.text((W / 2, 210), key, font=fit_font(d, key, "reg", 21, 1000), fill=INK, anchor="mm")

    # colour pill legend
    n = len(engine.DBZ_BAND_COLORS)
    x0, x1 = 60, W - 60
    seg = (x1 - x0) / n
    ytop = MAP_BOX[3] + 22
    ybar, bh = ytop + 28, 28
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([x0, ybar + 4, x1, ybar + bh + 4], bh // 2, fill=(0, 0, 0, 45))
    card.alpha_composite(shadow)
    bar = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bar)
    for i, hc in enumerate(engine.DBZ_BAND_COLORS):
        bd.rectangle([x0 + i * seg, ybar, x0 + (i + 1) * seg, ybar + bh], fill=hex_rgb(hc) + (255,))
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).rounded_rectangle([x0, ybar, x1, ybar + bh], bh // 2, fill=255)
    bar.putalpha(m)
    card.alpha_composite(bar)
    d = ImageDraw.Draw(card)
    for i in range(n):
        cx = x0 + (i + 0.5) * seg
        d.text((cx, ytop + 12), LEGEND_EN[i], font=fit_font(d, LEGEND_EN[i], "xbold", 19, seg - 6),
               fill=INK, anchor="mm")
        d.text((cx, ybar + bh + 17), LEGEND_TA[i], font=fit_font(d, LEGEND_TA[i], "ta", 17, seg - 6),
               fill=(60, 64, 76), anchor="mm")
        d.text((x0 + i * seg, ybar + bh + 42), f"{20 + 5 * i}{'+' if i == n - 1 else ''}",
               font=font("reg", 15), fill=GREY, anchor="mm" if i else "lm")
    d.text((x1, ybar + bh + 42), "dBZ", font=font("reg", 15), fill=GREY, anchor="rm")

    # footer
    disc = "Extrapolated from current storm motion, not a guaranteed forecast. Follow official IMD warnings."
    d.text((40, H - 66), disc, font=fit_font(d, disc, "reg", 17, 1000), fill=GREY)
    d.text((40, H - 38), "IMD radar data. Basemap © Esri, HERE, Garmin, © OpenStreetMap contributors", font=font("reg", 16), fill=GREY)
    d.text((W - 40, H - 40), config.BRAND_HANDLE, font=font("xbold", 22), fill=ACCENT, anchor="ra")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    card.convert("RGB").save(out_path, "JPEG", quality=92, optimize=True)
    return out_path
