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


def _keep_towns(center, view, aspect, towns, margin=0.1):
    """Shift `center` only as far as needed so every (lat, lon) in towns is on the card."""
    import math
    half = (view / 2, view * aspect / math.cos(math.radians(center[0])) / 2)
    c = list(center)
    for i in (0, 1):
        vals = [t[i] for t in towns]
        if not vals:
            continue
        lo_, hi_ = max(vals) - half[i] + margin, min(vals) + half[i] - margin
        c[i] = (lo_ + hi_) / 2 if lo_ > hi_ else min(max(c[i], lo_), hi_)
    return tuple(c)


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
                                    VIEW_LAT_DEG, WIDE_MAX_VIEW_LAT_DEG, WIDE_VIEW_LAT_DEG, best_window,
                                    fit_view, in_view)
    import os
    TELEGRAM_OVERVIEW = os.environ.get("TELEGRAM_OVERVIEW", "true").lower() != "false"
    OVERVIEW_MIN_LAT_DEG = float(os.environ.get("OVERVIEW_MIN_LAT_DEG", "2.6"))
    OVERVIEW_MAX_LAT_DEG = float(os.environ.get("OVERVIEW_MAX_LAT_DEG", "4.0"))

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
        # --- 1) the earlier wide card (1.6 deg, as before 8 Nov). Goes to the
        #        approval card (-> Facebook / WhatsApp) and the WhatsApp hourly post.
        towns = [(p.lat, p.lon, p.now_dbz) for p in rnc.raining] + [(p.lat, p.lon, p.soon_dbz) for p in rnc.coming]
        wc, _, wpeak = best_window(arr, g, obs_i, proj_i, bbox, WIDE_VIEW_LAT_DEG, aspect, towns)
        wc, wview = fit_view(wc, WIDE_VIEW_LAT_DEG, aspect, towns, max_view=WIDE_MAX_VIEW_LAT_DEG)
        wseen = lambda p: in_view(wc, wview, aspect, [(p.lat, p.lon)])
        wnc = _replace(rnc, raining=[p for p in rnc.raining if wseen(p)], coming=[p for p in rnc.coming if wseen(p)])
        if wnc.raining or wnc.coming:
            path = config.OUTPUT_DIR / f"masrainman_{key}.jpg"
            try:
                render_card(wnc, engine, wc, wview, en, ta, path)
                caption = build_caption(wnc, limit=940)
                (config.OUTPUT_DIR / f"caption_{key}.txt").write_text(caption, encoding="utf-8")
                print(f"[cycle] {key}: WIDE card (FB/WhatsApp) -> {path} ({wview:.2f} deg tall, peak {wpeak:.0f} dBZ)")
                print(caption)
                items.append((key, path, caption, "external"))
                doc["peak_dbz"] = max(float(doc.get("peak_dbz") or 0), float(wpeak or 0))
            except Exception:
                traceback.print_exc()

        # --- 1b) LONG-RANGE overview for Telegram (8 Nov 2026): the whole region
        #         on one map (~2.6-4 deg tall), clearly wider than the close-ups,
        #         so Telegram shows the big picture first, then the close-ups.
        if TELEGRAM_OVERVIEW:
            south, west, north, east = bbox
            import math as _m
            oc = ((south + north) / 2, (west + east) / 2)
            oview = max(north - south + 0.5, (east - west + 0.5) * _m.cos(_m.radians(oc[0])) / aspect)
            oview = min(max(oview, OVERVIEW_MIN_LAT_DEG), OVERVIEW_MAX_LAT_DEG)
            oc = _keep_towns(oc, oview, aspect, [(t[0], t[1]) for t in towns])
            oseen = lambda p: in_view(oc, oview, aspect, [(p.lat, p.lon)])
            onc = _replace(rnc, raining=[p for p in rnc.raining if oseen(p)], coming=[p for p in rnc.coming if oseen(p)])
            path = config.OUTPUT_DIR / f"masrainman_{key}_wide.jpg"
            try:
                render_card(onc, engine, oc, oview, f"{en} (wide view)", f"{ta} (பரந்த பார்வை)", path)
                caption = build_caption(onc, limit=940)
                (config.OUTPUT_DIR / f"caption_{key}_wide.txt").write_text(caption, encoding="utf-8")
                print(f"[cycle] {key}_wide: LONG-RANGE card (Telegram) -> {path} ({oview:.2f} deg tall)")
                items.append((f"{key}_wide", path, caption, "telegram"))
            except Exception:
                traceback.print_exc()

        # --- 2) close-up cards (Telegram only): tight view (VIEW_LAT_DEG,
        #        ~1.1 deg = ~120 km), one card per separate rain area,
        #        up to MAX_CARDS_PER_REGION.
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
            ckey = f"{key}_z{n}"
            cnc = _replace(rnc, raining=[p for p, k in here if k == "now"],
                           coming=[p for p, k in here if k == "soon"])
            top = max(here, key=strength)[0]
            t_en, t_ta = f"{en} - {top.en} (close-up)", f"{ta} - {top.ta} (அருகில்)"   # named after its main town
            path = config.OUTPUT_DIR / f"masrainman_{ckey}.jpg"
            try:
                render_card(cnc, engine, center, view, t_en, t_ta, path)
            except Exception:
                traceback.print_exc()
                continue
            caption = build_caption(cnc, limit=940)        # room left for the admin prompt line
            (config.OUTPUT_DIR / f"caption_{ckey}.txt").write_text(caption, encoding="utf-8")
            print(f"[cycle] {ckey}: CLOSE-UP card -> {path} (view centre {center[0]:.2f},{center[1]:.2f}, "
                  f"{view:.2f} deg tall, {len(here)} town(s), peak {peak:.0f} dBZ)")
            print(caption)
            items.append((ckey, path, caption, "telegram"))
        if left:
            print(f"[cycle] {key}: close-up card limit reached, left out: {', '.join(p.en for p, _ in left)}")
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
