"""Hourly false-echo audit (added 13 Nov 2026).

Runs on its own every hour (.github/workflows/false-echo-audit.yml), separate
from the 10-minute radar cycle, so it keeps watching even if this chat or your
computer is off. It never changes the bot or its posts -- it only reports.

Each run:
  1. reads the radar picture the bot is actually using for cards
     (live/nowcast_bot.json + live/nowcast_bot_grid.bin.gz, after the bot's
     own satellite check)
  2. downloads the latest Meteosat IODC infrared, cloud mask and H SAF H63
     satellite rain rate for south India (same source as satellite_check.py)
  3. checks every moderate+ rain patch on the radar against the satellite:
       OK       satellite rain or a thunderstorm-cold top nearby
       2 RADARS no satellite support, but two independent radars see it --
                most likely shallow (warm) rain, kept by the bot
       COLD     only a fairly cold top nearby (thin cirrus can look like this)
       NO SAT   no satellite rain and no cold cloud nearby -- possible false
                echo (or shallow rain only radars see)
       CLEAR    clear sky above it -- false echo
  4. adds how much echo the bot's satellite check removed in the last hour,
     per radar (state/satellite_log.json)
  5. sends a side-by-side picture (radar | satellite) with a verdict to the
     admin's private Telegram bot chat.
"""
from __future__ import annotations

import gzip
import io
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import satellite_check as sc                     # noqa: E402
from masrainman import config                    # noqa: E402
from masrainman.places import PLACES             # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
LIVE = ROOT / "live"
OUT = ROOT / "output" / "false_echo_audit.jpg"
BUFFER_KM = 15
RADAR_NAMES = {"karaikal": "Karaikal", "chennai": "Chennai", "niot": "NIOT Chennai", "mangalore": "Mangaluru",
               "trivandrum": "Thiruvananthapuram", "kochi": "Kochi", "machilipatnam": "Machilipatnam",
               "sriharikota": "Sriharikota", "hyderabad": "Hyderabad"}


def load_grid():
    doc = json.loads((LIVE / "nowcast_bot.json").read_text())
    g = doc["grid"]
    raw = gzip.open(LIVE / g["file"]).read()
    arr = np.frombuffer(raw, np.uint8).reshape(len(g["layers"]), g["nrows"], g["ncols"])
    obs = arr[[L["name"] for L in g["layers"]].index("observed")].astype(float)
    obs[obs == 255] = np.nan                     # no coverage
    return doc, g, obs


def nearest_town(lat, lon):
    best = min(PLACES, key=lambda t: (t[2] - lat) ** 2 + ((t[3] - lon) * math.cos(math.radians(lat))) ** 2)
    km = math.hypot(best[2] - lat, (best[3] - lon) * math.cos(math.radians(lat))) * 111
    return best[0], km


def satellite():
    ir, t = sc._get_map("msg_iodc:ir108", None)
    clm, _ = sc._get_map("msg_iodc:clm", t)
    try:
        h63, ht = sc._get_map("msg_iodc:h63", None)
        rain = ~np.all(h63 == 255, axis=2) & ~np.all(h63 == 0, axis=2)
    except Exception:
        rain, ht = np.zeros(ir.shape[:2], bool), None
    grey = ir.mean(axis=2)
    clear = np.zeros(grey.shape, bool)
    for c in sc.CLEAR_COLOURS:
        clear |= np.all(clm == c, axis=2)
    clear &= grey < sc.WARM_GREY
    r = max(1, int(round(BUFFER_KM / (sc.STEP * 111.0))))
    yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
    disc = (xx * xx + yy * yy) <= r * r
    strong = ndimage.binary_dilation((grey >= sc.DEEP_GREY) | rain, structure=disc)
    cold = ndimage.binary_dilation(grey >= sc.RAIN_GREY, structure=disc)
    cloud_near = ndimage.binary_dilation(~clear, structure=disc)
    return dict(t=t, h63_t=ht, grey=grey, rain=rain, strong=strong, cold=cold, clear_far=~cloud_near)


