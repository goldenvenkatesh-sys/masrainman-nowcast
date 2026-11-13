"""Satellite cross-check for radar echoes (added 13 Nov 2026).

Why: at 05:32 IST on 13 Nov the Karaikal radar showed "very heavy /
thunderstorm" rain over Karaikudi and Pudukkottai. The satellite picture at
the same time showed clear sky there. The echo was false, most likely
anomalous propagation (the beam bending down to the ground under a dawn
temperature inversion). A LIVE card went out for it anyway.

What it does: once per cycle it downloads two EUMETSAT Meteosat (Indian
Ocean, 45.5E) images covering all of south India:
  * msg_iodc:clm   -- cloud mask: cloud / clear land / clear sea
  * msg_iodc:ir108 -- 10.8 um infrared, rendered grey (brighter = colder top)
Two rules, applied before cells, cards, captions and the Rain Alarm data
are built:
  1. CLEAR SKY -> echo removed. A spot is clear only when BOTH agree: the
     cloud mask says clear AND the infrared is warm (dark grey), and no
     cloud lies within SAT_BUFFER_KM.
  2. NO COLD CLOUD -> moderate+ echo capped to light. Echo of SAT_STRONG_DBZ
     (30) or more needs a cloud top colder than about -20 C (IR grey >=
     SAT_RAIN_GREY) or H63 satellite rain within SAT_BUFFER_KM (raised from
     ~0 C at 06:05 IST 13 Nov: cirrus reached grey 80-118 over false echo
     at Chinna Salem / Attur / Yercaud). Without one it is capped just
     below the moderate threshold, so no heavy/thunderstorm card or LIVE
     card can come from it, while a real light shower would still show.
     (On 13 Nov thin high cirrus covered much of the false echo, so rule 1
     alone left it standing; about 85% of its 35+ dBZ echo had no top
     colder than ~0 C nearby, while the real Thanjavur cell did.)
  3. NO DEEP CLOUD AND NO SATELLITE RAIN -> heavy echo capped to light.
     Echo of SAT_HEAVY_DBZ (40) or more needs EITHER a deep, cold cloud top
     (IR grey >= SAT_DEEP_GREY, ~ -50 C; raised from ~ -30 C because thin
     cirrus over Salem reached that) OR rain in EUMETSAT's H SAF H63
     satellite rain-rate product (msg_iodc:h63) within SAT_BUFFER_KM.
  4. WHOLE PATCH. An echo patch (connected moderate+ echo) is capped to
     light as a whole when most of its heavy core fails rule 3, or when
     hardly any of it (< SAT_PATCH_MIN_BACKED) has deep cloud or satellite
     rain within SAT_BUFFER_KM -- a patch living entirely under thin cirrus
     is false echo, not rain (it kept "moderate rain coming to Kallakurichi
     / Perambalur" lines alive after rules 1-3).
     (Added 06:05 IST 13 Nov: thin high cirrus over Ariyalur, Perambalur,
     Salem and Kallakurichi reached grey 80-118, which passed rule 2, and
     the Karaikal radar's false echo there gave 57-59 dBZ "very heavy /
     thunderstorm" cards. H63 showed no rain at any of those towns; the
     real Thanjavur storm had grey 180 and H63 rain.)
  5. SECOND RADAR. Echo that another, independent radar also sees
     (moderate+ within SAT_CONFIRM_KM, 8 km) is kept whatever the
     satellite says -- shallow warm-rain monsoon showers have no cold top,
     but they show on every radar looking there; false echo doesn't.
Rain with real (deep) cloud, satellite rain or a second radar is never touched.

IR grey scale: EUMETSAT serves the 10.8 um channel only as an 8-bit grey
picture. From clear land at dawn (~297 K -> grey ~24) and the coldest tops
in view (grey ~215) it is roughly 0.5 K per grey level, so grey 60 ~ 280 K
and grey 75 ~ 272 K (about 0 C). Treat these as estimates.

It fails open: if the satellite cannot be fetched, is too old or looks
broken, nothing is removed and the cycle runs exactly as before.

Settings (environment variables):
  SAT_CHECK=false        turn the check off
  SAT_BUFFER_KM=15       echo within this distance of cloud is kept
  SAT_MAX_AGE_MIN=75     satellite older than this (vs now) is not used
  SAT_WARM_GREY=60       IR grey below this counts as warm (rule 1)
  SAT_RAIN_GREY=110      IR grey at/above this = cloud top colder than ~ -20 C (rule 2)
  SAT_STRONG_DBZ=30      rule 2 applies to echo at/above this
  SAT_DEEP_GREY=170      IR grey at/above this = thunderstorm top, ~ -50 C (rules 3-4)
  SAT_HEAVY_DBZ=40       rules 3-4 apply to echo at/above this
  SAT_PATCH_BAD=0.6      rule 4: patch capped when this share of its heavy core fails
  SAT_PATCH_MIN_BACKED=0.1  rule 4: patch capped when less than this share has deep cloud / sat rain
"""
from __future__ import annotations

