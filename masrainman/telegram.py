"""Tiny Telegram Bot API client (plain requests, no extra dependency).

The bot token is part of every request URL, so an error message straight
from `requests` would print it into the (public) Actions log. Every error
leaving this module goes through _redact() first.

Flood control: calls are spaced at least MIN_GAP_S apart, and a 429
"Too Many Requests" is waited out (up to MAX_WAIT_S) instead of retried
straight away -- hammering Telegram while it is throttling is what gets a
bot restricted.
"""
from __future__ import annotations

import json
import re
import time

import requests

from . import config


MIN_GAP_S = 1.1          # Telegram allows ~1 message/s per chat and 20/min per group or channel
MAX_WAIT_S = 60          # longest 429 retry_after we sit out; anything longer fails this call
ATTEMPTS = 3
_TOKEN_RE = re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}")
_last_call = 0.0


class TelegramError(RuntimeError):
    pass


def _redact(text) -> str:
    s = str(text)
    if config.TELEGRAM_BOT_TOKEN:
        s = s.replace(config.TELEGRAM_BOT_TOKEN, "<token>")
    return _TOKEN_RE.sub("<token>", s)


def _url(method: str) -> str:
    return f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}"


def _space_out():
    global _last_call
    wait = _last_call + MIN_GAP_S - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def call(method: str, data: dict | None = None, files: dict | None = None, timeout: int = 60) -> dict | list | bool:
    payload = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
               for k, v in (data or {}).items() if v is not None}
    for attempt in range(1, ATTEMPTS + 1):
        _space_out()
        try:
            r = requests.post(_url(method), data=payload, files=files, timeout=timeout)
        except requests.RequestException as e:
            # "from None": the chained traceback would show the URL, i.e. the token
            raise TelegramError(f"{method}: network error: {_redact(e)}") from None
        try:
            body = r.json()
        except ValueError:
            body = {}
        if body.get("ok"):
            return body["result"]
        desc = _redact(body.get("description") or f"HTTP {r.status_code}")
        if r.status_code == 429:
            wait = int((body.get("parameters") or {}).get("retry_after") or 5)
            if attempt < ATTEMPTS and wait <= MAX_WAIT_S:
                print(f"[telegram] {method}: flood limit, waiting {wait}s as Telegram asks")
                time.sleep(wait + 1)
                continue
            raise TelegramError(f"{method}: flood limit, Telegram asks to wait {wait}s -- not retrying")
        if r.status_code >= 500 and attempt < ATTEMPTS and not method.startswith("send"):   # a re-send could arrive twice
            time.sleep(3 * attempt)
            continue
        if r.status_code == 401:
            desc += " (bot token rejected: check the TELEGRAM_BOT_TOKEN secret)"
        raise TelegramError(f"{method}: {desc}")
    raise TelegramError(f"{method}: gave up after {ATTEMPTS} tries")


def send_photo(chat_id, photo: bytes | str, caption: str = "", reply_markup: dict | None = None) -> dict:
    if isinstance(photo, bytes):
        return call("sendPhoto", {"chat_id": chat_id, "caption": caption, "reply_markup": reply_markup},
                    files={"photo": ("radar.jpg", photo, "image/jpeg")})
    return call("sendPhoto", {"chat_id": chat_id, "photo": photo, "caption": caption, "reply_markup": reply_markup})


def send_album(chat_id, photos: list[tuple[bytes, str]]) -> list[dict]:
    """One album (2-10 photos, each with its own caption). A single photo is sent normally."""
    photos = photos[:10]
    if len(photos) == 1:
        return [send_photo(chat_id, photos[0][0], photos[0][1])]
    media, files = [], {}
    for i, (img, cap) in enumerate(photos):
        files[f"p{i}"] = (f"radar{i}.jpg", img, "image/jpeg")
        media.append({"type": "photo", "media": f"attach://p{i}", "caption": cap})
    return call("sendMediaGroup", {"chat_id": chat_id, "media": media}, files=files, timeout=120)


def edit_photo(chat_id, message_id: int, photo: bytes, caption: str = "") -> dict:
    media = {"type": "photo", "media": "attach://photo", "caption": caption}
    return call("editMessageMedia", {"chat_id": chat_id, "message_id": message_id, "media": media},
                files={"photo": ("radar.jpg", photo, "image/jpeg")})


def edit_caption(chat_id, message_id: int, caption: str, reply_markup: dict | None = None):
    return call("editMessageCaption", {"chat_id": chat_id, "message_id": message_id, "caption": caption,
                                       "reply_markup": reply_markup or {"inline_keyboard": []}})


def remove_buttons(chat_id, message_id: int):
    return call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                           "reply_markup": {"inline_keyboard": []}})


def send_message(chat_id, text: str):
    return call("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True})


def answer_callback(callback_id: str, text: str = "", alert: bool = False):
    try:
        call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text, "show_alert": alert}, timeout=15)
    except TelegramError as e:      # "query is too old" when handled minutes later by the scheduled run
        print(f"[telegram] answerCallbackQuery: {e}")


def get_updates(offset: int | None, timeout: int = 0) -> list[dict]:
    return call("getUpdates", {"offset": offset, "timeout": timeout,
                               "allowed_updates": ["callback_query", "message"]}, timeout=timeout + 30)


def download_file(file_id: str) -> bytes:
    info = call("getFile", {"file_id": file_id})
    url = f"https://api.telegram.org/file/bot{config.TELEGRAM_BOT_TOKEN}/{info['file_path']}"
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
    except requests.RequestException as e:
        raise TelegramError(f"download_file: {_redact(e)}") from None
    return r.content
