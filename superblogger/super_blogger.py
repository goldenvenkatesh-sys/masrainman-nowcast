#!/usr/bin/env python3
"""Super Blogger -- the MasRainman blog agent (created 13 Nov 2026).

Runs on its own on GitHub Actions every 2 hours (.github/workflows/super-blogger.yml);
no Claude, no PC needed. Target: at least 5,000 blog views a day.

Every run:
  1. SECURITY + TRENDS -- the MAS Rainman Friend watch (merged in): site up, unknown
     scripts, spam, robots.txt, Safe Browsing, Google Trends India weather terms.
  2. SEO EDITS -- for every new (or newly updated) post, edits the live post through
     the Blogger API: English+Tamil search intro paragraph, image alt text, labels,
     ALL-CAPS / Tamil-only title fix, a related-links + "join radar alerts" block.
     Never touches the forecast text or numbers. The original is kept for undo.
  3. TRAFFIC -- shares each new post once: Facebook (both Pages), Instagram,
     Telegram channel, WhatsApp main channel (via the radar bot's sender), and a
     one-tap X post in the admin chat. Pings Google (sitemap) and Bing (if keyed).
     When the day is behind the 5k pace by evening, re-shares the latest post once.
  4. PERFORMANCE -- Blogger page-view counter every run (views today vs 5,000),
     Google Analytics 4 (sources, countries, live users) and Search Console
     (queries, countries, low-CTR opportunities) when connected.
  5. REPORT -- short status to the admin's private Telegram chat each daytime run,
     full daily report after 9 PM IST.

Google access (one-time): GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN
secrets with Blogger + Search Console + Analytics read scopes (see
superblogger/google_auth.py). Without them it still guards the blog, shares new
posts and sends the SEO pack -- but cannot edit posts or count views.

Undo an edit: run the workflow manually with "undo" = the post URL.
"""
from __future__ import annotations

import base64
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "friend"))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SUPER_BLOGGER", "1")

import masrainman_friend as friend          # noqa: E402  (security watch + SEO knowledge)

BLOG = "https://masrainman.blogspot.com"
SITE = BLOG + "/"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
TARGET = int(os.environ.get("SB_TARGET_VIEWS", "5000"))
STATE_FILE = Path(os.environ.get("SB_STATE", HERE / "sb_state.json"))
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_ADMIN = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "")
TG_CHANNEL = os.environ.get("TELEGRAM_CHANNEL_ID", "")
WA_ALERT_LINK = os.environ.get("WA_ALERT_CHANNEL_LINK", "https://whatsapp.com/channel/0029VbE2C5F9RZAanCxEFv0o")
BING_KEY = os.environ.get("BING_API_KEY", "")
GH_TOKEN = os.environ.get("GH_TOKEN", "")
GH_REPO = os.environ.get("GITHUB_REPOSITORY", "goldenvenkatesh-sys/masrainman-nowcast")
UNDO = os.environ.get("SB_UNDO", "").strip()
FORCE_REPORT = os.environ.get("FORCE_REPORT", "") == "1"
DRY = os.environ.get("SB_DRY_RUN", "") == "1"
# Morning quick check (every 20 min, 5:30-11:10 AM IST): the owner posts ONE inference
# every morning; polish it for SEO and start promoting as soon as it's up. Skips the
# security watch / performance / reports (those run every 2 h) to stay light.
QUICK = os.environ.get("SB_QUICK", "") == "1"
SETTLE_MIN = 10        # wait this long after publishing, so the owner's own last fixes are in
SHARE_MAX_AGE_H = 18          # only posts newer than this are shared as "new"
E = html.escape


def now_ist():
    return dt.datetime.now(IST)


def log(msg):
    print(f"[super-blogger] {msg}", flush=True)


# ------------------------------------------------------------------ state / telegram
def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(st):
    if DRY:                      # a test run must not mark posts as edited/shared
        log("dry run: state not saved")
        return
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=1))


def tg(method, **data):
    if not TG_TOKEN:
        return None
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/{method}", json=data, timeout=40)
        j = r.json()
        if not j.get("ok"):
            log(f"telegram {method} failed: {j.get('description')}")
        return j.get("result")
    except Exception as e:
        log(f"telegram {method} error: {e}")


def tg_admin(text, **extra):
    if TG_ADMIN:
        for i in range(0, len(text), 3900):           # Telegram limit 4096
            tg("sendMessage", chat_id=TG_ADMIN, text=text[i:i + 3900], parse_mode="HTML",
               disable_web_page_preview=True, **(extra if i + 3900 >= len(text) else {}))


