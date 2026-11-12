"""MasRainman promotion agent -- grows the "South India Radar Nowcast Alerts" WhatsApp channel.

Strategy (from how WhatsApp itself surfaces channels):
  * The Explore directory ranks by the viewer's location + language most, then
    follower count, reactions and how regularly a channel posts -> Tamil + English
    keyword description, a post every day (even dry days), reaction prompts.
  * A forwarded update carries a link back to the channel -> every card asks
    followers to forward it to their family / local group.
  * Share links + QR codes -> QR poster on Facebook / Instagram, link on X/Telegram.
  * WhatsApp does not accept requests to be featured, so growth = consistency + shares.

Only OUR outlets are used: main MasRainman WhatsApp channel, Facebook Page,
Instagram, X (Twitter) and the Telegram channel. Never groups, DMs or other pages.

Marketing starts only after the alerts channel's logo + description have been
applied (state/wa_alert_brand_applied.txt is written by wa_channel/post_channel.mjs).
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import json
import secrets
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import requests

from . import config
from . import telegram as tg

IST = timezone(timedelta(hours=5, minutes=30))
STATE_DIR = config.STATE_DIR
BRAND_MARK = config.ROOT / "state" / "wa_alert_brand_applied.txt"
FOLLOWERS_CSV = config.ROOT / "state" / "wa_followers.csv"     # written by post_channel.mjs
LOG = config.ROOT / "state" / "promo_log.jsonl"
POSTER_URL = config._env("PROMO_POSTER_URL", "https://raw.githubusercontent.com/goldenvenkatesh-sys/"
                                             "masrainman-nowcast/main/assets/alert_channel_promo.jpg")
TAGS = "#RainAlert #ChennaiRains #TamilNaduWeather #KeralaRains #BengaluruRains #WeatherRadar #MasRainman"


def branding_done() -> bool:
    return BRAND_MARK.exists()


def log(outlet: str, action: str, ok: bool = True, detail: str = "") -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "outlet": outlet, "action": action, "ok": ok, "detail": detail[:200]},
                               ensure_ascii=False) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- Instagram --
def instagram_enabled() -> bool:
    return bool(config.IG_USER_ID and config.IG_TOKEN)


def post_instagram(caption: str, image_url: str = POSTER_URL) -> str:
    """Instagram Graph API (business/creator account linked to the FB Page;
    token needs instagram_basic + instagram_content_publish)."""
    base = f"https://graph.facebook.com/{config.FB_GRAPH_VERSION}/{config.IG_USER_ID}"
    r = requests.post(f"{base}/media", data={"image_url": image_url, "caption": caption,
                                             "access_token": config.IG_TOKEN}, timeout=60).json()
    if "id" not in r:
        raise RuntimeError(f"Instagram: {(r.get('error') or {}).get('message', r)}")
    for _ in range(10):                       # wait until the container is ready
        s = requests.get(f"https://graph.facebook.com/{config.FB_GRAPH_VERSION}/{r['id']}",
                         params={"fields": "status_code", "access_token": config.IG_TOKEN}, timeout=30).json()
        if s.get("status_code") == "FINISHED":
            break
        time.sleep(3)
    p = requests.post(f"{base}/media_publish", data={"creation_id": r["id"], "access_token": config.IG_TOKEN},
                      timeout=60).json()
    if "id" not in p:
        raise RuntimeError(f"Instagram publish: {(p.get('error') or {}).get('message', p)}")
    return p["id"]


# ------------------------------------------------------------------- X / Twitter --
def x_enabled() -> bool:
    return all([config.X_API_KEY, config.X_API_SECRET, config.X_ACCESS_TOKEN, config.X_ACCESS_SECRET])


def _oauth1(method: str, url: str) -> str:
    q = lambda s: urllib.parse.quote(str(s), safe="")
    p = {"oauth_consumer_key": config.X_API_KEY, "oauth_nonce": secrets.token_hex(16),
         "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": str(int(time.time())),
         "oauth_token": config.X_ACCESS_TOKEN, "oauth_version": "1.0"}
    base = "&".join([method, q(url), q("&".join(f"{q(k)}={q(v)}" for k, v in sorted(p.items())))])
    key = f"{q(config.X_API_SECRET)}&{q(config.X_ACCESS_SECRET)}"
    p["oauth_signature"] = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{k}="{q(v)}"' for k, v in sorted(p.items()))


def post_x(text: str) -> str:
    url = "https://api.x.com/2/tweets"
    r = requests.post(url, json={"text": text[:280]}, headers={"Authorization": _oauth1("POST", url)}, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"X: {r.status_code} {r.text[:200]}")
    return r.json().get("data", {}).get("id", "")


def x_text(peak: bool) -> str:
    lead = "🚨 Heavy rain on radar now! " if peak else "🛰️ "
    return (f"{lead}Free live radar rain alerts for Tamil Nadu, Chennai, Kerala, Karnataka & Andhra — "
            f"hourly + instant heavy-rain alerts in Tamil & English. Follow on WhatsApp 👉 {config.WA_CHANNEL_LINK}"
            f"\n#RainAlert #ChennaiRains #TNWeather")


# ------------------------------------------------------------ dry-day activity --
def keep_alive(st: dict, posted_alert_this_cycle: bool) -> None:
    """Directory ranking rewards regular posting: if the alerts channel has been
    silent ~a day, post one short morning radar status (text only) at 7 AM IST."""
    try:
        now = datetime.now(timezone.utc)
        ist = now.astimezone(IST)
        day = ist.date().isoformat()
        last = st.get("wa_channel_last_utc")
        silent_h = (now - datetime.fromisoformat(last)).total_seconds() / 3600 if last else 99
        if posted_alert_this_cycle or ist.hour != 7 or silent_h < 20 or st.get("wa_keepalive_day") == day:
            return
        q = config.WA_QUEUE_DIR
        q.mkdir(parents=True, exist_ok=True)
        (q / "90.txt").write_text(
            "☀️ காலை ரேடார் நிலவரம்: தென்னிந்தியாவில் தற்போது பெரிய மழை மேகங்கள் இல்லை. மழை தெரிந்தவுடன் "
            "உடனடி எச்சரிக்கை இங்கே வரும்.\nMorning radar check: no significant rain echoes over South India right now. "
            "The moment rain shows up on radar, the alert lands here.\n\n🔔 Keep notifications ON · "
            f"forward this channel to your family group 👉 {config.WA_CHANNEL_LINK}", encoding="utf-8")
        st["wa_keepalive_day"] = day
        log("WhatsApp alerts", "dry-day morning status")
        print("[promo] dry-day morning status queued for the alerts channel")
    except Exception as e:
        print(f"[promo] keep-alive skipped: {e}")


# ---------------------------------------------------------------- weekly report --
def _followers() -> list[dict]:
    try:
        with FOLLOWERS_CSV.open(encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def weekly_report(st: dict, force: bool = False) -> str | None:
    """Sunday 9 PM IST -> admin's private Telegram chat."""
    now = datetime.now(timezone.utc)
    ist = now.astimezone(IST)
    week = f"{ist.isocalendar()[0]}-W{ist.isocalendar()[1]:02d}"
    if not force and not (ist.weekday() == 6 and 21 <= ist.hour < 23 and st.get("report_week") != week):
        return None
    since = now - timedelta(days=7)

    def cnt(rows, col):
        vals = [int(r[col]) for r in rows if (r.get(col) or "").isdigit()]
        return vals

    rows = _followers()
    week_rows = [r for r in rows if datetime.fromisoformat(r["utc"]) >= since]
    before = [r for r in rows if datetime.fromisoformat(r["utc"]) < since]

    def change(col, label):
        cur = cnt(week_rows, col)
        if not cur:
            return f"• {label}: no reading yet"
        start = (cnt(before, col) or cur)[-1]
        end, hi, lo = cur[-1], max(cur), min(cur)
        gained = sum(max(b - a, 0) for a, b in zip(cur, cur[1:])) + max(cur[0] - start, 0)
        dropped = sum(max(a - b, 0) for a, b in zip(cur, cur[1:])) + max(start - cur[0], 0)
        return (f"• {label}: {start} → {end} ({end - start:+d})\n"
                f"   seen rising by {gained}, falling by {dropped} between checks · high {hi}, low {lo}")

    acts = []
    try:
        for line in LOG.read_text(encoding="utf-8").splitlines():
            a = json.loads(line)
            if datetime.fromisoformat(a["utc"]) >= since:
                acts.append(a)
    except Exception:
        pass
    by = {}
    for a in acts:
        k = (a["outlet"], a["ok"])
        by[k] = by.get(k, 0) + 1
    act_lines = "\n".join(f"• {o}: {n} {'post(s)' if ok else 'FAILED attempt(s)'}"
                          for (o, ok), n in sorted(by.items())) or "• nothing posted (waiting for channel branding?)"

    text = (f"📊 MasRainman promotion agent — weekly report ({(ist - timedelta(days=6)).strftime('%d %b')}"
            f" – {ist.strftime('%d %b %Y')})\n\n"
            f"👥 Followers\n{change('alerts', 'South India Radar Nowcast Alerts')}\n"
            f"{change('main', 'MasRainman main channel')}\n"
            "   (WhatsApp gives admins only the net follower count — exact unfollows are not exposed. "
            "'Falling' = drops seen between checks (every 1–3 h), so real unfollows are at least that many. "
            "For 30-day net follows/reach, see Channel insights in the WhatsApp app once you pass 100 followers.)\n\n"
            f"📣 What the agent did this week\n{act_lines}\n\n"
            "🧭 Strategy running\n"
            "• Tamil + English keyword description and logo (directory ranks by location + language first)\n"
            "• Daily posting, incl. a dry-day morning status (directory rewards regular posting)\n"
            "• Every card asks for a 🌧️ reaction and a forward to family groups (forwards link back to the channel)\n"
            "• Teasers in the main channel (8 AM, 6 PM + heavy-rain), Telegram daily, Facebook Sun + rain days, "
            "Instagram QR poster weekly, X via a daily one-tap button in your Telegram bot chat\n\n"
            "💡 Manual boost that only you can do: post the channel link to your WhatsApp Status twice a week.")
    if config.TELEGRAM_ADMIN_CHAT_ID:
        try:
            tg.send_message(config.TELEGRAM_ADMIN_CHAT_ID, text)
            st["report_week"] = week
            print("[promo] weekly report sent to admin")
        except Exception as e:
            print(f"[promo] weekly report failed: {e}")
    return text
