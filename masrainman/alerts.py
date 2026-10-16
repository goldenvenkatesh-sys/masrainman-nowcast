"""Bilingual (Tamil + English) nowcast text built from the engine's own
bot export (output/nowcast_bot.json + nowcast_bot_grid.bin.gz).

For every place in places.py we read the observed reflectivity and the
engine's motion-projected layers (+10 ... +90 min) around it, and classify:
  * raining now  -> intensity tier
  * coming soon  -> earliest lead time + strongest tier expected
The text is kept under Telegram's 1024-character photo-caption limit so the
same caption works on Telegram, Facebook and WhatsApp.
"""
from __future__ import annotations

import gzip
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import config
from .places import PLACES

BOT_JSON = config.OUTPUT_DIR / "nowcast_bot.json"
BOT_GRID = config.OUTPUT_DIR / "nowcast_bot_grid.bin.gz"

# (min dBZ, English, Tamil, emoji)
TIERS = [
    (50, "Very heavy / thunderstorm", "இடியுடன் கூடிய மிக கனமழை", "🟣"),
    (45, "Very heavy rain", "மிக கனமழை", "🔴"),
    (40, "Heavy rain", "கனமழை", "🟠"),
    (30, "Moderate rain", "மிதமான மழை", "🟡"),
    (20, "Light rain", "லேசான மழை", "🔵"),
]
DIRS_EN = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
DIRS_TA = ["வடக்கு", "வடகிழக்கு", "கிழக்கு", "தென்கிழக்கு",
           "தெற்கு", "தென்மேற்கு", "மேற்கு", "வடமேற்கு"]


def tier(dbz: float):
    for t in TIERS:
        if dbz >= t[0]:
            return t
    return None


@dataclass
class PlaceStatus:
    en: str
    ta: str
    lat: float
    lon: float
    priority: int
    state: str = "TN"
    now_dbz: float = 0.0
    eta_min: int | None = None     # earliest lead with rain >= ALERT_MIN_DBZ (if not raining now)
    soon_dbz: float = 0.0          # strongest expected within 60 min
    eta_clock: str = ""            # e.g. "7:01 PM"
    near: bool = False             # rain is in the area around the town, not over it


@dataclass
class Nowcast:
    generated_ist: str
    version: str
    motion_ok: bool
    speed_kmh: float | None
    bearing_deg: float | None
    raining: list[PlaceStatus] = field(default_factory=list)
    coming: list[PlaceStatus] = field(default_factory=list)
    any_echo: bool = False
    max_dbz: float = 0.0
    region_en: str = ""
    region_ta: str = ""
    hashtags: str = ""
    grid: object = None            # (layers array, grid meta) for the map renderer
    layer_times: dict = field(default_factory=dict)   # lead_min -> "6:11 PM"

    @property
    def peak_tier(self):
        return tier(self.max_dbz)


def _load_grid(doc: dict):
    g = doc.get("grid")
    if not g or not BOT_GRID.exists():
        return None, None
    raw = gzip.decompress(BOT_GRID.read_bytes())
    nl, nr, nc = len(g["layers"]), g["nrows"], g["ncols"]
    arr = np.frombuffer(raw, dtype=np.uint8)[: nl * nr * nc].reshape(nl, nr, nc)
    return arr, g


def _sample(arr2d: np.ndarray, g: dict, lat: float, lon: float, radius_km: float) -> float | None:
    """Max dBZ around a point, ignoring single isolated pixels (needs at
    least 2 nodes at that level). None if no radar covers the point."""
    step = g["step_deg"]
    r = round((g["lat_north"] - lat) / step)
    c = round((lon - g["lon_west"]) / step)
    dr = max(1, int(math.ceil(radius_km / (step * 111.0))))
    dc = max(1, int(math.ceil(radius_km / (step * 111.0 * math.cos(math.radians(lat))))))
    r0, r1, c0, c1 = max(r - dr, 0), min(r + dr + 1, g["nrows"]), max(c - dc, 0), min(c + dc + 1, g["ncols"])
    if r0 >= r1 or c0 >= c1:
        return None
    win = arr2d[r0:r1, c0:c1].astype(float)
    # true circle, not a square box (a box's corners reach ~40% further)
    rr, cc = np.mgrid[r0:r1, c0:c1]
    dkm = np.hypot((rr - r) * step * 111.0, (cc - c) * step * 111.0 * math.cos(math.radians(lat)))
    win = np.where(dkm <= radius_km, win, 255)
    covered = win[win != 255]
    if covered.size == 0:
        return None
    vals = np.sort(covered[covered > 0])
    if vals.size < 2:              # nothing, or one stray pixel -> treat as dry
        return 0.0
    return float(vals[-2])