def audit():
    doc, g, obs = load_grid()
    gen = datetime.fromisoformat(doc.get("generated_utc").replace("Z", "+00:00"))
    age = (datetime.now(timezone.utc) - gen).total_seconds() / 60
    S = satellite()
    st = g["step_deg"]
    lats = g["lat_north"] - np.arange(g["nrows"]) * st
    lons = g["lon_west"] + np.arange(g["ncols"]) * st
    LAT, LON = np.meshgrid(lats, lons, indexing="ij")
    sr = np.floor((sc.BBOX[3] - LAT) / sc.STEP).astype(int)
    scc = np.floor((LON - sc.BBOX[0]) / sc.STEP).astype(int)
    inb = (sr >= 0) & (sr < S["grey"].shape[0]) & (scc >= 0) & (scc < S["grey"].shape[1])
    sr, scc = np.clip(sr, 0, S["grey"].shape[0] - 1), np.clip(scc, 0, S["grey"].shape[1] - 1)

    agree = None
    try:   # where 2+ radars agreed in the latest radar cycle (state/radar_agree.png)
        meta = json.loads((config.STATE_DIR / "radar_agree.json").read_text())
        if time.time() - meta["t"] < 1800:
            from PIL import Image
            am = np.array(Image.open(config.STATE_DIR / "radar_agree.png").convert("L")) > 0
            ab, ast = meta["bbox"], meta["step"]
            ar = np.clip(np.floor((ab[3] - LAT) / ast).astype(int), 0, am.shape[0] - 1)
            ac = np.clip(np.floor((LON - ab[0]) / ast).astype(int), 0, am.shape[1] - 1)
            agree = am[ar, ac]
    except Exception:
        agree = None

    mod = np.nan_to_num(obs) >= 30
    lab, n = ndimage.label(mod, structure=np.ones((3, 3)))
    patches = []
    for i in range(1, n + 1):
        m = (lab == i) & inb
        if m.sum() < 3:
            continue
        strong = S["strong"][sr[m], scc[m]].mean()
        cold = S["cold"][sr[m], scc[m]].mean()
        clear = S["clear_far"][sr[m], scc[m]].mean()
        both = agree[m].mean() if agree is not None else 0.0
        if strong >= 0.1:
            status = "OK"
        elif both >= 0.3:
            status = "2 RADARS"
        elif clear >= 0.5:
            status = "CLEAR"
        elif cold >= 0.5:
            status = "COLD"
        else:
            status = "NO SAT"
        w = np.where(m, obs, 0)
        r0, c0 = np.unravel_index(np.argmax(w), w.shape)
        town, km = nearest_town(lats[r0], lons[c0])
        patches.append(dict(status=status, peak=int(obs[r0, c0]), nodes=int(m.sum()), lat=float(lats[r0]),
                            lon=float(lons[c0]), town=town, km=km, mask=m))
    patches.sort(key=lambda p: (p["status"] in ("OK", "2 RADARS"), -p["peak"]))

    # what the bot's own check removed in the last hour
    removed = {}
    try:
        log = json.loads((config.STATE_DIR / "satellite_log.json").read_text())
        for e in log:
            if e.get("t", 0) < time.time() - 3600:
                continue
            for radar, v in e.get("radars", {}).items():
                cur = removed.setdefault(radar, [0, 0])
                cur[0] += v["strong_gone_px"]
                cur[1] += v["strong_px"]
    except Exception:
        pass
    return doc, g, obs, S, patches, removed, age, (LAT, LON)


def render(g, obs, S, patches, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap, BoundaryNorm
    w, s = g["lon_west"], g["lat_north"] - (g["nrows"] - 1) * g["step_deg"]
    e, nth = g["lon_west"] + (g["ncols"] - 1) * g["step_deg"], g["lat_north"]
    # zoom to where the rain is (or the TN/Kerala core if none)
    if patches:
        la = [p["lat"] for p in patches]; lo = [p["lon"] for p in patches]
        box = [min(lo) - 1.2, max(lo) + 1.2, min(la) - 1.0, max(la) + 1.0]
    else:
        box = [75.5, 81.5, 8.0, 14.0]
    sat_ext = [sc.BBOX[0], sc.BBOX[2], sc.BBOX[1], sc.BBOX[3]]
    cmap = ListedColormap(["#3fbf5a", "#f2d21b", "#f28c1b", "#e3261b", "#b0128a"])
    norm = BoundaryNorm([30, 35, 40, 45, 50, 80], cmap.N)
    fig, ax = plt.subplots(1, 2, figsize=(13, 7.2), facecolor="white")
    for a in ax:
        a.imshow(S["grey"], cmap="gray", vmin=0, vmax=255, extent=sat_ext, origin="upper", alpha=0.55 if a is ax[0] else 1)
        a.set_xlim(box[0], box[1]); a.set_ylim(box[2], box[3]); a.set_aspect(1 / math.cos(math.radians((box[2] + box[3]) / 2)))
        a.tick_params(labelsize=8)
    lightish = np.where(np.nan_to_num(obs) >= 15, obs, np.nan)
    ax[0].imshow(np.where(np.nan_to_num(obs) >= 30, obs, np.nan), cmap=cmap, norm=norm, extent=[w, e, s, nth], origin="upper", interpolation="nearest")
    ax[0].imshow(np.where((np.nan_to_num(obs) >= 15) & (np.nan_to_num(obs) < 30), 1, np.nan), cmap=ListedColormap(["#7fb7e6"]), extent=[w, e, s, nth], origin="upper", alpha=0.6, interpolation="nearest")
    rain = np.where(S["rain"], 1.0, np.nan)
    ax[1].imshow(rain, cmap=ListedColormap(["#1f9bff"]), extent=sat_ext, origin="upper", alpha=0.75, interpolation="nearest")
    ax[0].set_title("Radar the bot is using now (light blue = light, colours = moderate+)", fontsize=10)
    ax[1].set_title(f"Meteosat IR {S['t'][11:16]} UTC + satellite rain (blue)", fontsize=10)
    colour = {"OK": "#1a9e3f", "2 RADARS": "#1a9e3f", "COLD": "#e69500", "NO SAT": "#d62728", "CLEAR": "#d62728"}
    for p in patches:
        for a in ax:
            a.add_patch(plt.Circle((p["lon"], p["lat"]), 0.18, fill=False, lw=2, color=colour[p["status"]]))
            a.text(p["lon"] + 0.2, p["lat"] + 0.12, f"{p['town']} {p['peak']} dBZ\n{p['status']}", fontsize=7.5,
                   color=colour[p["status"]], weight="bold")
    for name, lat, lon in [("Chennai", 13.08, 80.27), ("Madurai", 9.93, 78.12), ("Tiruchirappalli", 10.8, 78.69),
                           ("Salem", 11.66, 78.15), ("Karaikal", 10.93, 79.84), ("Kochi", 9.93, 76.27),
                           ("Coimbatore", 11.0, 76.96), ("Puducherry", 11.93, 79.83), ("Nellore", 14.44, 79.99)]:
        for a in ax:
            if box[0] < lon < box[1] and box[2] < lat < box[3]:
                a.plot(lon, lat, "o", ms=3, color="#0b3d91"); a.text(lon + 0.05, lat - 0.12, name, fontsize=7, color="#0b3d91")
    fig.suptitle("MasRainman hourly false-echo audit", fontsize=12, weight="bold")
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="jpg", dpi=90)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.getvalue())
    return buf.getvalue()