# ------------------------------------------------------------------ Google
class Google:
    """OAuth (refresh token) access to Blogger, Search Console and GA4."""

    def __init__(self):
        self.cid = os.environ.get("GOOGLE_CLIENT_ID", "")
        self.secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
        self.refresh = os.environ.get("GOOGLE_REFRESH_TOKEN", "")
        self.token = None
        self.problem = None if self.ok else "Google sign-in not set up yet"

    @property
    def ok(self):
        return bool(self.cid and self.secret and self.refresh)

    def access(self):
        if self.token:
            return self.token
        r = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id": self.cid, "client_secret": self.secret, "refresh_token": self.refresh,
            "grant_type": "refresh_token"}, timeout=30).json()
        if "access_token" not in r:
            self.problem = f"Google sign-in failed: {r.get('error_description') or r.get('error')}"
            raise RuntimeError(self.problem)
        self.token = r["access_token"]
        return self.token

    def req(self, method, url, **kw):
        h = kw.pop("headers", {})
        h["Authorization"] = "Bearer " + self.access()
        r = requests.request(method, url, headers=h, timeout=60, **kw)
        if r.status_code >= 300:
            try:
                msg = r.json().get("error", {}).get("message", r.text[:200])
            except Exception:
                msg = r.text[:200]
            raise RuntimeError(f"{r.status_code} {msg}")
        return r.json() if r.content else {}


BLOGGER = "https://www.googleapis.com/blogger/v3"


def blog_id(g, st):
    if st.get("blog_id"):
        return st["blog_id"]
    st["blog_id"] = g.req("GET", f"{BLOGGER}/blogs/byurl", params={"url": SITE})["id"]
    return st["blog_id"]


def blog_pages(g, bid, st):
    """Static pages (Rain Alarm, Forecast Models, Talk to MasRainman) for internal links; cached a day."""
    c = st.get("pages_cache", {})
    if c.get("day") == now_ist().date().isoformat():
        return c.get("pages", [])
    pages = []
    try:
        for p in g.req("GET", f"{BLOGGER}/blogs/{bid}/pages", params={"fetchBodies": "false"}).get("items", []):
            pages.append({"title": p.get("title", ""), "url": p.get("url", "")})
    except Exception as e:
        log(f"pages list failed: {e}")
    st["pages_cache"] = {"day": now_ist().date().isoformat(), "pages": pages}
    return pages


# ------------------------------------------------------------------ SEO edits
SB_BLOCK = re.compile(r"<!--SB:(intro|links)-->.*?<!--/SB:\1-->\s*", re.S)


def _title_fix(title, seo):
    """Only two safe title changes: ALL CAPS -> normal case; Tamil-only title gets the English SEO title added."""
    if friend.is_caps(title):
        return re.sub(r"[A-Za-z][A-Za-z']*", lambda m: m.group(0).capitalize() if len(m.group(0)) > 3 or m.start() == 0
                      else m.group(0).lower(), title.lower()), "ALL-CAPS title changed to normal case"
    if not re.search(r"[A-Za-z]{3,}", title) and len(title) + len(seo["title"]) + 3 <= 110:
        return f"{title} | {seo['title']}", "English search title added to the Tamil title"
    return title, None


def _alt_fix(content, alt):
    n = 0

    def add(m):
        nonlocal n
        tag = m.group(0)
        if re.search(r'\balt\s*=\s*"[^"]+"', tag) or re.search(r"\balt\s*=\s*'[^']+'", tag):
            return tag
        n += 1
        tag = re.sub(r'\salt\s*=\s*("")?(\'\')?', " ", tag)
        return tag[:-1].rstrip("/ ") + f' alt="{E(alt)} ({n})"' + (" />" if tag.endswith("/>") else ">")
    return re.sub(r"<img\b[^>]*>", add, content, flags=re.I), n


