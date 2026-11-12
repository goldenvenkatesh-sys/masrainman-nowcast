#!/usr/bin/env python3
"""MAS Rainman Friend - free, Claude-free watch-and-grow bot for masrainman.blogspot.com.

Runs on GitHub Actions every 2 hours:
  1. Security watch: site up, new unknown scripts/iframes, obfuscated code, hidden spam links,
     noindex/canonical/robots.txt changes, spam text in posts, Google Safe Browsing status.
  2. Growth: SEO pack for each new post (title, search description, permalink, labels, alt text,
     share captions EN + Tamil with hashtags), Google Trends India scan for weather topics,
     daily SEO summary.
  3. Reports to the owner's private Telegram bot chat. Alerts at once; quiet runs stay silent.
Never edits the blog and never posts publicly.
"""
import datetime as dt
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urlparse, urljoin

import requests

BLOG = "https://masrainman.blogspot.com"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
STATE_FILE = os.environ.get("FRIEND_STATE", "state.json")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "")
FORCE_REPORT = os.environ.get("FORCE_REPORT", "") == "1"
UA = {"User-Agent": "Mozilla/5.0 (MAS Rainman Friend site monitor; owner's own blog)"}

# Domains a Blogger weather blog is expected to load or link to.
SAFE_DOMAINS = [
    "blogger.com", "blogblog.com", "blogspot.com", "googleusercontent.com", "google.com",
    "gstatic.com", "googleapis.com", "googletagmanager.com", "google-analytics.com",
    "googlesyndication.com", "doubleclick.net", "youtube.com", "youtu.be", "ytimg.com",
    "pages.dev", "github.io", "githubusercontent.com", "github.com", "cdnjs.cloudflare.com",
    "jsdelivr.net", "unpkg.com", "fonts.googleapis.com", "facebook.com", "fb.com", "x.com",
    "twitter.com", "instagram.com", "t.me", "telegram.me", "whatsapp.com", "wa.me",
    "imd.gov.in", "mausam.imd.gov.in", "rsmcnewdelhi.imd.gov.in", "windy.com", "rainviewer.com",
    "openstreetmap.org", "tile.openstreetmap.org", "leafletjs.com", "carto.com", "basemaps.cartocdn.com",
    "ecmwf.int", "noaa.gov", "tropicaltidbits.com", "zoom.earth", "earth.nullschool.net",
]
SPAM_WORDS = ["casino", "viagra", "cialis", "poker", "slot gacor", "slot online", "judi", "togel",
              "betting", "porn", "xxx", "escort", "payday loan", "crack download", "keygen",
              "replica watches", "bitcoin doubler", "essay writing service"]
OBFUSCATION = [
    (r"eval\s*\(\s*atob\s*\(", "eval(atob(...)) – hidden code"),
    (r"eval\s*\(\s*unescape\s*\(", "eval(unescape(...)) – hidden code"),
    (r"eval\s*\(\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k", "packed eval script"),
    (r"String\.fromCharCode\((\s*\d+\s*,){40,}", "long fromCharCode string"),
    (r"coinhive|cryptonight|coin-hive|webminer|minero\.cc|cryptoloot", "crypto-miner"),
    (r"window\.location(\.href)?\s*=\s*['\"]https?://(?!masrainman)", "forced redirect"),
    (r"[A-Za-z0-9+/]{1500,}={0,2}", "very long base64 blob"),
]

TN_PLACES = ["Chennai", "Tiruvallur", "Kancheepuram", "Chengalpattu", "Vellore", "Ranipet",
             "Tirupattur", "Krishnagiri", "Dharmapuri", "Salem", "Namakkal", "Erode", "Tiruppur",
             "Coimbatore", "Nilgiris", "Ooty", "Karur", "Tiruchirappalli", "Trichy", "Perambalur",
             "Ariyalur", "Cuddalore", "Villupuram", "Kallakurichi", "Tiruvannamalai",
             "Kanchipuram", "Nagapattinam", "Mayiladuthurai", "Tiruvarur", "Thanjavur",
             "Pudukkottai", "Sivaganga", "Ramanathapuram", "Virudhunagar", "Madurai", "Theni",
             "Dindigul", "Thoothukudi", "Tuticorin", "Tirunelveli", "Tenkasi", "Kanyakumari",
             "Puducherry", "Pondicherry", "Karaikal", "Delta",
             "Kerala", "Thiruvananthapuram", "Trivandrum", "Kochi", "Ernakulam", "Idukki",
             "Kozhikode", "Kannur", "Wayanad", "Palakkad", "Thrissur", "Kollam", "Alappuzha",
             "Bengaluru", "Bangalore", "Karnataka", "Mangaluru", "Andhra", "Nellore", "Tirupati",
             "Telangana", "Hyderabad", "Sri Lanka"]