def caption(patches, removed, age, S):
    now = datetime.now(IST).strftime("%d %b %I:%M %p IST")
    lines = [f"🛰️ False-echo audit — {now}"]
    if age > 30:
        lines.append(f"⚠️ The bot's radar picture is {age:.0f} min old — the radar cycle may be stuck.")
    def by_town(status):
        best = {}
        for p in patches:
            if p["status"] in status and p["peak"] > best.get(p["town"], {"peak": -1})["peak"]:
                best[p["town"]] = p
        return sorted(best.values(), key=lambda p: -p["peak"])
    bad = by_town(("NO SAT", "CLEAR"))
    cold = by_town(("COLD",))
    two = by_town(("2 RADARS",))
    ok = by_town(("OK",))
    if not patches:
        lines.append("✅ No moderate+ rain on the radar right now.")
    elif not bad:
        lines.append(f"✅ All rain areas on the radar are backed by the satellite or by two radars.")
    else:
        lines.append(f"⚠️ Rain near {len(bad)} town(s) NOT backed by satellite or a second radar — possible false echo:")
        for p in bad[:6]:
            what = "clear sky above" if p["status"] == "CLEAR" else "no satellite rain / cold cloud"
            lines.append(f"• near {p['town']}: {p['peak']} dBZ, {what}")
    if cold:
        lines.append("🟠 Only thin/cold cloud above (check): " + ", ".join(f"{p['town']} {p['peak']} dBZ" for p in cold[:5]))
    if two:
        lines.append("🟢 No cold cloud, but 2 radars agree (shallow rain, kept): " + ", ".join(f"{p['town']} {p['peak']} dBZ" for p in two[:6]))
    if ok:
        lines.append("🟢 Confirmed by satellite: " + ", ".join(f"{p['town']} {p['peak']} dBZ" for p in ok[:6]))
    rem = [(r, v) for r, v in removed.items() if v[1] >= 300 and v[0] / v[1] >= 0.3]
    if rem:
        lines.append("🧹 Removed by the bot's satellite check in the last hour: " +
                     ", ".join(f"{RADAR_NAMES.get(r, r)} ({v[0] / v[1]:.0%} of its moderate+ echo)" for r, v in rem))
    elif removed:
        lines.append("🧹 Bot's satellite check removed little or nothing in the last hour.")
    lines.append(f"Satellite: Meteosat IODC {S['t'][11:16]} UTC" + (f", H63 rain {S['h63_t'][11:16]} UTC" if S.get("h63_t") else ""))
    return "\n".join(lines)[:1020]


def main():
    try:
        doc, g, obs, S, patches, removed, age, _ = audit()
    except Exception as e:
        msg = f"🛰️ False-echo audit could not run this hour: {e}"
        print(msg)
        if config.TELEGRAM_ADMIN_CHAT_ID and config.TELEGRAM_BOT_TOKEN:
            from masrainman.telegram import send_message
            send_message(config.TELEGRAM_ADMIN_CHAT_ID, msg)
        return
    img = render(g, obs, S, patches, OUT)
    cap = caption(patches, removed, age, S)
    print(cap)
    for p in patches:
        print(f"  {p['status']:7s} {p['town']:20s} {p['peak']} dBZ  {p['nodes']} nodes  ({p['lat']:.2f},{p['lon']:.2f})")
    if config.TELEGRAM_ADMIN_CHAT_ID and config.TELEGRAM_BOT_TOKEN:
        from masrainman.telegram import send_photo
        send_photo(config.TELEGRAM_ADMIN_CHAT_ID, img, cap)
        print("[audit] sent to admin chat")
    else:
        print("[audit] no Telegram admin chat configured -- not sent")


if __name__ == "__main__":
    main()