def _links_block(posts, url, pages):
    def find(*words):
        for p in pages:
            t = p["title"].lower()
            if any(w in t for w in words):
                return p
    items = []
    for emoji, label, words in (("🔔", "Rain Alarm", ("rain alarm",)), ("🗺️", "Forecast Models", ("forecast model", "model")),
                                ("🎙️", "Talk to MasRainman", ("talk",))):
        p = find(*words)
        if p and p.get("url"):
            items.append(f'{emoji} <a href="{E(p["url"])}">{E(label)}</a>')
    prev = [p for p in posts if p["url"] and p["url"] != url][:3]
    prev_html = " · ".join(f'<a href="{E(p["url"])}">{E(p["title"][:70])}</a>' for p in prev)
    return ('<!--SB:links--><div class="sb-links" style="margin:18px 0;padding:12px 14px;border-radius:12px;'
            'background:#eef6ff;border:1px solid #cfe3ff;font-size:15px;line-height:1.6">'
            f'<p style="margin:0 0 6px"><b>📲 நேரடி ரேடார் மழை எச்சரிக்கை / Live radar rain alerts on WhatsApp:</b> '
            f'<a href="{E(WA_ALERT_LINK)}">Join South India Radar Nowcast Alerts (free)</a></p>'
            + (f'<p style="margin:0 0 6px">{" · ".join(items)}</p>' if items else "")
            + (f'<p style="margin:0"><b>Previous updates:</b> {prev_html}</p>' if prev_html else "")
            + "</div><!--/SB:links-->")


def optimize(g, bid, post, posts, seo, pages):
    """Edit the live post. Returns (changes, backup) -- changes empty if nothing to do."""
    path = urllib.parse.urlparse(post["url"]).path
    p = g.req("GET", f"{BLOGGER}/blogs/{bid}/posts/bypath", params={"path": path, "view": "ADMIN"})
    backup = {"id": p["id"], "title": p.get("title", ""), "content": p.get("content", ""), "labels": p.get("labels", [])}
    content = SB_BLOCK.sub("", p.get("content", ""))
    changes = []

    intro = (f'<!--SB:intro--><p class="sb-intro" style="font-size:15px;color:#334"><b>{E(seo["lead"])} – {E(seo["date_s"])}:</b> '
             f'{E(seo["world"])} {E(seo["description"]) if seo["description"] not in seo["world"] else ""}</p><!--/SB:intro-->')
    content = intro + content
    changes.append("search intro paragraph (English summary for Google & world readers)")
    content, n_alt = _alt_fix(content, seo["alt"])
    if n_alt:
        changes.append(f"alt text on {n_alt} image(s)")
    content = content.rstrip() + _links_block(posts, post["url"], pages)
    changes.append("related links + join-radar-alerts block")

    labels = list(p.get("labels", []))
    add = [l for l in seo["labels"] if l.lower() not in {x.lower() for x in labels}][:max(0, 6 - len(labels))]
    if add:
        labels += add
        changes.append("labels added: " + ", ".join(add))
    title, why = _title_fix(p.get("title", ""), seo)
    if why:
        changes.append(why)

    body = {"content": content, "labels": labels, "title": title}
    if DRY:
        log(f"DRY RUN: would patch {post['url']}: {changes}")
        return changes, backup
    res = g.req("PATCH", f"{BLOGGER}/blogs/{bid}/posts/{p['id']}", json=body)
    backup["after_updated"] = res.get("updated", "")
    post["title"] = title                    # shares use the polished title
    return changes, backup


def undo(g, st, url):
    rec = st.get("optimized", {}).get(url, {})
    b = rec.get("backup")
    if not b:
        return f"No saved original for {url}"
    bid = blog_id(g, st)
    g.req("PATCH", f"{BLOGGER}/blogs/{bid}/posts/{b['id']}", json={"title": b["title"], "content": b["content"],
                                                                    "labels": b["labels"]})
    rec["undone"] = now_ist().isoformat(timespec="minutes")
    rec["skip"] = True                       # don't re-optimize it
    return f"Restored the original post: {url}"


# ------------------------------------------------------------------ traffic
def jpeg_url(src):
    """Blogger/googleusercontent image as a JPEG of ~1080 px (Instagram wants JPEG)."""
    if "googleusercontent.com" not in src and "bp.blogspot.com" not in src:
        return src
    if re.search(r"/s\d+(-[a-z0-9-]+)?/", src):
        return re.sub(r"/s\d+(-[a-z0-9-]+)?/", "/s1080-rj/", src)
    if re.search(r"=[swh]\d+[^/]*$", src):
        return re.sub(r"=[swh]\d+[^/]*$", "=s1080-rj", src)
    return src + "=s1080-rj"


def first_image(content):
    m = re.search(r'<img\b[^>]*\bsrc\s*=\s*["\']([^"\']+)', content or "", re.I)
    return m.group(1) if m else None


