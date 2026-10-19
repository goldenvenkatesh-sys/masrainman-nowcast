"""Regional cards and the auto-zoom that 'captures the screen close'.

Each cycle, every region that has rain worth talking about gets its own
card. Inside the region we slide a fixed-size window over the radar grid
and pick the spot with the most (and strongest) rain, now plus the
projected +90 min picture, so the card zooms in on where the action is.
"""
from __future__ import annotations

import math
import os

import numpy as np

# ---- Tamil Nadu & Puducherry split into the cards people asked for ----------
# One function decides each town's card, so every TN/PY town lands on exactly
# one card. Lines follow district groupings by town position, not exact
# district borders.
def tn_zone(lat: float, lon: float) -> str:
    if lat >= 12.45 and lon >= 79.65:
        return "chn"                       # Chennai, Tiruvallur, Chengalpattu, Kanchipuram
    if lat >= 11.95 and lon >= 78.45:
        return "tnni"                      # Tindivanam / Gingee belt
    if lat >= 11.55 and 78.45 <= lon < 79.6:
        return "tnni"                      # Vellore, Ranipet, Tirupattur, Tiruvannamalai, Villupuram, Kallakurichi
    if lon >= 79.05 and lat >= 10.25:
        return "delta"                     # Thanjavur, Tiruvarur, Nagapattinam, Mayiladuthurai, Cuddalore, Puducherry, Karaikal
    if (lon < 78.3 and lat >= 10.9) or (lon < 77.45 and lat >= 10.2):
        return "tnw"                       # Salem, Namakkal, Erode, Coimbatore, Tiruppur, Nilgiris, Dharmapuri, Krishnagiri, Karur
    if lat >= 10.25:
        return "tnc"                       # Trichy, Perambalur, Ariyalur, Pudukkottai, Dindigul
    return "tns"                           # Madurai, Theni, Virudhunagar, Sivaganga, Ramanathapuram, Tirunelveli, Tenkasi, Thoothukudi, Kanyakumari


def _tn(key):
    return lambda lat, lon: tn_zone(lat, lon) == key


REGIONS = [
    # key, English, Tamil, (south, west, north, east) where the view may go,
    # states named in text, which of those states' towns it names (None = all)
    ("chn", "Chennai & Suburbs", "சென்னை & புறநகர்", (12.3, 79.4, 13.7, 80.6), {"TN"}, _tn("chn")),
    ("tnni", "North Interior Tamil Nadu", "வட உள் தமிழ்நாடு", (11.4, 78.3, 13.2, 79.9), {"TN"}, _tn("tnni")),
    ("delta", "Delta & Puducherry", "டெல்டா & புதுச்சேரி", (10.2, 78.9, 12.0, 80.2), {"TN", "PY"}, _tn("delta")),
    ("tnc", "Central Tamil Nadu", "மத்திய தமிழ்நாடு", (10.1, 77.4, 11.4, 79.2), {"TN"}, _tn("tnc")),
    ("tnw", "West Tamil Nadu", "மேற்கு தமிழ்நாடு", (10.1, 76.3, 12.9, 78.6), {"TN"}, _tn("tnw")),
    ("tns", "South Tamil Nadu", "தென் தமிழ்நாடு", (7.9, 76.9, 10.4, 79.5), {"TN"}, _tn("tns")),
    ("kls", "South Kerala", "தென் கேரளா", (8.0, 75.9, 10.3, 77.5), {"KL"}, lambda la, lo: la < 10.25),
    ("kln", "North Kerala", "வட கேரளா", (10.2, 74.8, 12.8, 77.0), {"KL"}, lambda la, lo: la >= 10.25),
    ("kac", "Coastal Karnataka", "கடலோர கர்நாடகா", (12.3, 73.8, 15.0, 75.6), {"KA"}, lambda la, lo: lo < 75.45),
    ("blr", "Bengaluru", "பெங்களூரு", (12.4, 76.9, 13.7, 78.3), {"KA"}, lambda la, lo: lo >= 76.95),
    ("ap", "South Andhra Pradesh", "தென் ஆந்திரா", (13.15, 77.8, 15.6, 80.4), {"AP"}, None),
]
HASHTAGS = {"chn": "#ChennaiRains #ChennaiWeather",
            "tnni": "#TamilNaduRains #VelloreRains #TiruvannamalaiRains #VillupuramRains",
            "delta": "#DeltaRains #ThanjavurRains #PuducherryRains #TamilNaduRains",
            "tnc": "#TamilNaduRains #TrichyRains #DindigulRains",
            "tnw": "#TamilNaduRains #KonguRains #CoimbatoreRains #SalemRains",
            "tns": "#TamilNaduRains #MaduraiRains #SouthTamilNadu",
            "kls": "#KeralaRains #TrivandrumRains #KochiRains",
            "kln": "#KeralaRains #KozhikodeRains #KannurRains",
            "kac": "#KarnatakaRains #MangaluruRains #UdupiRains",
            "blr": "#BengaluruRains #BangaloreWeather",
            "ap": "#AndhraPradeshRains #TirupatiRains #NelloreRains"}
ALL_KEYS = [r[0] for r in REGIONS]
# Older setups list "tn,kl,ka" (the workflow's REGIONS variable): map those to the new cards.
_OLD = {"tn": ["chn", "tnni", "delta", "tnc", "tnw", "tns", "ap"], "tncw": ["tnc", "tnw"],
        "kl": ["kls", "kln"], "ka": ["kac", "blr"]}
