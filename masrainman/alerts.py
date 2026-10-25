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
    # radar -> last frame time ("22 Oct 16:54 IST") for every radar whose
    # IMD feed has gone stale this cycle (no product of it used); see
    # _down_radars(). for_region() narrows it to the radars covering that
    # region, and build_caption() then adds a "radar not updating" line.
    down_radars: dict = field(default_factory=dict)

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


CORE_MIN_DBZ = 35.0       # a grid peak this strong counts as a storm core
CORE_SPACING_KM = 12.0    # one core per ~12 km, so one storm isn't named five times


def _grid_cores(nc: "Nowcast") -> list[tuple[float, float, float]]:
    """Storm cores read straight off the observed radar grid: local peaks
    >= CORE_MIN_DBZ with some echo around them. The engine's own tracked
    cells can miss storms (far from the radar, or newly formed), and a storm
    sitting between two towns was then never named at all -- this catches
    those, wherever any radar sees them."""
    if nc.grid is None:
        return []
    from scipy import ndimage
    arr, g = nc.grid
    names = [L["name"] for L in g["layers"]]
    if "observed" not in names:
        return []
    a = arr[names.index("observed")].astype(float)
    a[a == 255] = 0
    step = g["step_deg"]
    k = max(3, int(round(CORE_SPACING_KM / (step * 111.0))) | 1)
    strong = a >= CORE_MIN_DBZ
    support = ndimage.uniform_filter(strong.astype(float), size=3) * 9   # strong nodes in the 3x3 around
    peaks = strong & (a == ndimage.maximum_filter(a, size=k)) & (support >= 2)
    out = []
    for r, c in zip(*np.nonzero(peaks)):
        out.append((g["lat_north"] - r * step, g["lon_west"] + c * step, float(a[r, c])))
    return out


def _add_near_towns(nc: "Nowcast", doc: dict) -> None:
    """Every storm cell at/above ALERT_MIN_DBZ gets named, even when it sits
    between towns: it is reported as 'Near <closest town>' (within
    NEAR_RADIUS_KM) with that cell's own strength, so no cell on the radar
    is silently left out of the caption."""
    named = {p.en for p in nc.raining}
    best_by_town: dict[str, tuple] = {}
    spots = [(c["lat"], c["lon"], float(c.get("max_dbz", 0))) for c in doc.get("cells") or []]
    spots += _grid_cores(nc)
    for lat, lon, dbz in spots:
        if dbz < config.ALERT_MIN_DBZ:
            continue
        c = {"lat": lat, "lon": lon}
        town = min(PLACES, key=lambda t: _km((t[2], t[3]), (c["lat"], c["lon"])))
        if town[0] in named or _km((town[2], town[3]), (c["lat"], c["lon"])) > NEAR_RADIUS_KM:
            continue
        if dbz > best_by_town.get(town[0], (None, 0))[1]:
            best_by_town[town[0]] = (town, dbz)
    for town, dbz in best_by_town.values():
        nc.raining.append(PlaceStatus(town[0], town[1], town[2], town[3], town[4], town[5],
                                      now_dbz=dbz, near=True))
    nc.coming = [p for p in nc.coming if p.en not in best_by_town]


# Radar names for the "not updating" caption line (radar keys as in the
# engine's RADAR_SITES / nowcast_bot.json "radars").
RADAR_NAMES = {
    "karaikal": ("Karaikal", "காரைக்கால்"),
    "chennai": ("Chennai", "சென்னை"),
    "niot": ("NIOT Chennai", "NIOT சென்னை"),
    "mangalore": ("Mangaluru", "மங்களூரு"),
    "trivandrum": ("Thiruvananthapuram", "திருவனந்தபுரம்"),
    "kochi": ("Kochi", "கொச்சி"),
}