SYSTEMS = [  # (regex, plain english, label, hashtags)
    (r"cyclon|புயல்", "Cyclone", "Cyclone", ["#Cyclone", "#BayOfBengal"]),
    (r"deep depression|\bdd\b|depression|தாழ்வு மண்டல", "Depression in Bay of Bengal", "Low Pressure", ["#BayOfBengal"]),
    (r"low pressure|\blpa\b|\blow\b|காற்றழுத்த", "Low Pressure in Bay of Bengal", "Low Pressure", ["#LowPressure", "#BayOfBengal"]),
    (r"\buac\b|circulation|சுழற்சி", "Upper Air Circulation", "Low Pressure", []),
    (r"thunder|இடி", "Thunderstorms", "Thunderstorms", ["#Thunderstorm"]),
    (r"northeast monsoon|\bnem\b|வடகிழக்கு", "Northeast Monsoon", "NEM 2026", ["#NEM2026", "#NortheastMonsoon"]),
    (r"southwest monsoon|\bswm\b|தென்மேற்கு", "Southwest Monsoon", "Southwest Monsoon", ["#Monsoon"]),
    (r"heat ?wave|வெப்ப அலை", "Heatwave", "Heatwave", ["#Heatwave"]),
    (r"\bmm\b|rainfall data|station", "Rainfall Data", "Rainfall Data", []),
]
TREND_WORDS = ["rain", "mazhai", "மழை", "cyclone", "புயல்", "weather", "imd", "storm", "flood",
               "school leave", "school holiday", "holiday", "leave today", "red alert", "orange alert",
               "chennai", "kerala", "tamil nadu", "bay of bengal", "low pressure", "monsoon", "depression"]


def now_ist():
    return dt.datetime.now(IST)


def get(url, **kw):
    kw.setdefault("timeout", 30)
    kw.setdefault("headers", UA)
    last = None
    for _ in range(3):
        try:
            return requests.get(url, **kw)
        except requests.RequestException as e:
            last = e
            time.sleep(5)
    raise last


def domain_of(url):
    try:
        return urlparse(url).netloc.lower().split(":")[0]
    except Exception:
        return ""


def is_safe(d):
    return any(d == s or d.endswith("." + s) for s in SAFE_DOMAINS)


class PageScan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts, self.iframes, self.links, self.imgs = [], [], [], []
        self.canonical = self.robots = self.description = ""
        self.title, self._in_title = "", False
        self.hidden_links = []
        self._hidden_depth = 0
        self._stack = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        style = (a.get("style") or "").replace(" ", "").lower()
        hidden = any(x in style for x in ["display:none", "visibility:hidden", "font-size:0",
                                           "font-size:1px", "left:-9999", "height:0;overflow:hidden"])
        if tag not in ("br", "img", "meta", "link", "input", "hr", "source"):
            self._stack.append(hidden)
            if hidden:
                self._hidden_depth += 1
        if tag == "script" and a.get("src"):
            self.scripts.append(a["src"])
        elif tag == "iframe" and a.get("src"):
            self.iframes.append(a["src"])
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
            if self._hidden_depth > 0 or hidden:
                self.hidden_links.append(a["href"])
        elif tag == "img":
            self.imgs.append((a.get("src", ""), a.get("alt")))
        elif tag == "link" and (a.get("rel") or "").lower() == "canonical":
            self.canonical = a.get("href", "")
        elif tag == "meta":
            n = (a.get("name") or "").lower()
            if n == "robots":
                self.robots = a.get("content", "")
            elif n == "description":
                self.description = a.get("content", "")
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag not in ("br", "img", "meta", "link", "input", "hr", "source") and self._stack:
            if self._stack.pop():
                self._hidden_depth -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def scan_html(text, base):
    p = PageScan()
    try:
        p.feed(text)
    except Exception:
        pass
    p.scripts = [urljoin(base, s) for s in p.scripts]
    p.iframes = [urljoin(base, s) for s in p.iframes]
    return p