ENABLED = []
for _r in [r.strip() for r in os.environ.get("REGIONS", ",".join(ALL_KEYS)).split(",") if r.strip()]:
    for _k in _OLD.get(_r, [_r]):
        if _k in ALL_KEYS and _k not in ENABLED:
            ENABLED.append(_k)

# Widest the view may zoom out so every town named in a caption is on the card.
MAX_VIEW_LAT_DEG = float(os.environ.get("MAX_VIEW_LAT_DEG", "3.0"))


def fit_view(center, view_lat, aspect, towns):
    """Zoom out (up to MAX_VIEW_LAT_DEG) and re-centre so every town in
    `towns` [(lat, lon, ...)] fits on the card with a small margin.
    Returns (center, view_lat)."""
    if not towns:
        return center, view_lat
    la = [t[0] for t in towns]; lo = [t[1] for t in towns]
    mid = ((min(la) + max(la)) / 2, (min(lo) + max(lo)) / 2)
    cosm = math.cos(math.radians(mid[0]))
    need = max(max(la) - min(la) + 0.45, (max(lo) - min(lo) + 0.45) * cosm / aspect)
    if need <= view_lat and in_view(center, view_lat, aspect, towns, margin=0.12):
        return center, view_lat
    v = min(max(view_lat, need), MAX_VIEW_LAT_DEG)
    return mid, v


def in_view(center, view_lat, aspect, towns, margin=0.0):
    half_la = view_lat / 2 - margin
    half_lo = view_lat * aspect / math.cos(math.radians(center[0])) / 2 - margin
    return all(abs(t[0] - center[0]) <= half_la and abs(t[1] - center[1]) <= half_lo for t in towns)


# Height of the zoomed view in degrees of latitude (1.6 deg ~ 180 km,
# about the reference "Chennai and surroundings" framing).
VIEW_LAT_DEG = float(os.environ.get("VIEW_LAT_DEG", "1.6"))


def describe(peak: float) -> tuple[str, str]:
    if peak >= 50:
        return "intense thunderstorms", "இடியுடன் கூடிய தீவிர மழை"
    if peak >= 40:
        return "heavy rain cells", "கனமழை மேகங்கள்"
    if peak >= 30:
        return "rainbands", "மழைப் பட்டைகள்"
    return "light showers", "லேசான மழை"


def best_window(arr: np.ndarray, g: dict, obs_i: int, proj_i: int | None,
                bbox: tuple, view_lat: float, aspect: float,
                places=()) -> tuple[tuple[float, float], float, float]:
    """Return ((lat, lon) centre, score, peak dBZ) of the busiest window
    inside bbox. aspect = width/height of the map panel. `places` =
    [(lat, lon, dbz)] of towns in the nowcast -- they weigh heavily, so the
    view always frames the places the caption talks about."""
    step = g["step_deg"]
    south, west, north, east = bbox
    r0 = max(0, int((g["lat_north"] - north) / step)); r1 = min(g["nrows"], int((g["lat_north"] - south) / step) + 1)
    c0 = max(0, int((west - g["lon_west"]) / step)); c1 = min(g["ncols"], int((east - g["lon_west"]) / step) + 1)
    if r1 - r0 < 3 or c1 - c0 < 3:
        return ((south + north) / 2, (west + east) / 2), 0.0, 0.0

    def weight(layer):
        a = layer[r0:r1, c0:c1].astype(float)
        a[a == 255] = 0
        return np.clip(a - 25, 0, 35) ** 1.5, a

    w, obs = weight(arr[obs_i])
    if proj_i is not None:
        wp, _ = weight(arr[proj_i])
        w = w + 0.5 * wp
    for lat, lon, dbz in places:
        r, c = int((g["lat_north"] - lat) / step) - r0, int((lon - g["lon_west"]) / step) - c0
        if 0 <= r < w.shape[0] and 0 <= c < w.shape[1]:
            w[r, c] += 60 * max(dbz - 25, 5) ** 1.5
    lat_mid = (south + north) / 2
    wr = max(1, int(view_lat / step))
    wc = max(1, int(view_lat * aspect / math.cos(math.radians(lat_mid)) / step))
    wr, wc = min(wr, w.shape[0]), min(wc, w.shape[1])
    cs = np.pad(w.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    tot = cs[wr:, wc:] - cs[:-wr, wc:] - cs[wr:, :-wc] + cs[:-wr, :-wc]
    i, j = np.unravel_index(np.argmax(tot), tot.shape)
    score = float(tot[i, j])
    peak = float(obs[i:i + wr, j:j + wc].max())
    i, j = int(i), int(j)
    # re-centre on the weighted middle of what that window holds, so a lone
    # rain area sits in the middle of the card instead of on its edge
    sub = w[i:i + wr, j:j + wc]
    if sub.sum() > 0:
        rr, cc = np.indices(sub.shape)
        ci = i + float((rr * sub).sum() / sub.sum()) - wr / 2
        cj = j + float((cc * sub).sum() / sub.sum()) - wc / 2
        i, j = min(max(ci, 0), w.shape[0] - wr), min(max(cj, 0), w.shape[1] - wc)
    lat = g["lat_north"] - (r0 + i + wr / 2) * step
    lon = g["lon_west"] + (c0 + j + wc / 2) * step
    return (lat, lon), score, peak