def _down_radars(doc: dict) -> dict:
    """{radar: last frame time} for radars that IMD has stopped updating --
    every product of that radar is stale and left off the map this cycle."""
    by_radar: dict = {}
    for r in doc.get("radars") or []:
        by_radar.setdefault(r.get("radar"), []).append(r)
    down = {}
    for radar, rows in by_radar.items():
        if radar and not any(x.get("used") for x in rows):
            newest = max(rows, key=lambda x: x.get("obs_time_utc") or "")
            down[radar] = newest.get("obs_time_ist") or ""
    return down


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
    nc.down_radars = _down_radars(doc)

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
               zone=None, radars=None) -> Nowcast:
    """A copy of the nowcast that only talks about places in `states`
    (and, if given, for which zone(lat, lon) is true). `radars` = the radars
    that cover this region; only those can trigger the "not updating" line."""
    from dataclasses import replace

    def keep(p):
        return p.state in states and (zone is None or zone(p.lat, p.lon))
    down = {r: t for r, t in nc.down_radars.items() if radars and r in radars}
    return replace(nc, raining=[p for p in nc.raining if keep(p)],
                   coming=[p for p in nc.coming if keep(p)], region_en=en, region_ta=ta,
                   hashtags=hashtags, down_radars=down)


def radar_down_line(nc: Nowcast) -> str:
    """'⚠️ காரைக்கால் ரேடார் ... | Karaikal radar not updating ...' or ''."""
    if not nc.down_radars:
        return ""
    keys = [k for k in RADAR_NAMES if k in nc.down_radars] + \
           [k for k in nc.down_radars if k not in RADAR_NAMES]
    ta = ", ".join(RADAR_NAMES.get(k, (k.title(), k.title()))[1] for k in keys)
    en = ", ".join(RADAR_NAMES.get(k, (k.title(), k.title()))[0] for k in keys)
    times = [nc.down_radars[k] for k in keys if nc.down_radars[k]]
    since = f" (last image {times[0].replace(' IST', '')} IST)" if len(keys) == 1 and times else ""
    plural = "s" if len(keys) > 1 else ""
    return (f"⚠️ {ta} ரேடார் IMD-யிடமிருந்து புதுப்பிக்கப்படவில்லை – சில இடங்களில் மழை விடுபடலாம். | "
            f"{en} radar{plural} not updating from IMD{since} – rain in parts of this area may be missed.")


def _utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def _ist_short(generated_ist: str) -> str:
    # "16 Oct 2026 11:50 IST" -> "16 Oct, 11:50 AM"
    try:
        from datetime import datetime
        d = datetime.strptime(generated_ist.replace(" IST", ""), "%d %b %Y %H:%M")
        return d.strftime("%d %b, %I:%M %p").lstrip("0").replace(", 0", ", ")
    except Exception:
        return generated_ist


# Sentence wording per tier (index matches TIERS): Tamil phrase, English phrase.
_TIER_SENT = {
    50: ("இடியுடன் கூடிய மிக கனமழை", "very heavy rain with thunderstorms"),
    45: ("மிக கனமழை", "very heavy rain"),
    40: ("கனமழை", "heavy rain"),
    30: ("மிதமான மழை", "moderate rain"),
    20: ("லேசான மழை", "light rain"),
}


def _join(items: list[str], ta: bool) -> str:
    """'A, B மற்றும் C' / 'A, B and C'."""
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + (" மற்றும் " if ta else " and ") + items[-1]


def _time_phrase(generated_ist: str, ta: bool) -> str:
    """'காலை 9:55 நிலவரப்படி' / 'As of 9:55 AM'."""
    try:
        from datetime import datetime
        d = datetime.strptime(generated_ist.replace(" IST", ""), "%d %b %Y %H:%M")
    except Exception:
        return "தற்போதைய நிலவரப்படி" if ta else "Right now"
    if not ta:
        return "As of " + d.strftime("%I:%M %p").lstrip("0")
    h = d.hour
    part = "இரவு" if h < 5 else "அதிகாலை" if h < 7 else "காலை" if h < 12 else \
        "மதியம்" if h < 16 else "மாலை" if h < 19 else "இரவு"
    return f"{part} {d.strftime('%I:%M').lstrip('0')} நிலவரப்படி"


