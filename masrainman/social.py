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

from . import config, publishers, promoter
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


def _forward(st: dict, cards: list[tuple[bytes, str]]) -> None:
    """Send all of this cycle's region cards to TELEGRAM_FORWARD_CHAT_ID as ONE album,
    at most once every TELEGRAM_FORWARD_EVERY_MIN minutes (one notification for followers).

    Runs are every ~10 min with some GitHub delay, so allow 5 min of slack.
    Never raises: a problem here must not disturb the main channel/approvals.
    """
    if not (config.TELEGRAM_FORWARD_CHAT_ID and cards):
        return
    try:
        now = datetime.now(timezone.utc)
        prev = st.get("forward_last_album_utc")
        if prev:
            age_min = (now - datetime.fromisoformat(prev)).total_seconds() / 60
            if age_min < max(config.TELEGRAM_FORWARD_EVERY_MIN - 5, 1):
                print(f"[social] forward album skipped (last one {age_min:.0f} min ago)")
                return
        msgs = tg.send_album(config.TELEGRAM_FORWARD_CHAT_ID, cards)
        st["forward_last_album_utc"] = now.isoformat(timespec="seconds")
        st.pop("forward_last_utc", None)
        print(f"[social] album of {len(msgs)} card(s) sent to forward channel")
    except Exception as e:
        print(f"[social] forward album to second channel failed: {e}")


def wa_share_footer() -> str:
    return ("\n\n🔔 இந்த South India Radar Nowcast Alerts சேனலை உங்கள் குடும்பம், நண்பர்கள் மற்றும் ஊர் "
            "குழுக்களுக்கு பகிருங்கள் — மழை நெருங்கும்போது தானியங்கி ரேடார் எச்சரிக்கை இலவசமாக கிடைக்கும். "
            "Share this channel with your family, friends and local groups so everyone gets free automatic "
            f"radar rain alerts. Follow 👉 {config.WA_CHANNEL_LINK}\n"
            "🌧️ உங்கள் ஊரில் மழையா? ரியாக்ட் செய்யுங்கள் · React 🌧️ if it's raining at your place, "
            "and forward this card to your family group.")


# ------------------------------------------------------ promotion agent --
PROMO_TEXT = [
    # rotated so the main channel doesn't see the same post every time
    ("🛰️ மழை உங்கள் ஊருக்கு வருகிறதா? நேரடி ரேடார் நவ்காஸ்ட் எச்சரிக்கைகள் இப்போது தனி சேனலில்!",
     "Is rain heading to your town? Live radar nowcast alerts now have their own channel."),
    ("⛈️ ஒவ்வொரு மணி நேரமும் தென்னிந்திய ரேடார் படங்கள் + கனமழை உடனடி எச்சரிக்கை — இலவசம்.",
     "Hourly South India radar cards + instant heavy-rain alerts, free."),
    ("☔ குடை எடுக்கணுமா? அடுத்த 60 நிமிட மழை நிலவரம் மாவட்ட வாரியாக — Radar Nowcast Alerts சேனலில்.",
     "Need an umbrella? District-wise rain for the next 60 minutes, on the Radar Nowcast Alerts channel."),
]


def _promo_caption(st: dict, peak: bool) -> str:
    i = int(st.get("wa_promo_rot", 0)) % len(PROMO_TEXT)
    st["wa_promo_rot"] = i + 1
    ta, en = PROMO_TEXT[i]
    lead = ("🚨 இப்போது கனமழை பதிவாகிறது! / Heavy rain on radar right now!\n\n" if peak else "")
    return (f"{lead}{ta}\n{en}\n\n"
            f"👉 Join *{config.WA_CHANNEL_NAME}*: {config.WA_CHANNEL_LINK}\n\n"
            "இந்த முதன்மை சேனலில் வழக்கமான வானிலை அப்டேட்கள் மட்டும் தொடரும். "
            "This main channel keeps the regular MasRainman updates — radar alerts live in the new channel. "
            "Please share 🙏")