def security_findings(text, scan):
    out = []
    low = text.lower()
    for pat, label in OBFUSCATION:
        m = re.search(pat, text, flags=re.I)
        if m:
            snippet = text[max(0, m.start() - 40): m.start() + 80].replace("\n", " ")
            # base64 blobs inside data:image are normal images
            if label.startswith("very long base64") and "data:image" in text[max(0, m.start() - 40): m.start()]:
                continue
            out.append(f"{label}: …{html.escape(snippet[:120])}…")
    for h in scan.hidden_links:
        d = domain_of(h)
        if d and not is_safe(d):
            out.append(f"hidden link to {d} ({h[:80]})")
    for w in SPAM_WORDS:
        if re.search(r"\b" + re.escape(w) + r"\b", low):
            out.append(f"spam word on page: '{w}'")
    if "noindex" in (scan.robots or "").lower():
        out.append(f"robots meta says '{scan.robots}' – Google will drop the page")
    return out


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def tg_send(text):
    print("----- TELEGRAM -----\n" + text)
    if not (TG_TOKEN and TG_CHAT):
        print("(no Telegram secrets; printed only)")
        return
    chats = [c.strip() for c in TG_CHAT.split(",") if c.strip()]
    for chat in chats[:1]:  # owner's private admin chat only
        for i in range(0, len(text), 3800):
            part = text[i:i + 3800]
            r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                              data={"chat_id": chat, "text": part, "parse_mode": "HTML",
                                    "disable_web_page_preview": "true"}, timeout=30)
            if not r.ok:
                print("telegram error", r.status_code, r.text[:200])
                requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                              data={"chat_id": chat, "text": re.sub(r"<[^>]+>", "", part)}, timeout=30)


# ---------------------------------------------------------------- feed / posts
def fetch_posts(n=10):
    r = get(f"{BLOG}/feeds/posts/default?alt=json&max-results={n}")
    r.raise_for_status()
    feed = r.json().get("feed", {})
    posts = []
    for e in feed.get("entry", []):
        link = next((l["href"] for l in e.get("link", []) if l.get("rel") == "alternate"), "")
        posts.append({
            "id": e.get("id", {}).get("$t", ""),
            "title": e.get("title", {}).get("$t", ""),
            "url": link,
            "published": e.get("published", {}).get("$t", ""),
            "updated": e.get("updated", {}).get("$t", ""),
            "labels": [c.get("term", "") for c in e.get("category", [])],
            "content": (e.get("content") or e.get("summary") or {}).get("$t", ""),
        })
    return posts


def plain(htmltext):
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", htmltext, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def detect(text):
    places = []
    for p in TN_PLACES:
        if re.search(r"\b" + re.escape(p) + r"\b", text, flags=re.I) and p not in places:
            places.append(p)
    found = []
    for s in SYSTEMS:
        m = re.search(s[0], text, flags=re.I)
        if m:
            found.append((m.start(), s))
    systems = [s for _, s in sorted(found, key=lambda x: x[0])]
    return places, systems


def is_caps(t):
    letters = [c for c in t if c.isalpha() and c.isascii()]
    return len(letters) > 8 and sum(c.isupper() for c in letters) / len(letters) > 0.7