def _places_phrase(places, ta: bool, extra: int) -> str:
    """Where it is raining, ready to drop into a sentence.

    Tamil ends with its own case ending: 'A, B மற்றும் C, D அருகிலுள்ள
    பகுதிகளில்' or, with places left out, '... உட்பட 9 இடங்களில்'.
    English: 'A, B and areas near C and D' (+ 'and N more places')."""
    exact = [p.ta if ta else p.en for p in places if not p.near]
    near = [p.ta if ta else p.en for p in places if p.near]
    if ta:
        bits = list(exact)
        if near:
            bits.append(f"{', '.join(near)} அருகிலுள்ள பகுதிகள்")
        where = _join(bits, True)
        if extra > 0:
            return f"{where} உட்பட {len(places) + extra} இடங்களில்"
        if near:
            return where[: -len("பகுதிகள்")] + "பகுதிகளில்"
        return f"{where} பகுதிகளில்"
    bits = list(exact)
    if near:
        bits.append(f"areas near {_join(near, False)}")
    if extra > 0:
        bits.append(f"{extra} more place{'s' if extra > 1 else ''}")
    return _join(bits, False)


def _down_sentence(nc: Nowcast, ta: bool) -> str:
    if not nc.down_radars:
        return ""
    keys = [k for k in RADAR_NAMES if k in nc.down_radars] + \
           [k for k in nc.down_radars if k not in RADAR_NAMES]
    names = _join([RADAR_NAMES.get(k, (k.title(), k.title()))[1 if ta else 0] for k in keys], ta)
    if ta:
        return f"{names} ரேடார் புதுப்பிக்கப்படாததால் சில இடங்களின் மழை விடுபடலாம்."
    s = "s are" if len(keys) > 1 else " is"
    return f"The {names} radar{s} not updating from IMD, so some rain may be missed."