def gh_put(path, data: bytes, message):
    """Write a file to the repo's main branch (WhatsApp hand-off to the radar bot)."""
    if not GH_TOKEN:
        raise RuntimeError("no GH_TOKEN")
    api = f"https://api.github.com/repos/{GH_REPO}/contents/{path}"
    h = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}
    sha = requests.get(api, headers=h, params={"ref": "main"}, timeout=30).json().get("sha")
    body = {"message": message, "branch": "main", "content": base64.b64encode(data).decode()}
    if sha:
        body["sha"] = sha
    r = requests.put(api, headers=h, json=body, timeout=60)
    if r.status_code >= 300:
        raise RuntimeError(f"GitHub {r.status_code}: {r.text[:150]}")


def share(post, seo, st, boost=False):
    """Share a post to all our outlets. Returns list of (outlet, ok, detail)."""
    from masrainman import promoter, publishers
    url = post["url"]
    img = first_image(post["content"])
    img_j = jpeg_url(img) if img else None
    tags = " ".join(seo["hashtags"])
    head = "🔁 இன்றைய வானிலை / Today's forecast" if boost else "🆕 புதிய வானிலை பதிவு / New weather update"
    text = (f"{head}\n\n<b>{E(post['title'][:150])}</b>\n\n{E(seo['world'])}\n\n👉 Full forecast: {url}\n\n"
            f"📲 Live radar rain alerts: {WA_ALERT_LINK}\n{tags}")
    plain = re.sub(r"</?b>", "", html.unescape(text))
    out = []

    def attempt(name, fn):
        if DRY:
            out.append((name, True, "dry run")); return
        try:
            out.append((name, True, str(fn())[:60]))
        except Exception as e:
            out.append((name, False, str(e)[:120]))

    # Facebook: link post on both Pages (link preview brings the clicks)
    def fb():
        pages = publishers._fb_pages()
        if not pages:
            raise RuntimeError("Facebook not connected")
        ids = []
        for pid, token, _ in pages:
            r = requests.post(f"https://graph.facebook.com/v21.0/{pid}/feed",
                              data={"message": plain, "link": url, "access_token": token}, timeout=60).json()
            if "error" in r:
                raise RuntimeError(r["error"].get("message"))
            ids.append(r.get("id"))
        return ",".join(ids)
    attempt("Facebook (both Pages)", fb)

    if TG_CHANNEL:
        def tgc():
            if img_j:
                r = tg("sendPhoto", chat_id=TG_CHANNEL, photo=img_j, caption=text[:1020], parse_mode="HTML")
            else:
                r = tg("sendMessage", chat_id=TG_CHANNEL, text=text, parse_mode="HTML")
            if not r:
                raise RuntimeError("Telegram refused")
            return r.get("message_id")
        attempt("Telegram channel", tgc)

    if not boost:
        if img_j and promoter.instagram_enabled():
            cap = (f"{post['title'][:150]}\n\n{seo['world']}\n\n{seo['cap_ta'].split('👉')[0].strip()}\n\n"
                   f"🔗 Full forecast: link in bio (masrainman.blogspot.com)\n"
                   f"📲 Free live radar rain alerts on WhatsApp — link in bio\n\n{tags} {promoter.TAGS}")
            attempt("Instagram", lambda: promoter.post_instagram(cap[:2150], image_url=img_j))

        def wa():
            data = requests.get(img_j, timeout=60).content if img_j else b""
            txt = (f"🆕 *{post['title'][:150]}*\n\n{seo['world']}\n\n👉 Full forecast: {url}\n\n"
                   f"📲 Live radar rain alerts: {WA_ALERT_LINK}\nPlease share 🙏")
            if data[:3] == b"\xff\xd8\xff":
                gh_put("state/wa_blog_queue/00.jpg", data, "Super Blogger: blog post for WhatsApp main channel")
            gh_put("state/wa_blog_queue/00.txt", txt.encode(), "Super Blogger: blog post for WhatsApp main channel")
            return "queued (posts with the next radar cycle)"
        attempt("WhatsApp main channel", wa)

        xt = f"{post['title'][:150]}\n{url}\n{' '.join(seo['hashtags'][:3])}"
        if len(xt) > 280:
            xt = f"{post['title'][:100]}…\n{url}\n{' '.join(seo['hashtags'][:2])}"
        attempt("X one-tap", lambda: tg("sendMessage", chat_id=TG_ADMIN, disable_web_page_preview=True,
                                         text="🐦 Super Blogger: new blog post ready for X — tap, then Post.\n\n" + xt,
                                         reply_markup={"inline_keyboard": [[{"text": "📤 Post on X",
                                         "url": "https://x.com/intent/post?text=" + urllib.parse.quote(xt)}]]})
                and "sent")
    return out