import io
import os
import re
from datetime import datetime, timezone

import numpy as np
import requests
from PIL import Image
from scipy import ndimage

WMS = "https://view.eumetsat.int/geoserver/ows"
BBOX = (72.0, 5.0, 87.0, 21.5)          # west, south, east, north -- every radar's range circle
STEP = 0.02                             # deg per satellite pixel (Meteosat IR is ~4-5 km here)
ENABLED = os.environ.get("SAT_CHECK", "true").lower() != "false"
BUFFER_KM = float(os.environ.get("SAT_BUFFER_KM", "15"))
MAX_AGE_MIN = float(os.environ.get("SAT_MAX_AGE_MIN", "75"))
WARM_GREY = int(os.environ.get("SAT_WARM_GREY", "60"))
RAIN_GREY = int(os.environ.get("SAT_RAIN_GREY", "110"))
STRONG_DBZ = float(os.environ.get("SAT_STRONG_DBZ", "30"))
DEEP_GREY = int(os.environ.get("SAT_DEEP_GREY", "170"))
HEAVY_DBZ = float(os.environ.get("SAT_HEAVY_DBZ", "40"))
PATCH_BAD = float(os.environ.get("SAT_PATCH_BAD", "0.6"))
PATCH_MIN_HEAVY_PX = 10
PATCH_MIN_BACKED = float(os.environ.get("SAT_PATCH_MIN_BACKED", "0.1"))
PATCH_MIN_PX = 20
CONFIRM_KM = float(os.environ.get("SAT_CONFIRM_KM", "8"))

CLEAR_COLOURS = ((0, 192, 0), (0, 0, 255))   # clear land, clear sea in EUMETSAT's CLM style
CLOUD_COLOUR = (255, 255, 255)

_cache: dict = {}
REPORT: dict = {}                      # this cycle's summary, read by the social layer / logs


def _get_map(layer: str, time_iso: str | None):
    w = int(round((BBOX[2] - BBOX[0]) / STEP))
    h = int(round((BBOX[3] - BBOX[1]) / STEP))
    params = {"service": "WMS", "version": "1.1.1", "request": "GetMap", "layers": layer,
              "styles": "", "srs": "EPSG:4326", "bbox": ",".join(str(v) for v in BBOX),
              "width": w, "height": h, "format": "image/png"}
    if time_iso:
        params["time"] = time_iso
    r = requests.get(WMS, params=params, timeout=25)
    r.raise_for_status()
    if not r.headers.get("content-type", "").startswith("image/"):
        raise ValueError(f"{layer}: not an image ({r.headers.get('content-type')})")
    m = re.search(r"time=(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)", r.headers.get("warning", ""))
    t = m.group(1) + "Z" if m else time_iso
    return np.array(Image.open(io.BytesIO(r.content)).convert("RGB")), t


