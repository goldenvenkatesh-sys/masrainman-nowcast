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


def build_items(engine):
    from masrainman.alerts import BOT_JSON, build_caption, build_nowcast, for_region
    from masrainman.card import MAP_BOX, render_card
    from masrainman.regions import ENABLED, HASHTAGS, REGIONS, VIEW_LAT_DEG, best_window

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

    for key, en, ta, bbox, states, zone in REGIONS:
        if key not in ENABLED:
            continue
        rnc = for_region(nc, states, en, ta, HASHTAGS.get(key, ""), zone)
        if not (rnc.raining or rnc.coming):
            print(f"[cycle] {key}: no moderate+ rain at any town -- no card")
            continue
        towns = [(p.lat, p.lon, p.now_dbz) for p in rnc.raining] + [(p.lat, p.lon, p.soon_dbz) for p in rnc.coming]
        center, score, peak = best_window(arr, g, obs_i, proj_i, bbox, VIEW_LAT_DEG, aspect, towns)
        path = config.OUTPUT_DIR / f"masrainman_{key}.jpg"
        try:
            render_card(rnc, engine, center, VIEW_LAT_DEG, en, ta, path)
        except Exception:
            traceback.print_exc()
            continue
        caption = build_caption(rnc, limit=940)        # room left for the admin prompt line
        (config.OUTPUT_DIR / f"caption_{key}.txt").write_text(caption, encoding="utf-8")
        print(f"[cycle] {key}: card -> {path} (view centre {center[0]:.2f},{center[1]:.2f}, peak {peak:.0f} dBZ)")
        print(caption)
        items.append((key, path, caption))
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