def ping_search_engines(g, st, url):
    out = []
    if g.ok and not DRY:
        try:
            site = gsc_site(g, st)
            if site:
                g.req("PUT", f"https://www.googleapis.com/webmasters/v3/sites/{urllib.parse.quote(site, safe='')}/sitemaps/"
                      + urllib.parse.quote(SITE + "sitemap.xml", safe=""))
                out.append(("Google sitemap", True, "submitted"))
        except Exception as e:
            out.append(("Google sitemap", False, str(e)[:100]))
    if BING_KEY and not DRY:
        try:
            r = requests.post(f"https://ssl.bing.com/webmaster/api.svc/json/SubmitUrlbatch?apikey={BING_KEY}",
                              json={"siteUrl": SITE, "urlList": [url]}, timeout=30)
            out.append(("Bing", r.status_code == 200, r.text[:80]))
        except Exception as e:
            out.append(("Bing", False, str(e)[:80]))
    return out


# ------------------------------------------------------------------ performance
def gsc_site(g, st):
    if "gsc_site" in st:
        return st["gsc_site"]
    sites = [s["siteUrl"] for s in g.req("GET", "https://www.googleapis.com/webmasters/v3/sites").get("siteEntry", [])
             if s.get("permissionLevel") != "siteUnverifiedUser"]
    st["gsc_site"] = next((s for s in sites if "masrainman.blogspot.com" in s), None)
    return st["gsc_site"]


