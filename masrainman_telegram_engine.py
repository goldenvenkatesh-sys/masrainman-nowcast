from datetime import datetime, timedelta
import math
import os
import time
import contextily as cx
import cv2
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import requests

# -----------------------------------------------------------------------------
# 1. TELEGRAM BOT CREDENTIALS
# -----------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = "8898452623:AAG1sYzutFvRkgNSR25h4PvcPaqOp8DdKxw"  # Replace with token from @BotFather
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID_HERE"  # e.g., "123456789" or "@your_channel"

# -----------------------------------------------------------------------------
# 2. EXTENT & 4-RADAR CONFIGURATION
# -----------------------------------------------------------------------------
LON_MIN, LON_MAX = 76.2, 82.2
LAT_MIN, LAT_MAX = 8.0, 14.2

RADAR_STATIONS = {
    "chennai_port": {
        "name": "Chennai Port DWR",
        "lat": 13.084,
        "lon": 80.293,
        "range_km": 250.0,
        "local_file": "live_chennai.jpg",
        "url": "https://mausam.imd.gov.in/Radar/cappi_chn.png",
        "layout": "imd_quadrant",
    },
    "pallikaranai": {
        "name": "Pallikaranai NIOT X-DWR",
        "lat": 12.9451,
        "lon": 80.2115,
        "range_km": 85.0,
        "local_file": "live_pallikaranai.jpg",
        "url": "https://mausam.imd.gov.in/Radar/cappi_pal.png",
        "layout": "full_disk",
    },
    "karaikal": {
        "name": "Karaikal DWR",
        "lat": 10.916,
        "lon": 79.843,
        "range_km": 250.0,
        "local_file": "live_karaikal.jpg",
        "url": "https://mausam.imd.gov.in/Radar/cappi_kkl.png",
        "layout": "imd_quadrant",
    },
    "trivandrum": {
        "name": "Thiruvananthapuram DWR",
        "lat": 8.484,
        "lon": 76.954,
        "range_km": 250.0,
        "local_file": "live_trivandrum.jpg",
        "url": "https://mausam.imd.gov.in/Radar/cappi_tvm.png",
        "layout": "imd_quadrant",
    },
}

MONITORING_ZONES = {
    "Chennai": (13.084, 80.270),
    "Tambaram": (12.924, 80.127),
    "Tiruvallur": (13.143, 79.907),
    "Kancheepuram": (12.834, 79.703),
    "Chengalpattu": (12.693, 79.983),
    "Puducherry": (11.941, 79.808),
    "Cuddalore": (11.748, 79.771),
    "Karaikal": (10.916, 79.843),
    "Nagapattinam": (10.767, 79.842),
    "Thiruvananthapuram": (8.484, 76.954),
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MASRainmanBot/2.0"
}