def seo_pack(post, trends):
    pub = post["published"][:10]
    try:
        d = dt.date.fromisoformat(pub)
    except Exception:
        d = now_ist().date()
    date_s = f"{d.day} {d.strftime('%b')} {d.year}"
    text = plain(post["content"])
    places, systems = detect(post["title"] + " " + text)
    tn_places = [p for p in places if p not in ("Kerala", "Karnataka", "Andhra", "Telangana", "Sri Lanka", "Delta")]
    main_sys = systems[0][1] if systems else "Rain Forecast"
    lead = "Chennai Rain" if "Chennai" in places[:3] else "Tamil Nadu Rain Today"
    if "Kerala" in places and "Chennai" not in places and not tn_places:
        lead = "Kerala Rain Today"
    others = [p for p in tn_places if not (lead.startswith("Chennai") and p == "Chennai")]
    key = ", ".join(others[:2])

    def fit(t, n=70):
        return t if len(t) <= n else t[:n - 1].rsplit(" ", 1)[0].rstrip(",–| ")
    t1 = fit(f"{lead} {date_s}: {main_sys}" + (f" – {key}" if key else ""))
    t2 = fit(f"{main_sys} | {', '.join(places[:3]) or 'Tamil Nadu'} Rain Forecast {date_s}")
    ta_sys = {"Cyclone": "புயல்", "Depression in Bay of Bengal": "வங்கக்கடலில் தாழ்வு மண்டலம்",
              "Low Pressure in Bay of Bengal": "வங்கக்கடலில் காற்றழுத்த தாழ்வு பகுதி",
              "Upper Air Circulation": "வளிமண்டல சுழற்சி", "Thunderstorms": "இடியுடன் மழை",
              "Northeast Monsoon": "வடகிழக்கு பருவமழை", "Southwest Monsoon": "தென்மேற்கு பருவமழை",
              "Heatwave": "வெப்ப அலை"}.get(main_sys, "மழை வாய்ப்பு")
    ta_title = f"{'சென்னை' if lead.startswith('Chennai') else 'தமிழ்நாடு'} மழை இன்று {d.day:02d}.{d.month:02d}.{d.year} – {ta_sys}"

    first = re.split(r"(?<=[.!?])\s", text)
    desc = ""
    for s in first:
        if len(desc) + len(s) > 150:
            break
        desc = (desc + " " + s).strip()
    if len(desc) < 90:
        desc = f"{lead} {date_s}: {main_sys}" + (f" – rain chances for {', '.join(places[:4])}" if places else "") + ". MASRainman daily inference."
    desc = desc[:155]
    slug = re.sub(r"[^a-z0-9]+", "-", f"{lead} {d.day} {d.strftime('%b')} {d.year}".lower()).strip("-")

    labels = []
    if tn_places:
        labels.append("Tamil Nadu Rain")
    if "Chennai" in places:
        labels.append("Chennai Rain")
    if "Kerala" in places or any(p in places for p in ["Kochi", "Idukki", "Trivandrum", "Thiruvananthapuram"]):
        labels.append("Kerala Rain")
    if any(p in places for p in ["Karnataka", "Bengaluru", "Bangalore", "Mangaluru"]):
        labels.append("Karnataka Rain")
    if any(p in places for p in ["Andhra", "Telangana", "Nellore", "Tirupati", "Hyderabad"]):
        labels.append("Andhra Telangana Rain")
    for s in systems:
        if s[2] not in labels:
            labels.append(s[2])
    labels = labels[:5] or ["Tamil Nadu Rain"]

    tags = ["#TamilNaduRain", "#TNWeather"]
    if "Chennai" in places:
        tags.insert(0, "#ChennaiRains")
    for s in systems:
        tags += s[3]
    if "Kerala" in places:
        tags.append("#KeralaRain")
    for t in trends:  # a trending weather term gets a hashtag
        h = "#" + re.sub(r"[^A-Za-z0-9]", "", t.title())
        if 4 < len(h) < 25:
            tags.append(h)
    seen, hashtags = set(), []
    for t in tags:
        if t.lower() not in seen:
            seen.add(t.lower()); hashtags.append(t)
    hashtags = hashtags[:5]

    # page-level checks on the live post
    issues, alts = [], []
    try:
        pr = get(post["url"])
        sc = scan_html(pr.text, post["url"])
        if not sc.description or sc.description.strip() == "":
            issues.append("No search description set (Blogger: post → Settings → Search description)")
        no_alt = [s for s, a in sc.imgs if (a is None or not a.strip()) and ("blogger" in s or "googleusercontent" in s or "bp.blogspot" in s)]
        for i, s in enumerate(no_alt[:6], 1):
            alts.append(f"Image {i}: \"{main_sys} {('– ' + ', '.join(places[:3])) if places else ''} {date_s} – MASRainman\"")
        if no_alt:
            issues.append(f"{len(no_alt)} image(s) without alt text")
    except Exception as e:
        issues.append(f"could not open post page ({e.__class__.__name__})")
    if is_caps(post["title"]):
        issues.append("Title is in ALL CAPS – Google shows normal case better")
    if len(post["title"]) > 75:
        issues.append(f"Title is {len(post['title'])} chars – Google cuts after ~60")
    if not post["labels"]:
        issues.append("No labels on the post")
    elif len(post["labels"]) > 6:
        issues.append(f"{len(post['labels'])} labels – keep 3–5 from the clean set")
    words = len(text.split())
    if words < 150:
        issues.append(f"Only ~{words} words of text – add 2–3 sentences for Google (pictures are not read)")

    url = post["url"]
    cap_en = f"{lead} {date_s}: {main_sys}" + (f" – {', '.join(places[:4])}" if places else "") + f". Full update 👉 {url}\n{' '.join(hashtags)}"
    cap_ta = f"இன்றைய வானிலை ({d.day:02d}.{d.month:02d}) – முழு விவரம் 👉 {url}\n{' '.join(hashtags[:3])}"
    world = f"{'Chennai' if 'Chennai' in places else 'Tamil Nadu'} weather this week: {main_sys.lower()} – latest rain outlook for South India ({date_s})."

    e = html.escape
    lines = [f"📝 <b>SEO pack – new post</b>\n<a href=\"{e(url)}\">{e(post['title'][:90])}</a>",
             f"\n<b>Fix first:</b>\n" + ("\n".join("• " + e(i) for i in issues) if issues else "• Nothing urgent ✓"),
             f"\n<b>SEO title</b> ({len(t1)} chars):\n<code>{e(t1)}</code>\nAlt: <code>{e(t2)}</code>\nTamil: <code>{e(ta_title)}</code>",
             f"\n<b>Search description</b> ({len(desc)} chars):\n<code>{e(desc)}</code>",
             f"\n<b>Permalink:</b> <code>{e(slug)}</code>",
             f"\n<b>Labels:</b> {e(', '.join(labels))}"]
    if alts:
        lines.append("\n<b>Alt text:</b>\n" + "\n".join(e(a) for a in alts))
    lines += [f"\n<b>Share – English</b> (WhatsApp/Telegram/FB/X):\n<code>{e(cap_en)}</code>",
              f"\n<b>Share – Tamil:</b>\n<code>{e(cap_ta)}</code>",
              f"\n<b>World reach line</b> (add to post's first paragraph):\n<code>{e(world)}</code>"]
    return "\n".join(lines), issues


