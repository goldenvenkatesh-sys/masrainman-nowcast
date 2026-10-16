"""Where an approved post goes: Facebook Page and WhatsApp Channel.

Each publisher takes (jpeg bytes, caption) and returns a short id string,
or raises with a readable message. A target whose secrets are not set is
simply not offered.
"""
from __future__ import annotations

import base64

import requests

from . import config


def facebook_enabled() -> bool:
    return bool(config.FB_PAGE_ID and config.FB_PAGE_TOKEN)


def whatsapp_enabled() -> bool:
    return bool(config.WHAPI_TOKEN and config.WHAPI_CHANNEL_ID)


def post_facebook(image: bytes, caption: str) -> str:
    """Graph API: upload a photo to the Page feed (needs a PAGE access token
    with pages_manage_posts + pages_read_engagement)."""
    url = f"https://graph.facebook.com/{config.FB_GRAPH_VERSION}/{config.FB_PAGE_ID}/photos"
    r = requests.post(url, data={"caption": caption, "published": "true",
                                 "access_token": config.FB_PAGE_TOKEN},
                      files={"source": ("radar.jpg", image, "image/jpeg")}, timeout=90)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or "error" in body:
        msg = (body.get("error") or {}).get("message") or r.text[:200]
        raise RuntimeError(f"Facebook: {msg}")
    return body.get("post_id") or body.get("id", "")


def post_whatsapp(image: bytes, caption: str) -> str:
    """WhatsApp Channels have no official Meta API, so this goes through the
    Whapi.Cloud gateway (a WhatsApp number linked to Whapi that is an ADMIN
    of your channel). Channel id looks like 120363xxxxxxxx@newsletter."""
    media = "data:image/jpeg;name=radar.jpg;base64," + base64.b64encode(image).decode()
    r = requests.post(f"{config.WHAPI_BASE_URL.rstrip('/')}/messages/image",
                      headers={"Authorization": f"Bearer {config.WHAPI_TOKEN}",
                               "Accept": "application/json"},
                      json={"to": config.WHAPI_CHANNEL_ID, "media": media, "caption": caption},
                      timeout=90)
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 300 or body.get("error") or body.get("sent") is False:
        raise RuntimeError(f"WhatsApp: {body.get('error') or r.text[:200]}")
    return str((body.get("message") or {}).get("id", "sent"))


TARGETS = [
    ("Facebook", facebook_enabled, post_facebook),
    ("WhatsApp", whatsapp_enabled, post_whatsapp),
]


def enabled_targets() -> list[tuple[str, callable]]:
    return [(name, fn) for name, ok, fn in TARGETS if ok()]