def clear_sky_mask(now_utc: datetime | None = None):
    """(masks, info). masks["clear_far"]: clear sky with no cloud within
    BUFFER_KM (rule 1). masks["no_cold"]: no cloud top colder than ~0 C
    within BUFFER_KM (rule 2). Returns (None, info) when the satellite
    can't be used."""
    if "result" in _cache:
        return _cache["result"]
    now_utc = now_utc or datetime.now(timezone.utc)
    info = {"used": False}
    result = (None, info)
    try:
        ir, t = _get_map("msg_iodc:ir108", None)
        if not t:
            raise ValueError("satellite time unknown")
        clm, _ = _get_map("msg_iodc:clm", t)
        sat_time = datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        age = (now_utc - sat_time).total_seconds() / 60.0
        info.update(time_utc=t, age_min=round(age))
        if age > MAX_AGE_MIN:
            raise ValueError(f"satellite is {age:.0f} min old")
        if clm.shape != ir.shape:
            raise ValueError("cloud mask and infrared sizes differ")
        is_clear = np.zeros(clm.shape[:2], bool)
        for c in CLEAR_COLOURS:
            is_clear |= np.all(clm == c, axis=2)
        known = is_clear | np.all(clm == CLOUD_COLOUR, axis=2)
        if known.mean() < 0.8:
            raise ValueError(f"cloud mask looks wrong ({known.mean():.0%} recognised pixels)")
        grey = ir.mean(axis=2)
        if grey.std() < 2:
            raise ValueError("infrared image is blank")
        clear = is_clear & (grey < WARM_GREY)
        r_px = max(1, int(round(BUFFER_KM / (STEP * 111.0))))
        yy, xx = np.ogrid[-r_px:r_px + 1, -r_px:r_px + 1]
        disc = (xx * xx + yy * yy) <= r_px * r_px
        cloud_near = ndimage.binary_dilation(~clear, structure=disc)
        cold = grey >= RAIN_GREY
        support = grey >= DEEP_GREY
        h63_note = "H63 rain-rate unavailable"
        try:   # H SAF satellite rain rate; optional -- deep cloud alone still counts
            h63, h63_t = _get_map("msg_iodc:h63", None)
            if h63.shape == ir.shape:
                rain = ~np.all(h63 == 255, axis=2) & ~np.all(h63 == 0, axis=2)
                support |= rain
                cold |= rain
                h63_note = f"H63 {h63_t}: {rain.mean():.1%} raining"
        except Exception as e:
            h63_note = f"H63 rain-rate unavailable ({e})"
        cold_near = ndimage.binary_dilation(cold, structure=disc)
        deep_near = ndimage.binary_dilation(support, structure=disc)
        info.update(used=True, clear_fraction=round(float(clear.mean()), 3))
        result = ({"clear_far": ~cloud_near, "no_cold": ~cold_near, "no_deep": ~deep_near}, info)
        print(f"[satellite] Meteosat IODC {t} ({age:.0f} min old): {clear.mean():.0%} of south India clear sky; {h63_note}")
    except Exception as e:
        info["reason"] = str(e)
        print(f"[satellite] check skipped this cycle -- {e}")
    _cache["result"] = result
    return result


def _sat_index(dbz, product, pixel_to_latlon, plot_bbox, shape):
    """Echo pixels of `dbz` and the satellite-grid cell each falls in."""
    echo = ~np.isnan(dbz)
    ys, xs = np.nonzero(echo)
    lat, lon = pixel_to_latlon(xs + plot_bbox[0], ys + plot_bbox[1], product)
    r = np.floor((BBOX[3] - lat) / STEP).astype(int)
    c = np.floor((lon - BBOX[0]) / STEP).astype(int)
    inb = (r >= 0) & (r < shape[0]) & (c >= 0) & (c < shape[1])
    return echo, ys, xs, r, c, inb


def screen_all(dbz_by_product: dict, pixel_to_latlon, plot_bbox_of, radar_of) -> dict:
    """Screen every radar product at once. Besides the satellite rules,
    echo that a SECOND, independent radar also sees (moderate+ within
    CONFIRM_KM) counts as confirmed: false echo such as anomalous
    propagation is specific to one radar's beam, while real rain shows on
    every radar that looks there (e.g. shallow warm-rain monsoon showers off
    Chennai with no cold cloud top, seen by both NIOT and the IMD Chennai
    radar)."""
    if not ENABLED:
        return dbz_by_product
    masks, info = clear_sky_mask()
    if masks is None:
        return dbz_by_product
    shape = masks["clear_far"].shape
    idx, per_radar = {}, {}
    for p, d in dbz_by_product.items():
        if d is None:
            continue
        idx[p] = _sat_index(d, p, pixel_to_latlon, plot_bbox_of(p), shape)
        echo, ys, xs, r, c, inb = idx[p]
        strong = inb & (d[ys, xs] >= STRONG_DBZ)
        m = per_radar.setdefault(radar_of(p), np.zeros(shape, bool))
        m[r[strong], c[strong]] = True
    rc = max(1, int(round(CONFIRM_KM / (STEP * 111.0))))
    yy, xx = np.ogrid[-rc:rc + 1, -rc:rc + 1]
    disc = (xx * xx + yy * yy) <= rc * rc
    near = {k: ndimage.binary_dilation(m, structure=disc) for k, m in per_radar.items()}
    count = sum(v.astype(np.int8) for v in near.values()) if near else None
    out = {}
    for p, d in dbz_by_product.items():
        if d is None or p not in idx:
            out[p] = d
            continue
        confirm = (count - near[radar_of(p)].astype(np.int8)) >= 1 if count is not None else None
        out[p] = screen(d, p, pixel_to_latlon, plot_bbox_of(p), confirm=confirm, _idx=idx[p])
    return out


