"""Tiny Telegram Bot API client (plain requests, no extra dependency)."""
from __future__ import annotations

import json

import requests

from . import config


class TelegramError(RuntimeError):
    pass


def _url(method: str) -> str:
    return f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}"


def call(method: str, data: dict | None = None, files: dict | None = None, timeout: int = 60) -> dict | list | bool:
    payload = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
               for k, v in (data or {}).items() if v is not None}
    r = requests.post(_url(method), data=payload, files=files, timeout=timeout)
    try:
        body = r.json()
    except ValueError:
        raise TelegramError(f"{method}: HTTP {r.status_code}")
    if not body.get("ok"):
        raise TelegramError(f"{method}: {body.get('description', r.status_code)}")
    return body["result"]


def send_photo(chat_id, photo: bytes | str, caption: str = "", reply_markup: dict | None = None) -> dict:
    if isinstance(photo, bytes):
        return call("sendPhoto", {"chat_id": chat_id, "caption": caption, "reply_markup": reply_markup},
                    files={"photo": ("radar.jpg", photo, "image/jpeg")})
    return call("sendPhoto", {"chat_id": chat_id, "photo": photo, "caption": caption, "reply_markup": reply_markup})


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
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content
