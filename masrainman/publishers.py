"""Where an approved post goes: Facebook Page and WhatsApp Channel.

Each publisher takes (jpeg bytes, caption) and returns a short id string,
or raises with a readable message. A target whose secrets are not set is
simply not offered.
"""
from __future__ import annotations

import base64

import requests

from . import config


_fb_cache: list | None = None


def _fb_pages() -> list[tuple[str, str, str]]:
    """[(page_id, page_token, page_name)] to post to. Uses FB_PAGE_ID +
    FB_PAGE_TOKEN when set; otherwise asks Meta for the Page tokens of
    config.FB_PAGE_IDS using the Instagram (user) token. Cached per run."""
    global _fb_cache
    if _fb_cache is not None:
        return _fb_cache
    pages = []
    if config.FB_PAGE_ID and config.FB_PAGE_TOKEN:
        pages = [(config.FB_PAGE_ID, config.FB_PAGE_TOKEN, "Facebook Page")]
    elif config.IG_TOKEN and config.FB_PAGE_IDS:
        try:
            r = requests.get(f"https://graph.facebook.com/{config.FB_GRAPH_VERSION}/me/accounts",
                             params={"fields": "id,name,access_token", "limit": 50,
                                     "access_token": config.IG_TOKEN}, timeout=30).json()
            got = {a["id"]: a for a in r.get("data", []) if a.get("access_token")}
            pages = [(pid, got[pid]["access_token"], got[pid].get("name", pid))
                     for pid in config.FB_PAGE_IDS if pid in got]
            if not pages:
                print(f"[facebook] no Page token available: {(r.get('error') or {}).get('message', 'pages not found')}")
        except Exception as e:
            print(f"[facebook] could not fetch Page tokens: {e}")
            return []                          # retry on the next call
    _fb_cache = pages
    return pages


def facebook_enabled() -> bool:
    return bool(_fb_pages())


def _post_facebook_page(page_id: str, token: str, image: bytes, caption: str) -> str:
    url = f"https://graph.facebook.com/{config.FB_GRAPH_VERSION}/{page_id}/photos"
    r = requests.post(url, data={"caption": caption, "published": "true", "access_token": token},
                      files={"source": ("radar.jpg", image, "image/jpeg")}, timeout=90)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or "error" in body:
        msg = (body.get("error") or {}).get("message") or r.text[:200]
        raise RuntimeError(msg)
    return body.get("post_id") or body.get("id", "")


def post_facebook(image: bytes, caption: str) -> str:
    """Upload a photo post to every configured Facebook Page (Tamil Nadu +
    Kerala pages). Raises only if no Page accepted it."""
    ok, errs = [], []
    for pid, token, name in _fb_pages():
        try:
            ok.append(_post_facebook_page(pid, token, image, caption))
        except Exception as e:
            errs.append(f"{name}: {e}")
    if not ok:
        raise RuntimeError("Facebook: " + ("; ".join(errs) or "no Page configured"))
    if errs:
        print("[facebook] posted to some pages only -- " + "; ".join(errs))
    return ",".join(ok)


def whatsapp_enabled() -> bool:
    return bool(config.WHAPI_TOKEN and config.WHAPI_CHANNEL_ID)


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