# ---------------------------------------------------------------- trends / safe browsing
def google_trends_in():
    hits = []
    try:
        r = get("https://trends.google.com/trending/rss?geo=IN")
        root = ET.fromstring(r.content)
        for item in root.iter("item"):
            t = (item.findtext("title") or "").strip()
            traffic = ""
            for ch in item:
                if ch.tag.endswith("approx_traffic"):
                    traffic = ch.text or ""
            if any(w in t.lower() for w in TREND_WORDS):
                hits.append((t, traffic))
    except Exception as e:
        print("trends failed:", e)
        return None
    return hits


SB_TEXT = {1: "no unsafe content", 2: "SITE MARKED UNSAFE", 3: "some pages marked unsafe",
           4: "check a specific URL", 5: "uncommon downloads", 6: "no data"}


def _sb(site):
    r = get("https://transparencyreport.google.com/transparencyreport/api/v3/safebrowsing/status?site=" + site)
    data = json.loads(r.text.split("\n", 1)[-1])
    return data[0][1]


def safe_browsing():
    """Returns (text, alert?). blogspot.com is shared by millions of blogs, so Google's status
    for a *.blogspot.com name can reflect other people's blogs. A control blog tells us that."""
    try:
        code = _sb("masrainman.blogspot.com")
    except Exception as e:
        return f"not checked ({e.__class__.__name__})", False
    if code in (1, 6):
        return f"OK – {SB_TEXT[code]}", False
    try:
        ctrl = _sb("googleblog.blogspot.com")
    except Exception:
        ctrl = None
    print("safe browsing codes: masrainman", code, "control", ctrl)
    if ctrl == code:
        return f"OK for your blog (code {code} '{SB_TEXT.get(code, '?')}' is shown for the shared blogspot.com domain too)", False
    return f"code {code} – {SB_TEXT.get(code, 'unknown')} (control blog: {ctrl})", True


