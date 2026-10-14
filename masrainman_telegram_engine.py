from datetime import datetime, timedelta
import os
import contextily as cx
import cv2
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import requests

# Reads your Token and Chat ID directly from GitHub Secrets!
TELEGRAM_BOT_TOKEN = os.environ.get("8898452623:AAG1sYzutFvRkgNSR25h4PvcPaqOp8DdKxw", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("1227665254", "").strip()

LON_MIN, LON_MAX = 76.2, 82.2
LAT_MIN, LAT_MAX = 8.0, 14.2

RADAR_STATIONS = {
    "chennai_port": {
        "name": "Chennai Port DWR",
        "lat": 13.084,
        "lon": 80.293,
        "range_km": 250.0,
        "url": "https://mausam.imd.gov.in/Radar/cappi_chn.png",
        "layout": "imd_quadrant",
    },
    "pallikaranai": {
        "name": "Pallikaranai NIOT X-DWR",
        "lat": 12.9451,
        "lon": 80.2115,
        "range_km": 85.0,
        "url": "https://mausam.imd.gov.in/Radar/cappi_pal.png",
        "layout": "full_disk",
    },
    "karaikal": {
        "name": "Karaikal DWR",
        "lat": 10.916,
        "lon": 79.843,
        "range_km": 250.0,
        "url": "https://mausam.imd.gov.in/Radar/cappi_kkl.png",
        "layout": "imd_quadrant",
    },
    "trivandrum": {
        "name": "Thiruvananthapuram DWR",
        "lat": 8.484,
        "lon": 76.954,
        "range_km": 250.0,
        "url": "https://mausam.imd.gov.in/Radar/cappi_tvm.png",
        "layout": "imd_quadrant",
    },
}

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def send_telegram_nowcast(photo_path, caption_text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(
            "[ERROR] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID secret is empty in GitHub Settings!"
        )
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    with open(photo_path, "rb") as img_file:
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "caption": caption_text[:1024],
            "parse_mode": "HTML",
        }
        files = {"photo": img_file}
        resp = requests.post(url, data=payload, files=files, timeout=25)
        print("Telegram Delivery Result:", resp.status_code, resp.text)


def fetch_radar_image(station_cfg):
    try:
        resp = requests.get(station_cfg["url"], headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            arr = np.asarray(bytearray(resp.content), dtype=np.uint8)
            return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception as e:
        print(f"Fetch warning for {station_cfg['name']}: {e}")
    return None


def extract_station_grid(station_cfg, gx, gy):
    img = fetch_radar_image(station_cfg)
    grid_out = np.zeros_like(gx)
    if img is None:
        return grid_out

    h, w, _ = img.shape
    s_lat, s_lon = station_cfg["lat"], station_cfg["lon"]
    max_range = station_cfg["range_km"]

    if station_cfg["layout"] == "imd_quadrant":
        cx_px, cy_px, r_px = int(w * 0.267), int(h * 0.635), int(h * 0.355)
    else:
        cx_px, cy_px, r_px = int(w * 0.440), int(h * 0.500), int(h * 0.430)

    dx_km = (gx - s_lon) * 111.0 * np.cos(np.radians(s_lat))
    dy_km = (gy - s_lat) * 111.0
    dist_km = np.sqrt(dx_km**2 + dy_km**2)

    in_range = dist_km <= max_range
    if not np.any(in_range):
        return grid_out

    px_x = np.clip(
        np.int32(cx_px + (dx_km[in_range] / max_range) * r_px), 0, w - 1
    )
    px_y = np.clip(
        np.int32(cy_px - (dy_km[in_range] / max_range) * r_px), 0, h - 1
    )

    sampled = img[px_y, px_x]
    hsv = cv2.cvtColor(sampled.reshape(-1, 1, 3), cv2.COLOR_BGR2HSV).reshape(
        -1, 3
    )
    H, S, V = hsv[:, 0], hsv[:, 1], hsv[:, 2]

    dbz_vals = np.zeros(len(px_x), dtype=np.float32)
    dbz_vals[(H >= 95) & (H <= 125) & (S > 70) & (V > 50)] = 25.0
    dbz_vals[(H >= 80) & (H < 95) & (S > 80) & (V > 100)] = 32.0
    dbz_vals[(H >= 18) & (H <= 38) & (S > 100) & (V > 120)] = 40.0
    dbz_vals[((H < 12) | (H > 165)) & (S > 120) & (V > 80)] = 48.0

    grid_out[in_range] = dbz_vals
    return grid_out


def main():
    print("[+] Starting Single Cloud Scan Cycle...")
    lons = np.linspace(LON_MIN, LON_MAX, 450)
    lats = np.linspace(LAT_MIN, LAT_MAX, 450)
    gx, gy = np.meshgrid(lons, lats)

    composite_dbz = np.zeros_like(gx)
    active_radars = []
    for key, cfg in RADAR_STATIONS.items():
        stn_dbz = extract_station_grid(cfg, gx, gy)
        active_count = np.count_nonzero(stn_dbz >= 20.0)
        if active_count > 0:
            active_radars.append(f"{cfg['name']} ({active_count} cells)")
        composite_dbz = np.maximum(composite_dbz, stn_dbz)

    fig, ax = plt.subplots(figsize=(9, 12), dpi=160)
    ax.set_xlim(LON_MIN, LON_MAX)
    ax.set_ylim(LAT_MIN, LAT_MAX)
    ax.set_aspect("equal")

    try:
        cx.add_basemap(
            ax,
            crs="EPSG:4326",
            source=cx.providers.Esri.WorldGrayCanvas,
            attribution=False,
            zoom=8,
        )
    except Exception:
        ax.set_facecolor("#ebecee")

    cmap_colors = [
        "#8de08d",
        "#42b966",
        "#f6e359",
        "#f39634",
        "#e14231",
        "#9d1a4d",
        "#71288f",
    ]
    bounds = [20, 25, 30, 35, 40, 45, 50, 65]
    norm = mcolors.BoundaryNorm(bounds, len(cmap_colors))
    cmap = mcolors.ListedColormap(cmap_colors)

    mesh = ax.pcolormesh(
        gx,
        gy,
        np.ma.masked_less(composite_dbz, 20),
        cmap=cmap,
        norm=norm,
        alpha=0.88,
        shading="nearest",
        zorder=3,
    )

    theta = np.linspace(0, 2 * np.pi, 200)
    for key, cfg in RADAR_STATIONS.items():
        s_lat, s_lon = cfg["lat"], cfg["lon"]
        deg_r = cfg["range_km"] / 111.0
        ax.plot(
            s_lon + deg_r * np.cos(theta),
            s_lat + deg_r * np.sin(theta),
            linestyle=":",
            color="#333333",
            linewidth=1.1,
            zorder=5,
        )
        ax.scatter(
            s_lon,
            s_lat,
            s=25,
            facecolor="white",
            edgecolor="black",
            linewidth=1.4,
            zorder=6,
        )
        ax.text(
            s_lon + 0.1,
            s_lat - 0.1,
            cfg["name"],
            fontsize=8,
            fontweight="bold",
            color="#222222",
            zorder=6,
        )

    now_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
    now_str = now_ist.strftime("%I:%M %p IST, %d %b %Y")

    plt.suptitle(
        "MASRainman Multi-Radar Composite Rainfall Nowcast",
        fontsize=13,
        weight="bold",
        y=0.96,
    )
    ax.set_title(
        f"Radars: Chennai Port | Pallikaranai | Karaikal | Trivandrum\nScan Time: {now_str}",
        fontsize=9.5,
        pad=8,
    )
    ax.set_xticks([])
    ax.set_yticks([])

    cbar = plt.colorbar(
        mesh,
        ax=ax,
        orientation="horizontal",
        fraction=0.04,
        pad=0.03,
        shrink=0.85,
        aspect=30,
    )
    cbar.set_ticks([20, 30, 40, 50])
    cbar.set_label(
        "radar echo strength (dBZ)      Moderate 35+    Heavy 45+", fontsize=8.5
    )

    out_file = "nowcast_latest.png"
    plt.savefig(out_file, bbox_inches="tight")
    plt.close()

    max_dbz = float(np.max(composite_dbz))
    caption = (
        f"<b>⛈️ MASRAINMAN LIVE RADAR NOWCAST</b>\n"
        f"<b>Time:</b> {now_str}\n"
        f"<b>Activity:</b> {', '.join(active_radars) if active_radars else 'Clear skies across radar sweeps'}\n"
        f"<b>Peak Echo:</b> {max_dbz:.1f} dBZ\n\n"
        f"<i>Automated 24/7 Cloud Runner • masrainman.blogspot.com</i>"
    )
    send_telegram_nowcast(out_file, caption)
    print("[✓] Cloud Scan Cycle Completed!")


if __name__ == "__main__":
    main()
