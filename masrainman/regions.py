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

REGIONS = [
    # key, English, Tamil, (south, west, north, east) where the view may go, states named in text
    ("tn", "Tamil Nadu & Puducherry", "தமிழ்நாடு, புதுச்சேரி", (7.9, 76.2, 14.2, 80.9), {"TN", "PY"}),
    ("kl", "Kerala", "கேரளா", (8.0, 74.6, 12.9, 77.5), {"KL"}),
    ("ka", "Karnataka", "கர்நாடகா", (11.4, 73.6, 15.4, 77.9), {"KA"}),
]
HASHTAGS = {"tn": "#TamilNaduRains #ChennaiWeather #PuducherryRains",
            "kl": "#KeralaRains #KeralaWeather",
            "ka": "#KarnatakaRains #MangaluruRains"}
ENABLED = [r.strip() for r in os.environ.get("REGIONS", "tn,kl,ka").split(",") if r.strip()]

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
