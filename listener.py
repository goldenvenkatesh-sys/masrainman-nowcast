"""OPTIONAL: instant approvals.

Without this, a "Yes" press is picked up by the next scheduled run (within
~10 minutes). Run this script 24x7 on any always-on machine (a spare PC,
Raspberry Pi, a free cloud VM, Railway/Render worker...) and posts go out
within a second or two of pressing Yes.

  pip install requests
  export TELEGRAM_BOT_TOKEN=... TELEGRAM_ADMIN_CHAT_ID=... \
         FB_PAGE_ID=... FB_PAGE_TOKEN=... WHAPI_TOKEN=... WHAPI_CHANNEL_ID=...
  python listener.py

Then set the repository variable APPROVALS_VIA_LISTENER=true so the
scheduled run stops polling Telegram (two pollers on one bot conflict).
"""
import time

from masrainman import config
from masrainman.social import process_pending_updates

if __name__ == "__main__":
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN first")
    print("MasRainman approval listener running (Ctrl+C to stop)")
    while True:
        try:
            process_pending_updates(timeout=50)     # long-poll: returns as soon as you tap a button
        except Exception as e:
            print(f"listener error: {e}")
            time.sleep(5)