def _promote(st: dict, cards: list[tuple[bytes, str]], peak: bool) -> None:
    """Promotion agent. Queues ONE teaser (strongest card + join link) for the main
    MasRainman WhatsApp channel, and once a day a join line in the Telegram channel.

    Guardrails (fans complained about flooding, and WhatsApp bans spammy numbers):
      * only the two configured channels -- never groups, chats or individuals
      * at most one post per IST slot, one extra in a peak event, cap per day,
        and a minimum gap between posts
    Never raises."""
    if not config.WA_PROMO_AUTO:
        return
    try:
        now = datetime.now(timezone.utc)
        ist = now.astimezone(IST)
        day = ist.date().isoformat()
        if st.get("wa_promo_day") != day:
            st["wa_promo_day"], st["wa_promo_count"], st["wa_promo_slots"] = day, 0, []
        prev = st.get("wa_promo_last_utc")
        gap_ok = not prev or (now - datetime.fromisoformat(prev)).total_seconds() / 3600 >= config.WA_PROMO_GAP_H
        slot = next((h for h in config.WA_PROMO_SLOTS_IST if h <= ist.hour < h + 2), None)
        due_slot = slot is not None and slot not in st["wa_promo_slots"]
        due_peak = peak and not st.get("wa_promo_peak_done")
        if gap_ok and st["wa_promo_count"] < config.WA_PROMO_MAX_PER_DAY and (due_slot or due_peak):
            q = config.WA_PROMO_QUEUE_DIR
            q.mkdir(parents=True, exist_ok=True)
            for old in q.iterdir():
                old.unlink()
            if cards:                                        # dry day -> text-only teaser
                (q / "00.jpg").write_bytes(max(cards, key=lambda c: len(c[0]))[0])
            (q / "00.txt").write_text(_promo_caption(st, peak), encoding="utf-8")
            st["wa_promo_count"] += 1
            st["wa_promo_last_utc"] = now.isoformat(timespec="seconds")
            if due_slot:
                st["wa_promo_slots"].append(slot)
            else:
                st["wa_promo_peak_done"] = True
            promoter.log("WhatsApp main channel", "join teaser" + (" (heavy rain)" if not due_slot else ""))
            print(f"[promo] main-channel teaser queued ({'peak' if not due_slot else f'slot {slot}:00 IST'}, "
                  f"{st['wa_promo_count']}/{config.WA_PROMO_MAX_PER_DAY} today)")
        if not peak:
            st.pop("wa_promo_peak_done", None)       # re-arm for the next event

        # Facebook Page: a join post every Sunday evening, plus one on a heavy-rain day
        week = f"{ist.isocalendar()[0]}-W{ist.isocalendar()[1]}"
        fb_weekly = ist.weekday() == 6 and 18 <= ist.hour < 20 and st.get("fb_promo_week") != week
        fb_peak = peak and st.get("fb_promo_peak_day") != day
        if cards and (fb_weekly or fb_peak) and publishers.facebook_enabled():
            try:
                publishers.post_facebook(max(cards, key=lambda c: len(c[0]))[0],
                                         _promo_caption(st, peak).replace("*", "")
                                         + "\n\n#RainAlert #ChennaiRains #TamilNaduWeather #KeralaRains #BengaluruRains #MasRainman")
                st["fb_promo_week" if fb_weekly else "fb_promo_peak_day"] = week if fb_weekly else day
                promoter.log("Facebook Page", "join post" + (" (heavy rain)" if not fb_weekly else ""))
                print("[promo] Facebook Page join post published")
            except Exception as e:
                promoter.log("Facebook Page", "join post", False, str(e))
                print(f"[promo] Facebook join post failed: {e}")

        # Instagram: QR "scan to follow" poster every Wednesday evening
        if ist.weekday() == 2 and 19 <= ist.hour < 21 and st.get("ig_promo_week") != week and promoter.instagram_enabled():
            try:
                promoter.post_instagram(_promo_caption(st, False).replace("*", "")
                                        + "\n\n📲 Scan the QR or tap the link in our bio.\n" + promoter.TAGS)
                st["ig_promo_week"] = week
                promoter.log("Instagram", "QR poster post")
                print("[promo] Instagram QR poster published")
            except Exception as e:
                st["ig_promo_week"] = week          # don't retry every 10 min
                promoter.log("Instagram", "QR poster post", False, str(e))
                print(f"[promo] Instagram post failed: {e}")

        # X (Twitter): one post a day at 6 PM IST, or earlier the first time heavy rain shows up
        if promoter.x_enabled() and st.get("x_promo_day") != day and (ist.hour == 18 or peak):
            try:
                promoter.post_x(promoter.x_text(peak))
                promoter.log("X (Twitter)", "join post" + (" (heavy rain)" if peak else ""))
                print("[promo] X post published")
            except Exception as e:
                promoter.log("X (Twitter)", "join post", False, str(e))
                print(f"[promo] X post failed: {e}")
            st["x_promo_day"] = day

        if (config.TELEGRAM_CHANNEL_ID and ist.hour == config.TG_PROMO_HOUR_IST
                and st.get("tg_promo_day") != day):
            tg.send_message(config.TELEGRAM_CHANNEL_ID,
                            f"📲 WhatsApp-இல் ரேடார் எச்சரிக்கை வேண்டுமா? Get these radar alerts on WhatsApp too:\n"
                            f"{config.WA_CHANNEL_NAME} 👉 {config.WA_CHANNEL_LINK}")
            st["tg_promo_day"] = day
            promoter.log("Telegram channel", "daily join line")
            print("[promo] daily join line posted in the Telegram channel")
    except Exception as e:
        print(f"[promo] skipped: {e}")


