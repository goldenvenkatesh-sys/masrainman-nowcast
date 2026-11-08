"""One MasRainman cycle -- what GitHub Actions runs every 10 minutes.

  1. handle any Yes/No button presses since the last run
  2. run the radar engine (fetch IMD radars, track cells, export nowcast)
  3. for each region (Tamil Nadu & Puducherry, Kerala, Karnataka) with rain:
     zoom in on the busiest area, render the card + Tamil/English caption
  4. post to the Telegram channel and send the approval cards to you
  5. handle any button presses that arrived during this run
"""
import json
import traceback

from masrainman import config
from masrainman.social import process_pending_updates, publish_cycle


def _blank(work, g, center, view, aspect):
    """Blank the rain inside a card's window so the next card looks elsewhere."""
    import math
    half_la = view / 2
    half_lo = view * aspect / math.cos(math.radians(center[0])) / 2
    st = g["step_deg"]
    r0 = max(0, int((g["lat_north"] - (center[0] + half_la)) / st))
    r1 = max(0, int((g["lat_north"] - (center[0] - half_la)) / st) + 1)
    c0 = max(0, int((center[1] - half_lo - g["lon_west"]) / st))
    c1 = max(0, int((center[1] + half_lo - g["lon_west"]) / st) + 1)
    work[:, r0:r1, c0:c1] = 0


def _recentre(obs, g, center, view, aspect, towns, margin=0.1):
    """Put the rain itself in the middle of the card (not hugging an edge),
    while keeping every town in `towns` [(lat, lon)] on the card."""
    import math
    import numpy as np
    st = g["step_deg"]
    half_la = view / 2
    half_lo = view * aspect / math.cos(math.radians(center[0])) / 2
    # look a bit beyond the window so rain cut off at its edge pulls it over
    r0 = max(0, int((g["lat_north"] - (center[0] + 1.4 * half_la)) / st))
    r1 = max(0, int((g["lat_north"] - (center[0] - 1.4 * half_la)) / st) + 1)
    c0 = max(0, int((center[1] - 1.4 * half_lo - g["lon_west"]) / st))
    c1 = max(0, int((center[1] + 1.4 * half_lo - g["lon_west"]) / st) + 1)
    a = obs[r0:r1, c0:c1].astype(float)
    a[a == 255] = 0
    w = np.clip(a - 25, 0, 35) ** 1.5
    lat, lon = center
    if w.sum() > 0:
        rr, cc = np.indices(w.shape)
        lat = g["lat_north"] - (r0 + (rr * w).sum() / w.sum()) * st
        lon = g["lon_west"] + (c0 + (cc * w).sum() / w.sum()) * st
    # then shift only as far as needed to keep the named towns on the card
    for i, half in ((0, half_la), (1, half_lo)):
        vals = [t[i] for t in towns]
        if not vals:
            continue
        lo_, hi_ = max(vals) - half + margin, min(vals) + half - margin
        c = lat if i == 0 else lon
        c = (lo_ + hi_) / 2 if lo_ > hi_ else min(max(c, lo_), hi_)
        if i == 0:
            lat = c
        else:
            lon = c
    return lat, lon