TOWN_RADIUS_KM = 3.0      # rain within this distance counts as "at" the town
NEAR_RADIUS_KM = 15.0     # a storm cell this close to a town is reported as "near" it


def _km(a, b):
    dlat = (a[0] - b[0]) * 111.0
    dlon = (a[1] - b[1]) * 111.0 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dlat, dlon)


def _add_near_towns(nc: "Nowcast", doc: dict) -> None:
    """Every storm cell at/above ALERT_MIN_DBZ gets named, even when it sits
    between towns: it is reported as 'Near <closest town>' (within
    NEAR_RADIUS_KM) with that cell's own strength, so no cell on the radar
    is silently left out of the caption."""
    named = {p.en for p in nc.raining}
    best_by_town: dict[str, tuple] = {}
    for c in doc.get("cells") or []:
        dbz = float(c.get("max_dbz", 0))
        if dbz < config.ALERT_MIN_DBZ:
            continue
        town = min(PLACES, key=lambda t: _km((t[2], t[3]), (c["lat"], c["lon"])))
        if town[0] in named or _km((town[2], town[3]), (c["lat"], c["lon"])) > NEAR_RADIUS_KM:
            continue
        if dbz > best_by_town.get(town[0], (None, 0))[1]:
            best_by_town[town[0]] = (town, dbz)
    for town, dbz in best_by_town.values():
        nc.raining.append(PlaceStatus(town[0], town[1], town[2], town[3], town[4], town[5],
                                      now_dbz=dbz, near=True))
    nc.coming = [p for p in nc.coming if p.en not in best_by_town]


def build_nowcast() -> Nowcast | None:
    if not BOT_JSON.exists():
        return None
    doc = json.loads(BOT_JSON.read_text())
    m = doc.get("motion") or {}
    nc = Nowcast(generated_ist=doc.get("generated_ist", ""), version=doc.get("version", ""),
                 motion_ok=bool(m.get("available")), speed_kmh=m.get("overall_speed_kmh"),
                 bearing_deg=m.get("overall_bearing_deg"))
    areas = doc.get("rain_areas") or []
    nc.any_echo = bool(areas)
    nc.max_dbz = max([a.get("max_dbz", 0) for a in areas] + [0])

    arr, g = _load_grid(doc)
    if arr is None:
        return nc
    nc.grid = (arr, g)
    leads = {L["lead_min"]: i for i, L in enumerate(g["layers"]) if L["name"] != "observed"}
    obs_i = next((i for i, L in enumerate(g["layers"]) if L["name"] == "observed"), None)
    for L in g["layers"]:
        if L.get("valid_ist"):
            nc.layer_times[L["lead_min"]] = _clock(L["valid_ist"])
    for en, ta, lat, lon, pri, st in PLACES:
        ps = PlaceStatus(en, ta, lat, lon, pri, st)
        if obs_i is not None:
            v = _sample(arr[obs_i], g, lat, lon, TOWN_RADIUS_KM)
            if v is None:          # outside every radar's coverage -- say nothing
                continue
            ps.now_dbz = v
        for lead in sorted(k for k in leads if 0 < k <= 60):
            # neighbourhood widens with lead time (~5 km @30, 10 km @60), as the engine advises
            v = _sample(arr[leads[lead]], g, lat, lon, max(3.0, lead / 6.0)) or 0.0
            ps.soon_dbz = max(ps.soon_dbz, v)
            if ps.eta_min is None and v >= config.ALERT_MIN_DBZ:
                ps.eta_min = lead
                ps.eta_clock = nc.layer_times.get(lead, "")
        if ps.now_dbz >= config.ALERT_MIN_DBZ:
            nc.raining.append(ps)
        elif ps.eta_min is not None:
            nc.coming.append(ps)
    _add_near_towns(nc, doc)
    nc.raining.sort(key=lambda p: (-p.now_dbz, p.priority))
    nc.coming.sort(key=lambda p: (p.eta_min, -p.soon_dbz, p.priority))
    return nc


def _clock(valid_ist: str) -> str:
    """'16 Oct 13:08 IST' -> '1:08 PM'"""
    try:
        from datetime import datetime
        return datetime.strptime(valid_ist.split(" ")[2], "%H:%M").strftime("%I:%M %p").lstrip("0")
    except Exception:
        return ""


def for_region(nc: Nowcast, states: set, en: str, ta: str, hashtags: str = "",
               lat_band: tuple | None = None) -> Nowcast:
    """A copy of the nowcast that only talks about places in `states`
    (and, if given, with latitude inside lat_band = (south, north))."""
    from dataclasses import replace

    def keep(p):
        return p.state in states and (lat_band is None or lat_band[0] <= p.lat < lat_band[1])
    return replace(nc, raining=[p for p in nc.raining if keep(p)],
                   coming=[p for p in nc.coming if keep(p)], region_en=en, region_ta=ta,
                   hashtags=hashtags)


