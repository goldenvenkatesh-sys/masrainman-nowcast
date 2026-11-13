"""All settings come from environment variables (GitHub Actions secrets /
variables), so nothing private is ever committed to the repo."""
import os
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _flag(name: str, default: bool) -> bool:
    v = _env(name)
    return default if not v else v.lower() in ("1", "true", "yes", "on")


ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
FONTS = ASSETS / "fonts"
STATE_DIR = ROOT / "state"
OUTPUT_DIR = ROOT / "output"

BRAND_NAME = _env("BRAND_NAME", "MasRainman")
BRAND_HANDLE = _env("BRAND_HANDLE", "@masrainman")
HASHTAGS = _env("HASHTAGS", "#MasRainman #Nowcast")      # region tags are added per card (regions.py)

# --- Telegram ---------------------------------------------------------------
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
# Public channel/group that receives the automatic update every cycle
# (e.g. "@masrainman" or "-1001234567890"). Leave empty to skip.
TELEGRAM_CHANNEL_ID = _env("TELEGRAM_CHANNEL_ID")
# "new" = a new post every cycle, "edit" = keep ONE live post and keep
# replacing its picture/caption (no feed spam).
TELEGRAM_CHANNEL_MODE = _env("TELEGRAM_CHANNEL_MODE", "new").lower()
# Second channel/group (e.g. the users' discussion channel) that gets a copy of
# each region's radar card at most once every TELEGRAM_FORWARD_EVERY_MIN
# minutes. "@channelusername" or "-100...". Leave empty to skip.
TELEGRAM_FORWARD_CHAT_ID = _env("TELEGRAM_FORWARD_CHAT_ID")
TELEGRAM_FORWARD_EVERY_MIN = int(_env("TELEGRAM_FORWARD_EVERY_MIN", "30"))
# Your private chat with the bot -- where the Yes/No approval card arrives.
TELEGRAM_ADMIN_CHAT_ID = _env("TELEGRAM_ADMIN_CHAT_ID")
# Comma-separated Telegram user ids allowed to press "Yes". Defaults to the
# admin chat id (a private chat id equals your user id).
TELEGRAM_ADMIN_IDS = {int(x) for x in _env("TELEGRAM_ADMIN_IDS", TELEGRAM_ADMIN_CHAT_ID).replace(" ", "").split(",")
                      if x.lstrip("-").isdigit()}
# An approval card older than this cannot be posted any more (the radar
# picture would be stale by then).
APPROVAL_TTL_MIN = int(_env("APPROVAL_TTL_MIN", "45"))

# --- Facebook Page (Graph API) ----------------------------------------------
FB_PAGE_ID = _env("FB_PAGE_ID")
FB_PAGE_TOKEN = _env("FB_PAGE_TOKEN")          # long-lived PAGE access token
# When FB_PAGE_ID / FB_PAGE_TOKEN are not set, the Instagram token (a Meta USER
# token with pages_manage_posts) is used to fetch these Pages' own tokens at
# run time (13 Nov 2026): Tamil Nadu page + Kerala page.
FB_PAGE_IDS = [x.strip() for x in _env("FB_PAGE_IDS", "102808714416567,351578428034103").split(",") if x.strip()]
FB_GRAPH_VERSION = _env("FB_GRAPH_VERSION", "v23.0")