# -----------------------------------------------------------------------------
# 3. TELEGRAM DISPATCH FUNCTION
# -----------------------------------------------------------------------------
def send_telegram_nowcast(photo_path, caption_text):
    """Sends the generated nowcast snapshot and bulletin directly to Telegram."""
    if (
        TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE"
        or not os.path.exists(photo_path)
    ):
        print(
            "[!] Telegram skipped: Credentials not configured or image not found."
        )
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        # Telegram photo captions are limited to 1024 characters
        safe_caption = (
            caption_text[:1000] + "..."
            if len(caption_text) > 1024
            else caption_text
        )
        with open(photo_path, "rb") as img_file:
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": safe_caption,
                "parse_mode": "HTML",
            }
            files = {"photo": img_file}
            resp = requests.post(url, data=payload, files=files, timeout=20)
            if resp.status_code == 200:
                print("[✓] Successfully pushed nowcast alert & map to Telegram!")
            else:
                print(f"[!] Telegram API error: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"[!] Failed to deliver to Telegram: {e}")


# -----------------------------------------------------------------------------
# 4. RADAR EXTRACTION & COMPOSITING
# -----------------------------------------------------------------------------
def fetch_radar_image(station_cfg):
    try:
        resp = requests.get(
            station_cfg["url"], headers=HEADERS, timeout=8, verify=False
        )
        if resp.status_code == 200:
            arr = np.asarray(bytearray(resp.content), dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is not None:
                return img
    except Exception:
        pass
    if os.path.exists(station_cfg["local_file"]):
        return cv2.imread(station_cfg["local_file"])
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
        cx_px = int(w * 0.267)
        cy_px = int(h * 0.635)
        r_px = int(h * 0.355)
    else:
        cx_px = int(w * 0.440)
        cy_px = int(h * 0.500)
        r_px = int(h * 0.430)

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
    hsv = cv2.cvtColor(sampled, cv2.COLOR_BGR2HSV)
    H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]

    dbz_vals = np.zeros(len(px_x), dtype=np.float32)
    dbz_vals[(H >= 95) & (H <= 125) & (S > 70) & (V > 50)] = 25.0
    dbz_vals[(H >= 80) & (H < 95) & (S > 80) & (V > 100)] = 32.0
    dbz_vals[(H >= 18) & (H <= 38) & (S > 100) & (V > 120)] = 40.0
    dbz_vals[((H < 12) | (H > 165)) & (S > 120) & (V > 80)] = 48.0

    grid_out[in_range] = dbz_vals
    return grid_out


def build_multi_radar_composite(gx, gy):
    composite_dbz = np.zeros_like(gx)
    active_radars = []
    for key, cfg in RADAR_STATIONS.items():
        stn_dbz = extract_station_grid(cfg, gx, gy)
        active_count = np.count_nonzero(stn_dbz >= 20.0)
        if active_count > 0:
            active_radars.append(f"{cfg['name']} ({active_count} cells)")
        composite_dbz = np.maximum(composite_dbz, stn_dbz)
    return composite_dbz, active_radars


# -----------------------------------------------------------------------------
# 5. MOTION ANALYSIS & PLOTTER
# -----------------------------------------------------------------------------
def analyze_nowcast(prev_dbz, curr_dbz, gx, gy):
    p_norm = np.uint8((prev_dbz / 65.0) * 255)
    c_norm = np.uint8((curr_dbz / 65.0) * 255)

    flow = cv2.calcOpticalFlowFarneback(
        p_norm,
        c_norm,
        None,
        pyr_scale=0.5,
        levels=3,
        winsize=15,
        iterations=3,
        poly_n=5,
        poly_sigma=1.2,
        flags=0,
    )
    u, v = flow[..., 0], flow[..., 1]

    rain_mask = curr_dbz >= 30.0
    if np.any(rain_mask):
        mean_u = float(np.mean(u[rain_mask]))
        mean_v = float(np.mean(v[rain_mask]))
    else:
        mean_u, mean_v = 0.0, 0.0

    h, w = curr_dbz.shape
    c_mesh, r_mesh = np.meshgrid(np.arange(w), np.arange(h))
    map_x = np.float32(c_mesh - (u * 6.0))
    map_y = np.float32(r_mesh - (v * 6.0))
    extrapolated_90m = cv2.remap(
        curr_dbz.astype(np.float32),
        map_x,
        map_y,
        interpolation=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )

    dx_hr = mean_u * ((LON_MAX - LON_MIN) / w) * 111.0 * 4.0
    dy_hr = mean_v * ((LAT_MAX - LAT_MIN) / h) * 111.0 * 4.0
    speed_kmh = math.sqrt(dx_hr**2 + dy_hr**2)
    heading_deg = (math.degrees(math.atan2(dx_hr, dy_hr)) + 360) % 360

    impacts = []
    now = datetime.now()
    for name, (zlat, zlon) in MONITORING_ZONES.items():
        box = (
            (gy >= zlat - 0.12)
            & (gy <= zlat + 0.12)
            & (gx >= zlon - 0.12)
            & (gx <= zlon + 0.12)
        )
        curr_val = float(np.max(curr_dbz[box])) if np.any(box) else 0.0
        fut_val = float(np.max(extrapolated_90m[box])) if np.any(box) else 0.0

        if curr_val >= 40.0:
            impacts.append({"zone": name, "status": "Active Heavy", "eta": "Now"})
        elif fut_val >= 35.0:
            eta_time = (now + timedelta(minutes=60)).strftime("%I:%M %p")
            impacts.append(
                {"zone": name, "status": "Approaching Heavy", "eta": f"~{eta_time}"}
            )

    return (
        extrapolated_90m,
        impacts,
        speed_kmh,
        heading_deg,
        mean_u,
        mean_v,
    )


def plot_composite_map(
    gx, gy, curr_dbz, extrapolated_dbz, impacts, mean_u, mean_v
):
    fig, ax = plt.subplots(figsize=(9, 12), dpi=180)
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
        np.ma.masked_less(curr_dbz, 20),
        cmap=cmap,
        norm=norm,
        alpha=0.88,
        shading="nearest",
        zorder=3,
    )

    if np.any(extrapolated_dbz >= 35):
        ax.contour(
            gx,
            gy,
            extrapolated_dbz,
            levels=[35],
            colors="black",
            linestyles="dashed",
            linewidths=1.6,
            zorder=4,
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
            marker="o",
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

    for item in impacts:
        lt, ln = MONITORING_ZONES[item["zone"]]
        ax.text(
            ln,
            lt + 0.08,
            f"{item['zone']}\nHeavy\n{item['eta']}",
            fontsize=8.5,
            fontweight="bold",
            ha="center",
            bbox=dict(
                boxstyle="square,pad=0.2",
                facecolor="white",
                edgecolor="black",
                linewidth=0.9,
            ),
            zorder=7,
        )

    now_str = datetime.now().strftime("%I:%M %p")
    eta_str = (datetime.now() + timedelta(minutes=90)).strftime("%I:%M %p")

    plt.suptitle(
        "MASRainman Multi-Radar Composite Rainfall Nowcast",
        fontsize=13,
        weight="bold",
        y=0.96,
    )
    ax.set_title(
        f"Radars: Chennai Port | Pallikaranai | Karaikal | Trivandrum\n"
        f"Colours: now {now_str}   Dashed: ~{eta_str} (Extrapolated)   Arrows: motion",
        fontsize=9,
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
    cbar.set_ticklabels(["20", "30", "40", "50"])
    cbar.set_label(
        "radar echo strength (dBZ)      Moderate 35+    Heavy 45+",
        fontsize=8.5,
        labelpad=4,
    )

    fig.text(
        0.5,
        0.02,
        "Extrapolated from current storm motion, not a guaranteed forecast. masrainman.blogspot.com\n"
        "Multi-DWR Composite Mosaic (IMD & NIOT). Map: Esri World Gray Canvas",
        ha="center",
        fontsize=7.5,
        color="#333333",
    )

    out_file = "nowcast_latest.png"
    plt.savefig(out_file, bbox_inches="tight")
    plt.close()
    return out_file


def format_bulletin(impacts, active_radars, speed_kmh, heading_deg, max_dbz):
    now_ts = datetime.now().strftime("%I:%M %p, %d %b %Y")
    report = [
        f"<b>⛈️ MASRAINMAN LIVE NOWCAST | {now_ts}</b>",
        f"<b>Radars Monitored:</b> Chennai Port, Pallikaranai, Karaikal, Trivandrum",
        f"<b>Radar Activity:</b> {', '.join(active_radars) if active_radars else 'Clear skies across peninsular radar sweeps'}",
    ]

    if max_dbz < 20.0:
        report.append(
            "\n<b>Status:</b> Dry conditions continue. No active rainbands detected."
        )
    else:
        report.append(
            f"\n<b>Peak Echo:</b> {max_dbz:.1f} dBZ (Moderate/Heavy Convective Core)"
        )
        report.append(
            f"<b>Storm Motion:</b> ~{speed_kmh:.1f} km/h toward bearing {heading_deg:.0f}°"
        )
        report.append("\n<b>🎯 Impact Zones & ETA:</b>")
        if impacts:
            for item in impacts:
                report.append(
                    f" • <b>{item['zone']}</b>: {item['status']} (ETA: {item['eta']})"
                )
        else:
            report.append(" • Cells drifting offshore / away from major towns.")

    report.append("\n<i>Automated Nowcast System • masrainman.blogspot.com</i>")
    return "\n".join(report)


# -----------------------------------------------------------------------------
# 6. MAIN ENGINE LOOP
# -----------------------------------------------------------------------------
def main():
    print("[+] Starting MASRainman 24/7 Telegram Automated Radar Nowcast...")
    lons = np.linspace(LON_MIN, LON_MAX, 450)
    lats = np.linspace(LAT_MIN, LAT_MAX, 450)
    gx, gy = np.meshgrid(lons, lats)

    prev_dbz = None

    while True:
        cycle_start = time.time()
        print(
            f"\n[>>>] Running scan cycle at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}..."
        )

        curr_dbz, active_radars = build_multi_radar_composite(gx, gy)
        if prev_dbz is None:
            prev_dbz = curr_dbz.copy()

        max_dbz = float(np.max(curr_dbz))
        extrapolated_dbz, impacts, speed_kmh, heading_deg, u, v = (
            analyze_nowcast(prev_dbz, curr_dbz, gx, gy)
        )

        # Render map snapshot
        map_image = plot_composite_map(
            gx, gy, curr_dbz, extrapolated_dbz, impacts, u, v
        )
        bulletin_text = format_bulletin(
            impacts, active_radars, speed_kmh, heading_deg, max_dbz
        )

        # Dispatch alert to Telegram
        # (Only alert every cycle if rain exists, or send hourly heartbeats)
        send_telegram_nowcast(map_image, bulletin_text)

        prev_dbz = curr_dbz.copy()

        elapsed = time.time() - cycle_start
        wait_time = max(60.0, 600.0 - elapsed)
        print(
            f"[⏳] Cycle complete. Sleeping {wait_time / 60:.1f} min until next scan..."
        )
        time.sleep(wait_time)


if __name__ == "__main__":
    main()
