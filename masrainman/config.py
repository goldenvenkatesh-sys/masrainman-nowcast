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
FB_GRAPH_VERSION = _env("FB_GRAPH_VERSION", "v23.0")

# --- WhatsApp Channel (via Whapi.Cloud gateway) -------------------------------
WHAPI_TOKEN = _env("WHAPI_TOKEN")
WHAPI_CHANNEL_ID = _env("WHAPI_CHANNEL_ID")    # looks like 1203630000000000@newsletter
WHAPI_BASE_URL = _env("WHAPI_BASE_URL", "https://gate.whapi.cloud")

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