def _whatsapp_hourly(st: dict, cards: list[tuple[bytes, str]], peak_dbz: float = 0.0) -> None:
    """Every WA_CHANNEL_EVERY_MIN minutes, post this cycle's region cards to the
    WhatsApp Channel with the share-the-channel footer. Uses Whapi if its
    secrets are set; otherwise queues the cards for the wa_channel/ (Baileys)
    workflow step. Never raises: must not disturb Telegram posting."""
    if not (config.WA_CHANNEL_AUTO and cards):
        return
    try:
        now = datetime.now(timezone.utc)
        peak = peak_dbz >= config.WA_CHANNEL_PEAK_DBZ
        every = config.WA_CHANNEL_PEAK_EVERY_MIN if peak else config.WA_CHANNEL_EVERY_MIN
        prev = st.get("wa_channel_last_utc")
        if prev:
            age_min = (now - datetime.fromisoformat(prev)).total_seconds() / 60
            if age_min < max(every - 5, 1):
                print(f"[social] WhatsApp channel skipped (last post {age_min:.0f} min ago, "
                      f"{'peak' if peak else 'normal'} mode = every {every} min, peak {peak_dbz:.0f} dBZ)")
                return
        print(f"[social] WhatsApp channel: {'PEAK' if peak else 'normal'} mode (peak {peak_dbz:.0f} dBZ, every {every} min)")
        footer = wa_share_footer()
        if publishers.whatsapp_enabled():
            ok = 0
            for image, caption in cards:
                try:
                    publishers.post_whatsapp(image, caption + footer)
                    ok += 1
                except Exception as e:
                    print(f"[social] WhatsApp channel (Whapi) post failed: {e}")
            if ok:
                st["wa_channel_last_utc"] = now.isoformat(timespec="seconds")
            print(f"[social] WhatsApp channel: {ok}/{len(cards)} card(s) posted via Whapi")
            return
        q = config.WA_QUEUE_DIR
        q.mkdir(parents=True, exist_ok=True)
        for old in q.iterdir():
            old.unlink()
        for i, (image, caption) in enumerate(cards):
            (q / f"{i:02d}.jpg").write_bytes(image)
            (q / f"{i:02d}.txt").write_text(caption + footer, encoding="utf-8")
        st["wa_channel_last_utc"] = now.isoformat(timespec="seconds")
        print(f"[social] WhatsApp channel: {len(cards)} card(s) queued in {q}")
    except Exception as e:
        print(f"[social] WhatsApp channel hourly post failed: {e}")


