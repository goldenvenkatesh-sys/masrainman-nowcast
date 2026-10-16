# MasRainman — IMD Radar Nowcast Bot

Every 10 minutes this repo pulls the latest IMD Doppler radar images (Chennai NIOT X-band, Chennai DWR, Karaikal, Mangaluru, Trivandrum), tracks rain cells, projects where they are going, and turns it into:

* **regional nowcast cards** for **Tamil Nadu & Puducherry**, **Kerala** and **Karnataka** (1080×1350). Each card zooms in on the area with the most rain and shows a white Leaflet-style map with:
  * coloured blocks for rain on radar now
  * dashed outlines for where it is projected in ~90 min
  * arrowheads for storm motion and dotted radar range rings
  * district, state and coast lines
  * bold town labels (English + Tamil + intensity + arrival time)
  * a neon colour bar with English + Tamil tier names
* a **bilingual nowcast caption** for each card — which places are getting rain *now* and which will get it in the *next 60 minutes*, plus where the rain clouds are moving
* an automatic post to your **Telegram channel**
* a private **Telegram approval card** with **✅ Yes / ❌ No** buttons — tap Yes and the same card + caption is posted to your **Facebook Page** and **WhatsApp Channel**

The radar engine (`nowcast_bot.py`) is forked from [Chennai-Rains/IMD_Radar_Updates](https://github.com/Chennai-Rains/IMD_Radar_Updates); everything in `masrainman/` and `run_cycle.py` is the MasRainman layer on top.

```
IMD radar GIFs ──► nowcast_bot.py ──► output/nowcast_bot.json + grid
                                         │
                       masrainman/alerts.py (Tamil + English text)
                       masrainman/card.py   (neon image)
                                         │
              ┌──────────────────────────┴──────────────────────────┐
     Telegram channel (auto)                       Your Telegram chat: [✅ Yes] [❌ No]
                                                                 │ Yes
                                                Facebook Page + WhatsApp Channel
```

## Files

| Path | What it does |
|---|---|
| `run_cycle.py` | One full cycle (what GitHub Actions runs) |
| `nowcast_bot.py` | Radar engine: fetch, decode, track, export. Colour scale = `DBZ_BAND_COLORS` |
| `masrainman/card.py` | Draws the nowcast card |
| `masrainman/regions.py` | Regions, hashtags and the auto-zoom window |
| `assets/boundaries.json` | District / state / coast lines (rebuild with `tools/build_boundaries.py`) |
| `tiles_cache/` | White basemap tiles, downloaded once and committed |
| `masrainman/alerts.py` | Builds the Tamil + English nowcast caption |
| `masrainman/places.py` | Towns the nowcast names (edit freely) |
| `masrainman/social.py` | Telegram channel post, approval card, Yes/No handling |
| `masrainman/publishers.py` | Facebook Page + WhatsApp Channel posting |
| `listener.py` | Optional: instant approvals (see below) |
| `.github/workflows/masrainman.yml` | The 10-minute schedule |

## Setup (about 30 minutes)

### 1. Create the repo
Create a new GitHub repo (e.g. `masrainman-radar`) and push this folder to its `main` branch. **Make it public**: a 10-minute schedule uses ~13,000 Actions minutes a month, which is free only on public repos (private repos get 2,000).

### 2. Telegram bot
1. In Telegram open **@BotFather** → `/newbot` → copy the token → secret `TELEGRAM_BOT_TOKEN`.
2. Create your channel (e.g. `@masrainman`), add the bot as an **administrator** with "Post messages" → secret `TELEGRAM_CHANNEL_ID` = `@masrainman` (or the `-100…` id for a private channel).
3. Open a private chat with your bot and send `/start`. After the next workflow run the bot replies with your chat id → secret `TELEGRAM_ADMIN_CHAT_ID`. (To let a co-admin approve too, set `TELEGRAM_ADMIN_IDS` = `id1,id2`.)

### 3. Facebook Page
1. At developers.facebook.com create an app (type **Business**), add your Page.
2. In **Graph API Explorer** pick the app, request `pages_manage_posts`, `pages_read_engagement`, `pages_show_list`, generate a user token, then exchange it for a long-lived token and call `GET /me/accounts` — the `access_token` listed for your Page is a **Page token that does not expire**.
3. Secrets: `FB_PAGE_ID` (the Page's numeric id) and `FB_PAGE_TOKEN`.

### 4. WhatsApp Channel
Meta does **not** offer an official API for posting to WhatsApp *Channels* (the WhatsApp Cloud API only messages individuals). This project therefore uses **[Whapi.Cloud](https://whapi.cloud)**, a gateway that links a WhatsApp number by QR code:
1. Sign up at whapi.cloud, create a channel (their word for a connected number), scan the QR with a WhatsApp number that is an **admin of your MasRainman WhatsApp Channel** (a spare number is safest).
2. Copy the API token → secret `WHAPI_TOKEN`.
3. Find your channel id: `GET https://gate.whapi.cloud/newsletters` with that token → something like `120363xxxxxxxxxx@newsletter` → secret `WHAPI_CHANNEL_ID`.

> Whapi is a paid service after its trial, and like every unofficial WhatsApp gateway it operates outside WhatsApp's own API terms, so there is some risk of the linked number being restricted. If you would rather not, leave the two `WHAPI_*` secrets empty — the Yes button then posts to Facebook only, and you can forward the Telegram post to WhatsApp by hand.

### 5. Optional
* `CARTO_API_KEY` (secret) — key from carto.com if their tile server asks for one. Tiles are downloaded once per area and committed to `tiles_cache/`, so traffic stays tiny. If tiles can't be fetched, the card falls back to a washed-out copy of the engine's own (lower-resolution) basemap.

### 6. Turn it on
**Actions** tab → enable workflows → **MasRainman radar nowcast** → **Run workflow**. Check the run log, then your Telegram.

## Secrets and variables

Settings → Secrets and variables → Actions.

**Secrets**: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`, `TELEGRAM_ADMIN_CHAT_ID`, `TELEGRAM_ADMIN_IDS` (optional), `FB_PAGE_ID`, `FB_PAGE_TOKEN`, `WHAPI_TOKEN`, `WHAPI_CHANNEL_ID`, `CARTO_API_KEY` (optional).

**Variables** (all optional):

| Variable | Default | Meaning |
|---|---|---|
| `BRAND_HANDLE` | `@masrainman` | Shown bottom-right on the card |
| `BRAND_URL` | `#` | Link used on the web map |
| `HASHTAGS` | `#MasRainman #Nowcast` | Added to every caption (region tags such as `#KeralaRains` are added automatically) |
| `TELEGRAM_CHANNEL_MODE` | `new` | `new` = new channel post each update, `edit` = keep one live post and refresh it |
| `ALERT_MIN_DBZ` | `30` | A region gets a card only when some town has at least this (30 = moderate, 40 = heavy) now or within 60 min |
| `REGIONS` | `tn,kl,ka` | Which regional cards to make |
| `VIEW_LAT_DEG` | `1.6` | How close the card zooms (degrees of latitude shown; 1.0 = closer, 2.5 = wider) |
| `CARD_BLOCK` | `2` | Size of the rain blocks (1 = 2 km, 2 = 4 km, 3 = 6 km) |
| `BASEMAP_URL` | CARTO Positron | Any `{z}/{x}/{y}` tile URL for a different white basemap |
| `APPROVALS_VIA_LISTENER` | `false` | Set `true` only if you run `listener.py` |

## How the approval works
Each new radar update sends you one card per region with rain, each with **✅ Yes, post to Facebook + WhatsApp** and **❌ No**. The previous unanswered card for the same region loses its buttons so only the newest is live. Pressing Yes takes the picture and caption straight from that Telegram message, posts them, and updates the message to `✅ Facebook | ✅ WhatsApp • 12:08 PM` (or the error). Cards older than 45 minutes can't be posted (stale radar). Only ids in `TELEGRAM_ADMIN_IDS` can approve.

**Timing:** GitHub Actions picks the press up on the next run, so posting can take up to ~10 minutes. For instant posting run `python listener.py` on any always-on machine (same env vars) and set `APPROVALS_VIA_LISTENER=true`.

## Making "every 10 minutes" reliable
GitHub's scheduler often starts runs late or skips them when it is busy. If that bothers you, add a free job at **cron-job.org** every 10 minutes:
`POST https://api.github.com/repos/<you>/masrainman-radar/actions/workflows/masrainman.yml/dispatches` with header `Authorization: Bearer <fine-grained token with Actions: read & write on this repo>`, `Accept: application/vnd.github+json` and body `{"ref":"main"}` — then delete the `schedule:` lines from the workflow.

## Customising
* **Colours:** `DBZ_BAND_COLORS` in `nowcast_bot.py` (8 bands, 20 → 55+ dBZ). The card, web map legend and loops all read from it.
* **Regions:** `REGIONS` in `masrainman/regions.py` (name in English + Tamil, the box the view may move in, which states' towns are named).
* **Kochi radar:** the engine leaves `koc_maxz` out because that radar was under maintenance. When it is back, add `"koc_maxz"` to `POLLED_PRODUCTS` in `nowcast_bot.py` for better central-Kerala coverage.
* **Tier names:** `LEGEND_EN` / `LEGEND_TA` in `masrainman/card.py`; caption wording in `TIERS` in `masrainman/alerts.py`.
* **Logo:** replace `assets/masrainman_logo.png` (square PNG) — the current one is a placeholder.
* **Places:** add rows to `masrainman/places.py` (English, Tamil, lat, lon, priority, state).
* **Boundary data:** `assets/boundaries.json` was built from the district GeoJSON in github.com/udit-001/india-maps-data, which does not state a licence. Swap in another source with `tools/build_boundaries.py` if you need clearly licensed data.

## Disclaimer
Experimental nowcast derived from IMD radar imagery; not an official forecast. Every caption says so and points people to official IMD warnings.