# --- WhatsApp Channel (via Whapi.Cloud gateway) -------------------------------
WHAPI_TOKEN = _env("WHAPI_TOKEN")
WHAPI_CHANNEL_ID = _env("WHAPI_CHANNEL_ID")    # looks like 1203630000000000@newsletter
WHAPI_BASE_URL = _env("WHAPI_BASE_URL", "https://gate.whapi.cloud")
# Automatic hourly post to the WhatsApp Channel (no Yes button needed).
# Sent through Whapi when WHAPI_* is set; otherwise the cards are queued in
# output/wa_queue/ and the free wa_channel/ (Baileys) step in the workflow posts them.
WA_CHANNEL_AUTO = _flag("WA_CHANNEL_AUTO", True)
WA_CHANNEL_EVERY_MIN = int(_env("WA_CHANNEL_EVERY_MIN", "60"))
# Peak events: when any region's card shows at least WA_CHANNEL_PEAK_DBZ
# (45 dBZ = heavy/intense rain), post every WA_CHANNEL_PEAK_EVERY_MIN instead.
WA_CHANNEL_PEAK_DBZ = float(_env("WA_CHANNEL_PEAK_DBZ", "45"))
WA_CHANNEL_PEAK_EVERY_MIN = int(_env("WA_CHANNEL_PEAK_EVERY_MIN", "30"))
# 12 Nov 2026: radar cards + alerts moved OUT of the main MasRainman channel into
# the dedicated "South India Radar Nowcast Alerts" channel (fans said the alerts
# drowned the main channel's periodic updates).
WA_CHANNEL_LINK = _env("WA_ALERT_CHANNEL_LINK", "https://whatsapp.com/channel/0029VbE2C5F9RZAanCxEFv0o")
WA_CHANNEL_NAME = _env("WA_ALERT_CHANNEL_NAME", "South India Radar Nowcast Alerts")
WA_QUEUE_DIR = OUTPUT_DIR / "wa_queue"            # -> alerts channel

# Promotion agent: the main MasRainman channel gets NO radar cards any more, only a
# short "join the alerts channel" post: at most once per slot (IST hours below)
# plus one extra during a peak rain event, never more than WA_PROMO_MAX_PER_DAY.
WA_MAIN_CHANNEL_LINK = _env("WA_MAIN_CHANNEL_LINK", "https://whatsapp.com/channel/0029VaBMeqH4Y9lg93fA8w3Y")
WA_PROMO_AUTO = _flag("WA_PROMO_AUTO", True)
WA_PROMO_SLOTS_IST = [int(x) for x in _env("WA_PROMO_SLOTS_IST", "8,18").split(",") if x.strip().isdigit()]
WA_PROMO_MAX_PER_DAY = int(_env("WA_PROMO_MAX_PER_DAY", "3"))
WA_PROMO_GAP_H = float(_env("WA_PROMO_GAP_H", "4"))
WA_PROMO_QUEUE_DIR = OUTPUT_DIR / "wa_promo_queue"  # -> main channel
# Telegram channel gets one daily "also on WhatsApp" line (IST hour)
TG_PROMO_HOUR_IST = int(_env("TG_PROMO_HOUR_IST", "19"))
# Instagram (business/creator account linked to the Facebook Page)
IG_USER_ID = _env("IG_USER_ID")
IG_TOKEN = _env("IG_TOKEN") or _env("FB_PAGE_TOKEN")
# X / Twitter (OAuth 1.0a user keys with Read+Write)
X_API_KEY = _env("X_API_KEY")
X_API_SECRET = _env("X_API_SECRET")
X_ACCESS_TOKEN = _env("X_ACCESS_TOKEN")
X_ACCESS_SECRET = _env("X_ACCESS_SECRET")

# --- What to post -------------------------------------------------------------
# Minimum dBZ for a place to be named (and for a region to get a card):
# 30 = moderate, 40 = heavy.
ALERT_MIN_DBZ = float(_env("ALERT_MIN_DBZ", "30"))
# Region views, zoom and block size live in masrainman/regions.py and card.py
# (env: REGIONS, VIEW_LAT_DEG, CARD_BLOCK, BASEMAP_URL).
# Set to true if you run listener.py somewhere 24x7 (instant approvals);
# the scheduled run then leaves Telegram updates to the listener (two
# clients polling the same bot would fight with HTTP 409 errors).
APPROVALS_VIA_LISTENER = _flag("APPROVALS_VIA_LISTENER", False)