def views_snapshot(g, bid, st):
    """Blogger's own page-view counter. Returns dict with today, last2h, avg7, avg30."""
    r = g.req("GET", f"{BLOGGER}/blogs/{bid}/pageviews", params=[("range", "all"), ("range", "7DAYS"), ("range", "30DAYS")])
    c = {}
    for x in r.get("counts", []):            # labels vary (7DAYS / SEVEN_DAYS / ...): match loosely
        k = str(x.get("timeRange", "")).upper()
        key = "7" if ("7" in k or "SEVEN" in k) else ("30" if ("30" in k or "THIRTY" in k) else "ALL")
        c[key] = int(x.get("count", 0))
    t = now_ist()
    snaps = st.setdefault("views", [])
    snaps.append({"t": t.isoformat(timespec="minutes"), "all": c.get("ALL", 0)})
    st["views"] = snaps = [s for s in snaps if s["t"] >= (t - dt.timedelta(days=8)).isoformat()]
    midnight = t.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    before = [s for s in snaps if s["t"] < midnight]
    today_base = (before[-1] if before else snaps[0])["all"]
    exact = bool(before)
    prev = snaps[-2]["all"] if len(snaps) > 1 else None
    out = {"today": snaps[-1]["all"] - today_base, "today_exact": exact,
           "last2h": (snaps[-1]["all"] - prev) if prev is not None else None,
           "avg7": c.get("7", 0) / 7, "avg30": c.get("30", 0) / 30,
           "all": snaps[-1]["all"]}
    # remember finished days
    days = st.setdefault("daily_views", {})
    y = (t - dt.timedelta(days=1)).date().isoformat()
    if before and y not in days:
        ymid = (t - dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        yb = [s for s in snaps if s["t"] < ymid]
        if yb:
            days[y] = before[-1]["all"] - yb[-1]["all"]
    st["daily_views"] = dict(sorted(days.items())[-30:])
    return out


def ga4(g, st):
    """GA4: live users, today's views by source and country (if a GA4 property is reachable)."""
    if "ga4_property" not in st:
        st["ga4_property"] = None
        summ = g.req("GET", "https://analyticsadmin.googleapis.com/v1beta/accountSummaries").get("accountSummaries", [])
        props = [p for a in summ for p in a.get("propertySummaries", [])]
        pick = next((p for p in props if "masrainman" in p.get("displayName", "").lower()), props[0] if len(props) == 1 else None)
        if pick:
            st["ga4_property"] = pick["property"]
    prop = st.get("ga4_property")
    if not prop:
        return None
    base = f"https://analyticsdata.googleapis.com/v1beta/{prop}"
    live = g.req("POST", base + ":runRealtimeReport", json={"metrics": [{"name": "activeUsers"}]})
    live_n = int(live.get("rows", [{}])[0].get("metricValues", [{"value": "0"}])[0]["value"]) if live.get("rows") else 0

    def top(dim, n=5):
        r = g.req("POST", base + ":runReport", json={"dateRanges": [{"startDate": "today", "endDate": "today"}],
                  "dimensions": [{"name": dim}], "metrics": [{"name": "screenPageViews"}],
                  "orderBys": [{"metric": {"metricName": "screenPageViews"}, "desc": True}], "limit": n})
        return [(row["dimensionValues"][0]["value"], int(row["metricValues"][0]["value"])) for row in r.get("rows", [])]
    return {"live": live_n, "sources": top("sessionSource"), "countries": top("country"),
            "views": sum(v for _, v in top("date", 1))}


def gsc_report(g, st):
    site = gsc_site(g, st)
    if not site:
        return None
    end = (now_ist() - dt.timedelta(days=2)).date()
    start = end - dt.timedelta(days=6)
    q = lambda dims, n: g.req("POST", f"https://www.googleapis.com/webmasters/v3/sites/{urllib.parse.quote(site, safe='')}/searchAnalytics/query",
                              json={"startDate": str(start), "endDate": str(end), "dimensions": dims, "rowLimit": n}).get("rows", [])
    tot = q([], 1)
    queries = q(["query"], 25)
    countries = q(["country"], 6)
    opp = [r for r in queries if r["impressions"] >= 50 and r["ctr"] < 0.03][:5]
    return {"clicks": int(tot[0]["clicks"]) if tot else 0, "impr": int(tot[0]["impressions"]) if tot else 0,
            "queries": [(r["keys"][0], int(r["clicks"]), int(r["impressions"])) for r in queries[:6]],
            "countries": [(r["keys"][0].upper(), int(r["clicks"])) for r in countries],
            "opp": [(r["keys"][0], int(r["impressions"]), r["position"]) for r in opp], "range": f"{start}–{end}"}


def inspect_url(g, st, url):
    site = gsc_site(g, st)
    if not site:
        return None
    r = g.req("POST", "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect",
              json={"inspectionUrl": url, "siteUrl": site})
    s = r.get("inspectionResult", {}).get("indexStatusResult", {})
    return s.get("coverageState") or s.get("verdict")


# ------------------------------------------------------------------ main
def pace_line(v, t):
    frac = max((t.hour * 60 + t.minute) / 1440, 0.05)
    proj = int(v["today"] / frac)
    dot = "🟢" if proj >= TARGET else ("🟠" if proj >= TARGET * 0.6 else "🔴")
    approx = "" if v["today_exact"] else " (counting since the first check today)"
    return (f"{dot} <b>Views today: {v['today']:,} / {TARGET:,}</b>{approx} · on pace for ~{proj:,}\n"
            + (f"Last 2 h: {v['last2h']:,} · " if v["last2h"] is not None else "")
            + f"7-day avg {int(v['avg7']):,}/day · 30-day avg {int(v['avg30']):,}/day"), proj


def main():
    t = now_ist()
    st = load_state()
    g = Google()
    first = "started" not in st
    st.setdefault("started", t.isoformat(timespec="minutes"))
    st.setdefault("optimized", {})
    st.setdefault("shared", [])
    did, warn = [], []

    posts_peek = None
    if QUICK:
        try:
            posts_peek = friend.fetch_posts(3)
        except Exception as e:
            log(f"quick check: feed not readable ({e})")
            return
        if all(p["url"] in st.get("shared", []) or p["url"] in st.get("optimized", {}) for p in posts_peek):
            log("quick check: no new post")
            return

    # 0) security + trends (MAS Rainman Friend, merged) -- full runs only
    if not QUICK:
      try:
        friend.STATE_FILE = str(HERE / "friend_state.json")
        friend.main()
      except Exception as e:
        warn.append(f"security watch error: {e}")

    if UNDO:
        try:
            msg = undo(g, st, UNDO) if g.ok else "Google sign-in not set up -- nothing to undo"
        except Exception as e:
            msg = f"Undo failed: {e}"
        tg_admin("↩️ <b>Super Blogger</b>: " + E(msg))
        save_state(st)
        return

    posts = friend.fetch_posts(8)
    trends = [] if QUICK else [x[0] for x in (friend.google_trends_in() or [])]
    bid = pages = None
    if g.ok:
        try:
            bid = blog_id(g, st)
            pages = blog_pages(g, bid, st)
        except Exception as e:
            warn.append(str(e))
    if first:   # don't rework or re-share old posts on the very first run: only the newest one
        for p in posts[1:]:
            st["optimized"][p["url"]] = {"t": "before Super Blogger", "skip": True}
            st["shared"].append(p["url"])

    for p in posts[:3]:
        url = p["url"]
        if not url:
            continue
        try:
            pub_age_min = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(
                p["published"].replace("Z", "+00:00"))).total_seconds() / 60
        except Exception:
            pub_age_min = 999
        if pub_age_min < SETTLE_MIN:
            log(f"new post {url} is only {pub_age_min:.0f} min old -- polishing it on the next check")
            continue
        rec = st["optimized"].get(url, {})
        user_edited = rec.get("after_updated") and p["updated"] > rec["after_updated"] and not rec.get("skip")
        try:
            seo = friend.seo_data(p, trends[:2])
        except Exception as e:
            warn.append(f"SEO analysis failed for {url}: {e}")
            continue
        # SEO edits
        # pack_only = seen before Google sign-in was set up -> polish it now that we can
        if (not rec or user_edited or (rec.get("pack_only") and bid)) and not rec.get("skip"):
            if bid:
                try:
                    changes, backup = optimize(g, bid, p, posts, seo, pages or [])
                    st["optimized"][url] = {"t": t.isoformat(timespec="minutes"), "changes": changes,
                                            "after_updated": backup.pop("after_updated", ""), "backup": backup}
                    did.append(f"✏️ SEO-edited <a href=\"{E(url)}\">{E(p['title'][:60])}</a>: " + E("; ".join(changes)))
                except Exception as e:
                    warn.append(f"could not edit {url}: {e}")
            else:
                st["optimized"][url] = {"t": t.isoformat(timespec="minutes"), "pack_only": True}
                tg_admin(seo["pack"] + "\n\n<i>Super Blogger will make these edits itself once Google sign-in is set up.</i>")
                did.append(f"📝 SEO pack sent for {E(p['title'][:60])} (can't edit yet: {E(g.problem or '')})")
        # traffic: share new posts once
        try:
            age_h = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(p["published"].replace("Z", "+00:00"))).total_seconds() / 3600
        except Exception:
            age_h = 0
        if url not in st["shared"] and age_h <= SHARE_MAX_AGE_H:
            res = share(p, seo, st) + ping_search_engines(g, st, url)
            st["shared"].append(url)
            ok = [n for n, o, _ in res if o]
            bad = [f"{n} ({d})" for n, o, d in res if not o]
            did.append(f"📣 Shared {E(p['title'][:50])} → " + E(", ".join(ok) or "nowhere")
                       + (f"\n   ⚠️ failed: {E('; '.join(bad))}" if bad else ""))
            if g.ok:
                try:
                    st.setdefault("inspect", {})[url] = {"t": t.isoformat(timespec="minutes"), "state": inspect_url(g, st, url)}
                except Exception as e:
                    log(f"url inspection: {e}")
    st["shared"] = st["shared"][-80:]
    # keep backups only for the last 15 edited posts
    keep = sorted(st["optimized"], key=lambda u: st["optimized"][u].get("t", ""))[-60:]
    st["optimized"] = {u: st["optimized"][u] for u in keep}
    for u in keep[:-15]:
        st["optimized"][u].pop("backup", None)

    if QUICK:      # morning quick check: tell the owner what was done, nothing else
        if did or warn:
            tg_admin("\n".join([f"🚀 <b>Super Blogger</b> · {t.strftime('%I:%M %p')} · this morning's post"] + did
                                + (["⚠️ " + E(" | ".join(warn))[:600]] if warn else [])))
        for d in did + warn:
            log(re.sub(r"<[^>]+>", "", html.unescape(d)))
        save_state(st)
        return

    # performance
    v = ga = gs = None
    if bid:
        try:
            v = views_snapshot(g, bid, st)
        except Exception as e:
            warn.append(f"view counter: {e}")
        try:
            ga = ga4(g, st)
        except Exception as e:
            log(f"GA4 not available: {e}")
    proj = None
    lines = [f"🚀 <b>Super Blogger</b> · {t.strftime('%d %b %I:%M %p')}"]
    if v:
        pl, proj = pace_line(v, t)
        lines.append(pl)
    else:
        lines.append(f"📉 Views: not measured yet — {E(g.problem or 'Blogger counter unavailable')}")
    if ga:
        lines.append(f"👥 Live now: {ga['live']} · top sources: " + E(", ".join(f"{s} {n}" for s, n in ga["sources"][:4]) or "–")
                     + "\n🌍 Countries: " + E(", ".join(f"{c} {n}" for c, n in ga["countries"][:5]) or "–"))

    # evening boost when behind the 5k pace
    day = t.date().isoformat()
    if proj is not None and proj < TARGET and 17 <= t.hour < 21 and st.get("boost_day") != day and posts:
        latest = posts[0]
        try:
            res = share(latest, friend.seo_data(latest, trends[:2]), st, boost=True)
            st["boost_day"] = day
            did.append("🔁 Behind the 5k pace — re-shared the latest post → "
                       + E(", ".join(n for n, o, _ in res if o) or "nowhere"))
        except Exception as e:
            warn.append(f"boost failed: {e}")

    lines += did
    if warn:
        lines.append("⚠️ " + E(" | ".join(warn))[:600])

    # daily full report after 9 PM
    daily = (t.hour >= 21 and st.get("report_day") != day) or FORCE_REPORT
    if daily:
        st["report_day"] = day
        try:
            gs = gsc_report(g, st) if g.ok else None
        except Exception as e:
            log(f"Search Console not available: {e}")
        rep = [f"📊 <b>Super Blogger – daily report</b> {t.strftime('%d %b')}  (target {TARGET:,} views/day)"]
        if v:
            rep.append(f"Today: <b>{v['today']:,}</b> views · 7-day avg {int(v['avg7']):,}/day · 30-day avg {int(v['avg30']):,}/day")
            hist = st.get("daily_views", {})
            if hist:
                last7 = list(hist.items())[-7:]
                hit = sum(1 for _, n in last7 if n >= TARGET)
                rep.append("Last days: " + ", ".join(f"{d[5:]} {n:,}" for d, n in last7) + f"  ({hit}/{len(last7)} hit 5k)")
        if ga:
            rep.append("Sources today: " + E(", ".join(f"{s} {n}" for s, n in ga["sources"])))
            rep.append("Countries today: " + E(", ".join(f"{c} {n}" for c, n in ga["countries"])))
        if gs:
            rep.append(f"\n<b>Google Search</b> ({gs['range']}): {gs['clicks']:,} clicks · {gs['impr']:,} impressions")
            rep.append("Top searches: " + E(", ".join(f"{q} ({c})" for q, c, _ in gs["queries"])))
            rep.append("Countries: " + E(", ".join(f"{c} {n}" for c, n in gs["countries"])))
            if gs["opp"]:
                rep.append("💡 <b>Seen but not clicked</b> (put these words in a title): "
                           + E(", ".join(f"“{q}” ({i} views, pos {p:.0f})" for q, i, p in gs["opp"])))
        ins = [(u, x["state"]) for u, x in st.get("inspect", {}).items() if x.get("t", "").startswith(day)]
        if ins:
            rep.append("Google index: " + E(", ".join(f"{u.rsplit('/', 1)[-1][:30]}: {s}" for u, s in ins)))
        edited = [u for u, r in st["optimized"].items() if r.get("t", "").startswith(day) and r.get("changes")]
        shared = sum(1 for d in did if d.startswith("📣"))
        rep.append(f"\nToday I edited {len(edited)} post(s) for SEO" + (f", shared {shared} new post(s)" if shared else "") + ".")
        if trends:
            rep.append("Trending weather searches in India: " + E(", ".join(trends[:5])))
        plan = []
        if v and v["today"] < TARGET:
            gap = TARGET - v["today"]
            plan.append(f"{gap:,} views short. Biggest levers: post the inference before 8 AM with an English title; "
                        "a second short post on rain days (\"Chennai rain live – <time>\"); share the post LINK, not just the image.")
        if gs and gs["opp"]:
            plan.append("Write the next title with: " + ", ".join(q for q, _, _ in gs["opp"][:3]))
        if not g.ok:
            plan.append("Finish the one-time Google sign-in so I can edit posts and count views.")
        if plan:
            rep.append("\n<b>Plan for tomorrow:</b>\n" + "\n".join("• " + E(x) for x in plan))
        tg_admin("\n".join(rep))

    # 2-hourly status: daytime only, or any time something happened
    quiet = not (6 <= t.hour < 23)
    if (did or warn or first or not quiet) and not daily:
        if first:
            lines.insert(1, "Hi! I'm <b>Super Blogger</b>, your blog agent. I check masrainman.blogspot.com every 2 hours, "
                            f"edit new posts for SEO, share them everywhere and chase <b>{TARGET:,} views a day</b>. "
                            "MAS Rainman Friend's security watch now runs inside me.")
        tg_admin("\n".join(lines))
    for d in did + warn:
        log(re.sub(r"<[^>]+>", "", html.unescape(d)))
    st["last_run"] = t.isoformat(timespec="minutes")
    save_state(st)
    log(f"done: did={len(did)} warn={len(warn)} views={v and v['today']} google={'ok' if g.ok else 'no'}")


if __name__ == "__main__":
    main()