# ---------------------------------------------------------------- main
def main():
    st = load_state()
    t = now_ist()
    st.setdefault("alerts_sent", [])
    st.setdefault("packs_sent", [])
    st.setdefault("runs", [])
    first_run = "baseline" not in st
    alerts, notes = [], []

    # 1. homepage
    up, home = False, ""
    try:
        r = get(BLOG + "/")
        up = r.status_code == 200
        home = r.text
        final = r.url
        if domain_of(final) not in ("masrainman.blogspot.com", "www.masrainman.blogspot.com"):
            alerts.append(f"Homepage redirects to {final}")
        if not up:
            alerts.append(f"Homepage returned HTTP {r.status_code}")
    except Exception as e:
        alerts.append(f"Blog not reachable ({e.__class__.__name__})")

    scan = scan_html(home, BLOG) if home else None
    if scan:
        ext = sorted({domain_of(u) for u in scan.scripts + scan.iframes if domain_of(u)})
        base = st.get("baseline", {})
        if first_run:
            st["baseline"] = {"ext": ext, "canonical": scan.canonical, "title": scan.title.strip(),
                              "robots_meta": scan.robots, "made": t.isoformat()}
            unknown = [d for d in ext if not is_safe(d)]
            if unknown:
                notes.append("Scripts/iframes from domains not on my safe list (please confirm they are yours): " + ", ".join(unknown))
        else:
            new = [d for d in ext if d not in base.get("ext", []) and not is_safe(d)]
            for d in new:
                alerts.append(f"NEW script/iframe source on homepage: {d}")
            new_safe = [d for d in ext if d not in base.get("ext", []) and is_safe(d)]
            if new_safe:
                st["baseline"]["ext"] = sorted(set(base.get("ext", [])) | set(new_safe))
            if base.get("canonical") and scan.canonical and scan.canonical != base["canonical"]:
                alerts.append(f"Canonical changed: {base['canonical']} → {scan.canonical}")
        for f in security_findings(home, scan):
            (notes if first_run else alerts).append("Homepage: " + f)

    # robots.txt (Blogger custom robots.txt can be abused)
    try:
        rr = get(BLOG + "/robots.txt")
        if rr.status_code != 200 or "<html" in rr.text[:300].lower():
            raise ValueError(f"robots.txt HTTP {rr.status_code}")  # Blogger sometimes 404s it briefly
        rb = rr.text.strip()
        if "Disallow: /\n" in rb + "\n" and "User-agent: *" in rb and "Disallow: /search" not in rb:
            alerts.append("robots.txt blocks the whole blog from Google")
        if st.get("robots_txt") is not None and rb != st["robots_txt"]:
            alerts.append("robots.txt changed:\n" + rb[:400])
        st["robots_txt"] = rb
    except Exception as e:
        print(f"robots.txt not checked ({e})")

    sb_text, sb_alert = safe_browsing()
    if sb_alert:
        alerts.append(f"Google Safe Browsing: {sb_text} – check https://transparencyreport.google.com/safe-browsing/search?url=masrainman.blogspot.com")

    # 2. posts
    posts = []
    try:
        posts = fetch_posts(10)
    except Exception as e:
        alerts.append(f"Post feed not readable ({e.__class__.__name__})")
    for p in posts[:5]:
        low = (p["title"] + " " + plain(p["content"])).lower()
        bad = [w for w in SPAM_WORDS if re.search(r"\b" + re.escape(w) + r"\b", low)]
        if bad:
            alerts.append(f"Spam words {bad} in post: {p['url']}")
        sc = scan_html(p["content"], p["url"])
        for h in sc.links + sc.scripts + sc.iframes:
            d = domain_of(h)
            if d and any(w.replace(" ", "") in d for w in ["casino", "viagra", "porn", "betting", "slot", "loan", "togel"]):
                alerts.append(f"Spam link {d} in post: {p['url']}")

    trends = google_trends_in()
    trend_terms = [x[0] for x in (trends or [])]

    packs = []
    if first_run:
        st["packs_sent"] = [p["url"] for p in posts[1:]]  # only the newest post gets a pack now
    for p in posts[:3]:
        if p["url"] and p["url"] not in st["packs_sent"]:
            try:
                pack, _ = seo_pack(p, trend_terms[:2])
                packs.append(pack)
            except Exception as e:
                notes.append(f"SEO pack failed for {p['url']} ({e})")
            st["packs_sent"].append(p["url"])
    st["packs_sent"] = st["packs_sent"][-60:]

    # hot topics not covered in last 24 h of posts
    hot = []
    if trends:
        recent = " ".join((p["title"] + " " + plain(p["content"])[:1500]).lower() for p in posts[:3])
        for term, traffic in trends[:6]:
            key = term.lower().split()[0]
            if key not in recent:
                hot.append((term, traffic))

    # 3. alerts (only new ones)
    new_alerts = [a for a in alerts if a not in st["alerts_sent"]]
    st["alerts_sent"] = (st["alerts_sent"] + new_alerts)[-50:]
    e = html.escape
    if new_alerts:
        msg = ["⚠️ <b>MAS Rainman Friend – security alert</b> " + t.strftime("%d %b %H:%M IST")]
        msg += ["• " + e(a) for a in new_alerts]
        msg.append("\n<b>What to do:</b> Blogger → Theme → Edit HTML / Layout: remove any gadget or code you did not add. "
                   "Change your Google password, turn on 2-Step Verification, and check Settings → Permissions for unknown authors. "
                   "Search Console → Security issues shows Google's own findings.")
        tg_send("\n".join(msg))

    if first_run:
        msg = ["🤝 <b>MAS Rainman Friend is now watching your blog</b> (" + t.strftime("%d %b %H:%M IST") + ")",
               f"Site: {'UP ✓' if up else 'DOWN ✗'} · Safe Browsing: {e(sb_text)}",
               f"Scripts/iframes loaded from: {e(', '.join(st.get('baseline', {}).get('ext', [])) or 'none')}",
               f"Newest posts read: {len(posts)}"]
        if notes:
            msg.append("\n<b>Please check:</b>\n" + "\n".join("• " + e(n) for n in notes))
        msg.append("\nI run every 2 hours on GitHub, free. You get a message only for an alert, a new-post SEO pack, a hot weather trend, or the 9 pm daily summary.")
        tg_send("\n".join(msg))

    for pk in packs:
        tg_send(pk)

    if hot and not first_run:
        k = "hot:" + ",".join(x[0] for x in hot[:3])
        if k not in st.get("hot_sent", []):
            st["hot_sent"] = (st.get("hot_sent", []) + [k])[-30:]
            lines = ["🔥 <b>Trending in India now</b> – not covered in your latest posts:"]
            for term, traffic in hot[:4]:
                lines.append(f"• {e(term)} ({e(traffic)} searches)")
            lines.append("A quick post or update with that wording in the title can catch this search traffic.")
            tg_send("\n".join(lines))

    # daily summary after 9 pm IST
    today = t.date().isoformat()
    if (t.hour >= 21 and st.get("last_daily") != today) or FORCE_REPORT:
        st["last_daily"] = today
        todo = []
        for p in posts[:7]:
            if is_caps(p["title"]):
                todo.append(f"ALL-CAPS title: {p['title'][:60]}")
            if not p["labels"]:
                todo.append(f"No labels: {p['title'][:60]}")
            if len(p["labels"]) > 6:
                todo.append(f"{len(p['labels'])} labels (keep 3–5): {p['title'][:50]}")
        runs_today = [r for r in st["runs"] if r.get("t", "").startswith(today)]
        down = sum(1 for r in runs_today if not r.get("up"))
        lines = ["📊 <b>MAS Rainman Friend – daily summary</b> " + t.strftime("%d %b"),
                 f"Checks today: {len(runs_today) + 1} · site down: {down} · new alerts: {sum(r.get('alerts', 0) for r in runs_today) + len(new_alerts)}",
                 f"Safe Browsing: {e(sb_text)}",
                 f"Trending weather searches: {e(', '.join(trend_terms[:5]) or 'none right now')}"]
        if todo:
            lines.append("\n<b>Fix this week:</b>\n" + "\n".join("• " + e(x) for x in todo[:5]))
        lines.append("\n<b>Daily habit:</b> post the inference with a clear English title (\"Tamil Nadu Rain Today …\"), "
                     "fill the search description, share the post link (not just the image) on WhatsApp, Telegram, FB and X.")
        tg_send("\n".join(lines))

    st["runs"] = (st["runs"] + [{"t": t.isoformat(timespec="minutes"), "up": up,
                                 "alerts": len(new_alerts), "packs": len(packs)}])[-24:]
    st["last_run"] = t.isoformat(timespec="minutes")
    save_state(st)
    print(f"done: up={up} alerts={len(new_alerts)} packs={len(packs)} hot={len(hot)} first={first_run}")


if __name__ == "__main__":
    main()
