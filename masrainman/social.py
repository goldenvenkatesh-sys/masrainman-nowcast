"""Telegram automation + the Yes/No approval flow.

Every cycle (publish_cycle):
  1. Telegram channel  -> the new radar card is posted automatically
                          (or the one live post is edited, TELEGRAM_CHANNEL_MODE=edit)
  2. Your private chat -> the same card arrives with two buttons:
                          [✅ Yes, post to Facebook + WhatsApp]  [❌ No]

When you press Yes (handle_update):
  the picture + caption are taken straight from that Telegram message and
  posted to every configured target. Because everything needed is inside
  the message itself, the press can be handled either by the next scheduled
  run (up to ~10 min later) or instantly by listener.py if you run it.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from . import config, publishers
from . import telegram as tg
from pathlib import Path

STATE = config.STATE_DIR / "social_state.json"
ASK_MARK = "\n\n👉 "                      # separates the public caption from the admin prompt
IST = timezone(timedelta(hours=5, minutes=30))


def _load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save(st: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1))


def _radar_signature(doc: dict) -> str:
    """Changes only when at least one radar delivered a new frame."""
    return "|".join(sorted(f"{r['product']}={r.get('obs_time_utc')}" for r in doc.get("radars", [])
                           if r.get("obs_time_utc")))


def _keyboard() -> dict:
    names = " + ".join(n for n, _ in publishers.enabled_targets())
    return {"inline_keyboard": [[{"text": f"✅ Yes, post to {names}", "callback_data": "mr:yes"}],
                                [{"text": "❌ No", "callback_data": "mr:no"}]]}


# ------------------------------------------------------------------ cycle --
def publish_cycle(items: list[tuple[str, "Path", str]], doc: dict) -> None:
    """items: (region key, card path, caption) for every region with rain."""
    if not config.TELEGRAM_BOT_TOKEN:
        print("[social] TELEGRAM_BOT_TOKEN not set -- skipping all posting")
        return
    st = _load()
    sig = _radar_signature(doc)
    if sig and sig == st.get("last_signature"):
        print("[social] no new radar frame since last post -- nothing to send")
        return
    if not items:
        print("[social] no region has rain worth posting this cycle")
        st["last_signature"] = sig
        _save(st)
        return

    channel_ids = st.setdefault("channel_message_ids", {})
    approval_ids = st.setdefault("approval_message_ids", {})
    targets = " + ".join(n for n, _ in publishers.enabled_targets())
    for key, path, caption in items:
        image = path.read_bytes()
        # 1) automatic Telegram channel post (one live post per region in "edit" mode)
        if config.TELEGRAM_CHANNEL_ID:
            try:
                mid = channel_ids.get(key) if config.TELEGRAM_CHANNEL_MODE == "edit" else None
                if mid:
                    try:
                        tg.edit_photo(config.TELEGRAM_CHANNEL_ID, mid, image, caption)
                        print(f"[social] {key}: channel post {mid} updated")
                    except tg.TelegramError as e:
                        print(f"[social] {key}: edit failed ({e}) -- sending a new post")
                        mid = None
                if not mid:
                    m = tg.send_photo(config.TELEGRAM_CHANNEL_ID, image, caption)
                    channel_ids[key] = m["message_id"]
                    print(f"[social] {key}: channel post {m['message_id']} sent")
            except Exception as e:
                print(f"[social] {key}: channel post failed: {e}")

        # 2) approval card in the admin's private chat
        if not (config.TELEGRAM_ADMIN_CHAT_ID and targets):
            continue
        try:
            old = approval_ids.get(key)
            if old:                        # older unanswered card for this region: drop its buttons
                try:
                    tg.remove_buttons(config.TELEGRAM_ADMIN_CHAT_ID, old)
                except tg.TelegramError:
                    pass                   # already answered / not modified
            m = tg.send_photo(config.TELEGRAM_ADMIN_CHAT_ID, image,
                              caption + ASK_MARK + f"Post this to {targets}?", reply_markup=_keyboard())
            approval_ids[key] = m["message_id"]
            print(f"[social] {key}: approval card {m['message_id']} sent to admin")
        except Exception as e:
            print(f"[social] {key}: approval card failed: {e}")

    st["last_signature"] = sig
    _save(st)


# --------------------------------------------------------------- approvals --
def _stamp() -> str:
    return datetime.now(IST).strftime("%I:%M %p").lstrip("0")


def handle_update(upd: dict) -> None:
    msg = upd.get("message")
    if msg and msg.get("chat", {}).get("type") == "private":
        text = (msg.get("text") or "").strip().lower()
        if text.startswith(("/start", "/id")):
            tg.send_message(msg["chat"]["id"],
                            f"Hi! Your chat id is {msg['chat']['id']} (user id {msg['from']['id']}).\n"
                            "Put it in the TELEGRAM_ADMIN_CHAT_ID secret to receive approval cards.")
        return

    cq = upd.get("callback_query")
    if not cq or not str(cq.get("data", "")).startswith("mr:"):
        return
    who = cq.get("from", {}).get("id")
    if who not in config.TELEGRAM_ADMIN_IDS:
        tg.answer_callback(cq["id"], "Only MasRainman admins can approve posts.", alert=True)
        return
    m = cq.get("message") or {}
    chat_id, mid = m.get("chat", {}).get("id"), m.get("message_id")
    full = m.get("caption") or ""
    public = full.split(ASK_MARK)[0]

    if cq["data"] == "mr:no":
        tg.answer_callback(cq["id"], "Skipped")
        tg.edit_caption(chat_id, mid, public + f"\n\n❌ Skipped by you at {_stamp()}")
        return

    age_min = (time.time() - m.get("date", 0)) / 60
    if age_min > config.APPROVAL_TTL_MIN:
        tg.answer_callback(cq["id"], "This radar image is too old to post now.", alert=True)
        tg.edit_caption(chat_id, mid, public + f"\n\n⌛ Expired ({age_min:.0f} min old) – wait for the next update")
        return

    # Remove the buttons first, so a double tap can't post twice.
    tg.answer_callback(cq["id"], "Posting…")
    tg.edit_caption(chat_id, mid, public + "\n\n⏳ Posting…")
    photos = m.get("photo") or []
    if not photos:
        tg.edit_caption(chat_id, mid, public + "\n\n⚠️ No photo found in this message")
        return
    image = tg.download_file(photos[-1]["file_id"])       # largest size
    results = []
    for name, fn in publishers.enabled_targets():
        try:
            fn(image, public)
            results.append(f"✅ {name}")
        except Exception as e:
            results.append(f"⚠️ {name} failed: {str(e)[:120]}")
    tg.edit_caption(chat_id, mid, public + f"\n\n{' | '.join(results)} • {_stamp()}")
    print(f"[approval] {results}")


OFFSET = config.STATE_DIR / "telegram_offset.json"


def process_pending_updates(timeout: int = 0) -> int:
    """Handle button presses that arrived since the last run. Returns the
    number of updates processed."""
    if not config.TELEGRAM_BOT_TOKEN:
        return 0
    try:
        offset = json.loads(OFFSET.read_text()).get("offset")
    except Exception:
        offset = None
    try:
        updates = tg.get_updates(offset, timeout=timeout)
    except tg.TelegramError as e:
        print(f"[approval] getUpdates failed: {e}")
        return 0
    for u in updates:
        try:
            handle_update(u)
        except Exception as e:
            print(f"[approval] update {u.get('update_id')} failed: {e}")
        offset = u["update_id"] + 1
        OFFSET.parent.mkdir(parents=True, exist_ok=True)
        OFFSET.write_text(json.dumps({"offset": offset}))   # saved per update: never handle one twice
    return len(updates)