def screen(dbz: np.ndarray, product: str, pixel_to_latlon, plot_bbox, confirm=None, _idx=None) -> np.ndarray:
    """Apply rules 1-4 to `dbz` (NaN = no echo). pixel_to_latlon(px, py,
    product) is the engine's own georeferencing; plot_bbox gives the decoded
    array's pixel offset inside the frame. `confirm` (satellite grid, bool):
    where another radar also sees moderate+ echo -- such echo is kept."""
    if not ENABLED or dbz is None:
        return dbz
    if not (~np.isnan(dbz)).any():
        print(f"[satellite] {product}: no echo -- nothing to check")
        return dbz
    masks, info = clear_sky_mask()
    if masks is None:
        return dbz
    echo, ys, xs, r, c, inb = _idx or _sat_index(dbz, product, pixel_to_latlon, plot_bbox, masks["clear_far"].shape)
    vals = dbz[ys, xs]
    ok2 = np.zeros(ys.shape, bool)              # confirmed by another radar
    if confirm is not None:
        ok2[inb] = confirm[r[inb], c[inb]]
    drop = np.zeros(ys.shape, bool)
    cap = np.zeros(ys.shape, bool)
    drop[inb] = masks["clear_far"][r[inb], c[inb]]
    drop &= ~ok2
    cap[inb] = masks["no_cold"][r[inb], c[inb]] & (vals[inb] >= STRONG_DBZ)
    no_deep = np.zeros(ys.shape, bool)          # echo the satellite can't see is never judged
    no_deep[inb] = masks["no_deep"][r[inb], c[inb]]
    no_deep &= ~ok2
    heavy_bad = no_deep & (vals >= HEAVY_DBZ)
    cap |= heavy_bad
    # rule 4: echo patches (connected moderate+ echo)
    strong_img = np.zeros(dbz.shape, bool)
    strong_img[ys[vals >= STRONG_DBZ], xs[vals >= STRONG_DBZ]] = True
    strong_img[ys[drop], xs[drop]] = False
    lab, n = ndimage.label(ndimage.binary_closing(strong_img, iterations=2) & echo, structure=np.ones((3, 3)))
    patches = 0
    if n:
        lab_px = lab[ys, xs]
        heavy = vals >= HEAVY_DBZ
        n_heavy = np.bincount(lab_px[heavy], minlength=n + 1)
        n_bad = np.bincount(lab_px[heavy_bad], minlength=n + 1)
        bad_lab = (n_heavy >= PATCH_MIN_HEAVY_PX) & (n_bad >= PATCH_BAD * np.maximum(n_heavy, 1))
        mod = (vals >= STRONG_DBZ) & inb
        n_mod = np.bincount(lab_px[mod], minlength=n + 1)
        n_backed = np.bincount(lab_px[mod & ~no_deep], minlength=n + 1)
        bad_lab |= (n_mod >= PATCH_MIN_PX) & (n_backed < PATCH_MIN_BACKED * n_mod)
        bad_lab[0] = False
        patch_px = bad_lab[lab_px] & (vals >= STRONG_DBZ)
        patches = int(bad_lab.sum())
        cap |= patch_px
    cap &= ~drop & ~ok2
    if not (drop.any() or cap.any()):
        print(f"[satellite] {product}: all {echo.sum()} echo px backed by satellite cloud / another radar -- kept")
        return dbz
    out = dbz.copy()
    out[ys[drop], xs[drop]] = np.nan
    out[ys[cap], xs[cap]] = STRONG_DBZ - 1
    n_strong = int((vals >= STRONG_DBZ).sum())
    gone = int((drop & (vals >= STRONG_DBZ)).sum() + cap.sum())
    REPORT[product] = {"removed_px": int(drop.sum()), "capped_px": int(cap.sum()),
                       "strong_px": n_strong, "strong_gone_px": gone,
                       "strong_gone_share": round(gone / n_strong, 3) if n_strong else 0.0,
                       "sat_time_utc": info.get("time_utc")}
    print(f"[satellite] {product}: {drop.sum()} echo px removed (clear sky), {cap.sum()} px capped to light "
          f"(no deep cloud, satellite rain or second radar; {patches} whole patch(es)) -- {gone} of {n_strong} "
          f"moderate+ px not backed; {int((ok2 & (vals >= STRONG_DBZ)).sum())} confirmed by another radar")
    return out