# ------------------------------------------------------------------ cycle --
def publish_cycle(items: list[tuple[str, "Path", str]], doc: dict) -> None:
    """items: (key, card path, caption, audience) for every region with rain.
    audience "all" = everywhere; "external" = the earlier 1.6 deg region card,
    approval (-> Facebook / WhatsApp) + WhatsApp hourly only; "telegram" =
    long-range overview + close-up cards, Telegram channel + forward channel only;
    "bot" = district-wise + LIVE cards, admin's private bot chat only."""
    if not config.TELEGRAM_BOT_TOKEN:
        print("[social] TELEGRAM_BOT_TOKEN not set -- skipping all posting")
        return
    st = _load()
    sig = _radar_signature(doc)
    if sig and sig == st.get("last_signature"):
        print("[social] no new radar frame since last post -- nothing to send")
        _marketing(st, [], doc, posted=False)
        return
    if not items:
        print("[social] no region has rain worth posting this cycle")
        st["last_signature"] = sig
        _marketing(st, [], doc, posted=False)
        return

    channel_ids = st.setdefault("channel_message_ids", {})
    approval_ids = st.setdefault("approval_message_ids", {})
    targets = " + ".join(n for n, _ in publishers.enabled_targets())
    forward_cards: list[tuple[bytes, str]] = []     # Telegram forward channel: wide + close-ups
    wide_cards: list[tuple[bytes, str]] = []        # WhatsApp hourly: wide only
    for item in items:
        key, path, caption = item[:3]
        audience = item[3] if len(item) > 3 else "all"
        image = path.read_bytes()
        # 0) "bot" cards (district-wise + LIVE, 10 Nov 2026): ONLY the admin's
        #    private bot chat -- no channel, forward album, approval, WhatsApp.
        if audience == "bot":
            if config.TELEGRAM_ADMIN_CHAT_ID:
                try:
                    m = tg.send_photo(config.TELEGRAM_ADMIN_CHAT_ID, image, caption)
                    print(f"[social] {key}: bot-only card {m['message_id']} sent to admin chat")
                except Exception as e:
                    print(f"[social] {key}: bot-only card failed: {e}")
            continue
        # 1) automatic Telegram channel post (one live post per region in "edit" mode)
        if config.TELEGRAM_CHANNEL_ID and audience != "external":
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

        if audience != "external":
            forward_cards.append((image, caption))
        if audience == "telegram":
            continue                       # close-ups stay on Telegram
        wide_cards.append((image, caption))

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

    # 3) one album for the second channel (every TELEGRAM_FORWARD_EVERY_MIN)
    _forward(st, forward_cards)

    # 4) automatic hourly WhatsApp Channel post (with share-the-channel footer)
    _whatsapp_hourly(st, wide_cards, float(doc.get("peak_dbz") or 0))

    # 5) promotion agent
    st["last_signature"] = sig
    _marketing(st, wide_cards, doc, posted=bool(wide_cards))


def _marketing(st: dict, wide_cards: list[tuple[bytes, str]], doc: dict, posted: bool) -> None:
    """Promotion agent, run every cycle (rain or not). Never raises."""
    try:
        peak = float(doc.get("peak_dbz") or 0) >= config.WA_CHANNEL_PEAK_DBZ
        promoter.keep_alive(st, posted)
        if promoter.branding_done():
            _promote(st, wide_cards, peak)
        else:
            print("[promo] waiting: alerts channel logo/description not applied yet "
                  "(add the bot's number as channel admin) -- marketing starts after that")
        promoter.weekly_report(st)
    except Exception as e:
        print(f"[promo] agent error: {e}")
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