def build_caption(nc: Nowcast, limit: int = 1000) -> str:
    """Paragraph-style caption: a one-line title, then one flowing Tamil
    paragraph and one English paragraph (rain now, next 60 min, motion,
    radar status, disclaimer), then hashtags.

    Telegram photo captions max out at 1024 UTF-16 units; we stay under
    `limit` by dropping the least important places (folded in as
    "N more places"), never the title or the closing sentences."""
    region = f" – {nc.region_ta} | {nc.region_en}" if nc.region_en else ""
    head = f"🌧️ {config.BRAND_NAME} ரேடார் மழை அறிவிப்பு{region} • {_ist_short(nc.generated_ist)}"
    tags = f"{config.HASHTAGS} {nc.hashtags}".strip()

    motion = {True: "", False: ""}
    if nc.motion_ok and nc.speed_kmh and nc.bearing_deg is not None and nc.speed_kmh >= 5:
        k = int(((nc.bearing_deg % 360) + 22.5) // 45) % 8
        motion[True] = f"மழை மேகங்கள் {DIRS_TA[k]} நோக்கி மணிக்கு ~{nc.speed_kmh:.0f} கி.மீ வேகத்தில் நகர்கின்றன."
        dir_en = {"N": "north", "NE": "northeast", "E": "east", "SE": "southeast", "S": "south",
                  "SW": "southwest", "W": "west", "NW": "northwest"}[DIRS_EN[k]]
        motion[False] = f"Rain clouds are moving {dir_en} at ~{nc.speed_kmh:.0f} km/h."
    closing = {
        True: "இது சோதனை முன்னறிவிப்பு; IMD அறிவிப்புகளைப் பின்பற்றவும்.",
        False: "Experimental nowcast; follow official IMD warnings.",
    }

    def tail(ta: bool) -> list[str]:
        return [x for x in (motion[ta], _down_sentence(nc, ta), closing[ta]) if x]

    def assemble(paras: list[str]) -> str:
        return f"{head}\n\n" + "\n\n".join(paras) + (f"\n\n{tags}" if tags else "")

    if not nc.raining and not nc.coming:
        paras = []
        for ta in (True, False):
            if ta:
                lead = (f"{_time_phrase(nc.generated_ist, True)} ரேடாரில் குறிப்பிடத்தக்க மழை எதுவும் இல்லை."
                        if not nc.any_echo else
                        f"{_time_phrase(nc.generated_ist, True)} ரேடாரில் லேசான, ஆங்காங்கே மழை மேகங்கள் மட்டுமே உள்ளன.")
            else:
                lead = (f"{_time_phrase(nc.generated_ist, False)}, there is no significant rain on radar."
                        if not nc.any_echo else
                        f"{_time_phrase(nc.generated_ist, False)}, only light, scattered rain clouds show on radar.")
            paras.append(" ".join([lead] + tail(ta)))
        return assemble(paras)

    def now_sentence(places, extra: int, ta: bool) -> str:
        groups: dict = {}
        for p in places:
            groups.setdefault(tier(p.now_dbz)[0], []).append(p)
        order = sorted(groups, reverse=True)
        clauses = []
        for i, lvl in enumerate(order):
            more_n = extra if i == len(order) - 1 else 0
            where = _places_phrase(groups[lvl], ta, more_n)
            phrase = _TIER_SENT[lvl][0 if ta else 1]
            if ta:
                clauses.append(f"{where} {phrase} பெய்து வருகிறது" if i == 0 else f"{where} {phrase}")
            else:
                clauses.append(f"{phrase} is falling over {where}" if i == 0 else f"{phrase} over {where}")
        body = "; ".join(clauses)
        return f"தற்போது {body}." if ta else f"Right now, {body}."

    def soon_sentence(places, extra: int, ta: bool) -> str:
        items = []
        for p in places:
            t = tier(max(p.soon_dbz, config.ALERT_MIN_DBZ))
            when = f"~{p.eta_clock}" if p.eta_clock else (f"~{p.eta_min} நிமி" if ta else f"~{p.eta_min} min")
            name = p.ta if ta else p.en
            what = _TIER_SENT[t[0]][0] if ta else _TIER_SENT[t[0]][1].replace(" rain", "").replace(" with", ",")
            items.append(f"{name} ({when}, {what})")
        if ta:
            if extra > 0:
                where = f"{_join(items, True)} உட்பட {len(items) + extra} இடங்களுக்கு"
            else:
                where = f"{_join(items, True)} {'பகுதிக்கு' if len(items) == 1 else 'ஆகிய பகுதிகளுக்கு'}"
            return f"அடுத்த 60 நிமிடங்களில் {where} மழை வர வாய்ப்பு உள்ளது."
        if extra > 0:
            items.append(f"{extra} more place{'s' if extra > 1 else ''}")
        return f"In the next 60 minutes, rain is likely to reach {_join(items, False)}."

    picks = {0: [], 1: []}
    queues = {0: list(nc.raining), 1: list(nc.coming)}
    totals = {0: len(nc.raining), 1: len(nc.coming)}

    def render() -> str:
        paras = []
        for ta in (True, False):
            sents = []
            if picks[0]:
                sents.append(now_sentence(picks[0], totals[0] - len(picks[0]), ta))
            if picks[1]:
                s = soon_sentence(picks[1], totals[1] - len(picks[1]), ta)
                if not picks[0]:   # nothing raining now: open with the time instead
                    s = ("தற்போது இப்பகுதியில் மழை இல்லை. " if ta else "It is not raining here yet. ") + s
                sents.append(s)
            paras.append(" ".join(sents + tail(ta)))
        return assemble(paras)

    # The top town of each section is always named (a caption with no towns
    # says nothing); then add places one at a time, alternating sections,
    # most important first, until the caption would exceed the limit.
    for i in (0, 1):
        if queues[i]:
            picks[i].append(queues[i].pop(0))
    progressed = True
    while progressed:
        progressed = False
        for i in (0, 1):
            if queues[i]:
                picks[i].append(queues[i].pop(0))
                if _utf16_len(render()) > limit:
                    queues[i].insert(0, picks[i].pop())
                    queues[i] = []
                else:
                    progressed = True
    return render()


def top_places_for_card(nc: Nowcast, n: int = 6) -> list[tuple[PlaceStatus, str]]:
    """(place, 'now'|'soon') pairs to label on the image."""
    out = [(p, "now") for p in nc.raining[:n]]
    out += [(p, "soon") for p in nc.coming[: max(0, n - len(out))]]
    return out