def _utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def _ist_short(generated_ist: str) -> str:
    # "16 Oct 2026 11:50 IST" -> "16 Oct, 11:50 AM"
    try:
        from datetime import datetime
        d = datetime.strptime(generated_ist.replace(" IST", ""), "%d %b %Y %H:%M")
        return d.strftime("%d %b, %I:%M %p").lstrip("0")
    except Exception:
        return generated_ist


def build_caption(nc: Nowcast, limit: int = 1000) -> str:
    """Telegram photo captions max out at 1024 UTF-16 units; we stay under
    `limit` by trimming the place lists, never the header/footer."""
    head = [f"🌧️ {config.BRAND_NAME} | ரேடார் மழை அறிவிப்பு"]
    if nc.region_en:
        head.append(f"📍 {nc.region_en} | {nc.region_ta}")
    head.append(f"🕒 {_ist_short(nc.generated_ist)} IST")
    foot = []
    if nc.motion_ok and nc.speed_kmh and nc.bearing_deg is not None and nc.speed_kmh >= 5:
        k = int(((nc.bearing_deg % 360) + 22.5) // 45) % 8
        foot.append(f"🧭 Rain clouds moving {DIRS_EN[k]} ~{nc.speed_kmh:.0f} km/h | "
                    f"மழை மேகங்கள் {DIRS_TA[k]} நோக்கி ~{nc.speed_kmh:.0f} கி.மீ/மணி வேகத்தில் நகர்கின்றன")
    foot.append("ℹ️ IMD radar based experimental nowcast. Follow official IMD warnings. "
                "| அதிகாரப்பூர்வ IMD அறிவிப்புகளைப் பின்பற்றவும்.")
    foot.append(f"{config.HASHTAGS} {nc.hashtags}".strip())

    if not nc.raining and not nc.coming:
        mid = (["🌤️ No significant rain on radar right now. | தற்போது ரேடாரில் குறிப்பிடத்தக்க மழை இல்லை."]
               if not nc.any_echo else
               ["🌦️ Only light/scattered echoes on radar. | ரேடாரில் லேசான, ஆங்காங்கே மழை மேகங்கள் மட்டும்."])
        return "\n".join(head + [""] + mid + [""] + foot)

    def now_line(p: PlaceStatus) -> str:
        t = tier(p.now_dbz)
        if p.near:
            return f"{t[3]} Near {p.en} – {t[1]} | {p.ta} அருகே – {t[2]}"
        return f"{t[3]} {p.en} – {t[1]} | {p.ta} – {t[2]}"

    def soon_line(p: PlaceStatus) -> str:
        t = tier(max(p.soon_dbz, config.ALERT_MIN_DBZ))
        when = f"~{p.eta_clock}" if p.eta_clock else f"~{p.eta_min} min"
        when_ta = f"~{p.eta_clock}" if p.eta_clock else f"~{p.eta_min} நிமி"
        return f"{t[3]} {p.en} {when} – {t[1]} | {p.ta} {when_ta} – {t[2]}"

    sections = [("🔴 RAINING NOW | இப்போது மழை", nc.raining, now_line),
                ("⏱️ NEXT 60 MIN | அடுத்த 60 நிமிடங்களில்", nc.coming, soon_line)]
    # Grow the lists one line at a time (alternating sections, important
    # places first) until the caption would exceed the limit.
    picks = {0: [], 1: []}
    queues = {i: list(s[1]) for i, s in enumerate(sections)}

    def render() -> str:
        body = []
        for i, (title, items, fn) in enumerate(sections):
            if not picks[i]:
                continue
            body += [title] + [fn(p) for p in picks[i]]
            more = len(items) - len(picks[i])
            if more > 0:
                body.append(f"   + {more} more places | மேலும் {more} இடங்கள்")
            body.append("")
        return "\n".join(head + [""] + body + foot)

    progressed = True
    while progressed:
        progressed = False
        for i in (0, 1):
            if queues[i]:
                picks[i].append(queues[i].pop(0))
                if _utf16_len(render()) > limit:
                    queues[i].insert(0, picks[i].pop())
                    queues[i] = []          # this section is full
                else:
                    progressed = True
    return render()


def top_places_for_card(nc: Nowcast, n: int = 6) -> list[tuple[PlaceStatus, str]]:
    """(place, 'now'|'soon') pairs to label on the image."""
    out = [(p, "now") for p in nc.raining[:n]]
    out += [(p, "soon") for p in nc.coming[: max(0, n - len(out))]]
    return out