def build_items(engine):
    from masrainman.alerts import BOT_JSON, build_caption, build_nowcast, for_region
    from masrainman.card import MAP_BOX, render_card
    from masrainman.regions import (ENABLED, HASHTAGS, MAX_CARDS_PER_REGION, REGION_RADARS, REGIONS,
                                    VIEW_LAT_DEG, best_window, fit_view, in_view)

    nc = build_nowcast()
    doc = json.loads(BOT_JSON.read_text()) if BOT_JSON.exists() else {}
    items = []
    if not nc or nc.grid is None:
        print("[cycle] no nowcast grid this cycle")
        return items, doc
    arr, g = nc.grid
    names = [L["name"] for L in g["layers"]]
    obs_i = names.index("observed")
    plus = [n for n in names if n.startswith("plus_")]
    proj_i = names.index(max(plus, key=lambda n: int(n[5:]))) if plus else None
    aspect = (MAP_BOX[2] - MAP_BOX[0]) / (MAP_BOX[3] - MAP_BOX[1])

    from dataclasses import replace as _replace

    def strength(t):
        return t[0].now_dbz if t[1] == "now" else t[0].soon_dbz

    for key, en, ta, bbox, states, zone in REGIONS:
        if key not in ENABLED:
            continue
        rnc = for_region(nc, states, en, ta, HASHTAGS.get(key, ""), zone, REGION_RADARS.get(key))
        if not (rnc.raining or rnc.coming):
            print(f"[cycle] {key}: no moderate+ rain at any town -- no card")
            continue
        # 8 Nov 2026: close-up cards. Instead of zooming one card far out until
        # every town fits, keep the view tight (VIEW_LAT_DEG, ~1.1 deg = ~120 km)
        # and give each separate rain area its own card (up to MAX_CARDS_PER_REGION).
        left = [(p, "now") for p in rnc.raining] + [(p, "soon") for p in rnc.coming]
        work = arr.copy()                    # rain already shown on a card is blanked here
        n = 0
        while left and n < MAX_CARDS_PER_REGION:
            towns = [(p.lat, p.lon, strength((p, k))) for p, k in left]
            center, score, peak = best_window(work, g, obs_i, proj_i, bbox, VIEW_LAT_DEG, aspect, towns)

            def inside(c, v):
                return [(p, k) for p, k in left if in_view(c, v, aspect, [(p.lat, p.lon)], margin=0.04)]

            if not inside(center, VIEW_LAT_DEG):
                # window sat on rain with no named town: centre on the strongest town left
                p0 = max(left, key=strength)[0]
                center = (p0.lat, p0.lon)
            here = inside(center, VIEW_LAT_DEG)
            # small zoom-out (max MAX_VIEW_LAT_DEG) only to keep a group's labels off the edge
            center, view = fit_view(center, VIEW_LAT_DEG, aspect, [(p.lat, p.lon) for p, _ in here])
            here = inside(center, view) or here
            if not here:
                break
            center = _recentre(work[obs_i], g, center, view, aspect, [(p.lat, p.lon) for p, _ in here])
            here = inside(center, view) or here
            left = [t for t in left if t not in here]
            _blank(work, g, center, view, aspect)
            n += 1
            ckey = key if n == 1 else f"{key}{n}"
            cnc = _replace(rnc, raining=[p for p, k in here if k == "now"],
                           coming=[p for p, k in here if k == "soon"])
            top = max(here, key=strength)[0]
            multi = n > 1 or bool(left)
            t_en = f"{en} - {top.en}" if multi else en
            t_ta = f"{ta} - {top.ta}" if multi else ta
            path = config.OUTPUT_DIR / f"masrainman_{ckey}.jpg"
            try:
                render_card(cnc, engine, center, view, t_en, t_ta, path)
            except Exception:
                traceback.print_exc()
                continue
            caption = build_caption(cnc, limit=940)        # room left for the admin prompt line
            (config.OUTPUT_DIR / f"caption_{ckey}.txt").write_text(caption, encoding="utf-8")
            print(f"[cycle] {ckey}: card -> {path} (view centre {center[0]:.2f},{center[1]:.2f}, "
                  f"{view:.2f} deg tall, {len(here)} town(s), peak {peak:.0f} dBZ)")
            print(caption)
            items.append((ckey, path, caption))
            doc["peak_dbz"] = max(float(doc.get("peak_dbz") or 0), float(peak or 0))   # for WhatsApp peak-mode
        if left:
            print(f"[cycle] {key}: card limit reached, left out: {', '.join(p.en for p, _ in left)}")
    return items, doc


def main():
    if not config.APPROVALS_VIA_LISTENER:
        n = process_pending_updates()
        print(f"[cycle] handled {n} Telegram update(s) before the radar run")

    import nowcast_bot as engine
    try:
        engine.run_pipeline()
    except Exception:
        traceback.print_exc()
        print("[cycle] radar engine failed this cycle -- nothing new to post")
        return

    items, doc = build_items(engine)
    publish_cycle(items, doc)

    if not config.APPROVALS_VIA_LISTENER:
        process_pending_updates()


if __name__ == "__main__":
    main()
