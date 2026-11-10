# Moe's Network Trend Scanner

A daily scanner that reads free public news feeds about supplements, nutraceuticals, pharma, regulation and South African health, counts which topics are suddenly getting more coverage, and feeds a **Trends** tab on the website.

Nothing here is live yet. Nothing in `Website/` has been changed.

## What's in this folder

| File | What it is |
|---|---|
| `trends.html` | The future Trends tab. Same header, footer and black-and-gold style as `find.html`. |
| `sample-trends.json` | Real data from a live scan run on this Mac, so you can preview the page. |
| `worker.js` | The Cloudflare Worker (Option B below). Paste it straight into the Cloudflare editor. |
| `local_scan.py` | The same scanner in Python. Runs on this Mac (preview) or on GitHub (Option A). |
| `.github/workflows/trend-scan.yml` | The daily GitHub job for Option A. The folder is hidden in Finder; press Cmd+Shift+. to see it. |
| `styles.css`, `nav.js`, `assets/` | **Copies** of the site files, only so the preview works here. Don't copy them back into `Website/`. |
| `screens/` | Screenshots of the page at desktop and phone width. |

## Preview it on this Mac

```
cd "/Users/Moey/Desktop/Supplement Brand Kit/TrendScanner"
python3 local_scan.py            # fetch the feeds (about 2 minutes) and rebuild sample-trends.json
python3 -m http.server 8000      # then open http://localhost:8000/trends.html
```

`local_scan.py` keeps its history in `.local_state/`, so running it every day builds up the 30-day baseline. If you edit the topic list, run `python3 local_scan.py --offline --retag` to re-tag what's already saved.

## How trends are scored (no paid AI)

1. Each story is tagged with topics from a starter dictionary: about 60 ingredients, formats, health areas, market themes and regulatory themes (probiotics, creatine, magnesium glycinate, GLP-1, gummies, sachets, SAHPRA and so on). Edit `TOPICS` in **both** `worker.js` and `local_scan.py`.
2. For each topic, it compares mentions in the **last 3 days** with the **previous 27 days**. The score goes up when growth is fast, when many different publishers mention the topic, and when it appears across categories (trade news, pharma, regulatory, SA, social). A topic that only one source is talking about gets half the score.
3. It also looks for **new two- and three-word phrases** that several sources are suddenly using in headlines, so it can catch things the dictionary doesn't know about yet.
4. **First 2–4 weeks:** many feeds only show their latest 10–50 stories, so at first the "previous 27 days" is thin and most topics read as "Rising". Scores settle once a few weeks of history have built up.

## Feeds (checked live on 10 Nov 2026)

**Working (28):** NutraIngredients, FoodNavigator, Nutritional Outlook, SupplySide Supplement Journal (the old Natural Products Insider feed now points here), Vitafoods Insights, BioPharma Dive, PharmaTimes, STAT, Pharmaceutical Technology, FDA press releases, FDA recalls, Health-e News, Bhekisisa, Medical Brief, Food Business Africa, Reddit r/Supplements, Reddit r/Nootropics, YouTube (one example channel), and 10 Google News searches: nutraceutical trends, probiotics, gut health, creatine, magnesium, collagen, GLP-1 supplements, SAHPRA, supplements South Africa, and Fierce Pharma/Fierce Biotech.

**Patchy (kept in the list):** Reddit r/southafrica search. It worked once but often returns "429 Too Many Requests", because Reddit limits tools that read without logging in. PharmaTimes timed out once and then worked.

**Dropped:**
- Fierce Pharma, Fierce Biotech and Nutraceuticals World direct feeds return 403 (blocked) to scanners. Fierce is covered through Google News instead.
- The NutraIngredients-USA and NutraIngredients-Asia feeds now redirect to the main NutraIngredients feed.
- SAHPRA's own site didn't connect; it's covered through a Google News "SAHPRA" search instead.
- The WHO news feed is mostly years old.
- IOL, News24/Health24, Daily Maverick, SA Pharmacist, Endpoints and Healthline either have no working feed or block scanners.

The scanner never tries to get around a block. It identifies itself as `MoesNetworkTrendScanner/1.0 (+https://moesnetwork.pages.dev)`, waits between requests, and gives each feed a 15-second timeout. One failing feed never stops the others. To add or remove a feed, edit `FEEDS` in both files.

## Social media: what's covered and what isn't

- **Covered:** Reddit (public RSS) and YouTube (one RSS feed per channel; YouTube has no feed for search results). Swap the example channel for South African fitness or wellness creators by changing `channel_id`.
- **Not covered: TikTok and Instagram.** Neither offers a free public feed, and scraping them breaks their terms, so the scanner doesn't. To add them later, use a paid, terms-compliant data service. Options include TikTok's Research API (academic and non-profit access only), Meta's Content Library (approved researchers only), or commercial social-listening tools (paid). Its output can be added as one more feed in the `social` category.
- Reddit may block requests from cloud servers (Cloudflare or GitHub), not just slow them down. If the Reddit feeds keep showing "not reachable", that's why.

---

## Choose where it runs

The brief asked for a Cloudflare Worker, and it's built and tested (Option B). Testing turned up one catch: **on Cloudflare's free plan, a scheduled Worker gets only 10 ms of CPU time per run** (Cloudflare Workers limits page). In testing, one run of this scanner used about **50–150 ms**. Most of that is reading the XML of 29 feeds and the saved 30-day history. On the free plan, Cloudflare would likely stop runs with "Exceeded CPU Time Limits".

- **Option A: GitHub Actions. Free, recommended to start.** GitHub runs `local_scan.py` once a day and saves `trends.json` in a public repository. There's no CPU limit to worry about, and it's the exact code already tested live on this Mac.
- **Option B: Cloudflare Worker.** Use this if you upgrade to Workers Paid ($5/month). That raises the CPU limit to 30 seconds per run and keeps everything in your Cloudflare account. You can also try it on the free plan and watch for errors.

### Free-tier limits (from the official docs)

- **Cloudflare Workers Free:** 100,000 requests a day, 10 ms CPU per request or cron run, 50 outbound fetches per run, and 5 Cron Triggers per account.
- **Workers KV Free:** 100,000 reads a day, 1,000 writes a day, and 1 GB of storage. This scanner uses about 9 writes a day. Reads are cached for 5 minutes, and browsers cache `/trends.json` for 15 minutes.
- **GitHub Actions:** standard runners are free in **public** repositories. Scheduled runs can be delayed when GitHub is busy. GitHub turns off a public repo's schedule after 60 days with no activity; the daily commit should count as activity, but if it stops, re-enable it in the Actions tab.

### Option A: GitHub Actions (free)

1. On github.com, select **+** (top right) → **New repository**. Name it `moesnetwork-trends`, set it to **Public**, and select **Create repository**.
2. Upload everything in this `TrendScanner` folder, including the hidden `.github` folder. In Finder, press Cmd+Shift+. to see it.
3. In the repository, open **Settings** → **Actions** → **General** → **Workflow permissions**, select **Read and write permissions**, then **Save**.
4. Open the **Actions** tab, select **Daily trend scan** → **Run workflow**. After about 3 minutes, `trends.json` appears in the repo.
5. Your data URL is `https://raw.githubusercontent.com/<your-github-username>/moesnetwork-trends/main/trends.json`. Use it as `trendsUrl` in step 2 of "Add the Trends tab to the website" below.

### Option B: Cloudflare Worker

All paths are from Cloudflare's current docs (checked 10 Nov 2026).

1. **Create the storage.** In the Cloudflare dashboard, go to the **Workers KV** page (direct link: https://dash.cloudflare.com/?to=/:account/workers/kv/namespaces) → **Create instance**. Name it `moesnetwork-trends` → **Create**.
2. **Create the Worker.** Go to **Workers & Pages** → **Create application** → **Start with Hello World!** → **Get started**. Name it `moesnetwork-trends` → **Deploy**.
3. **Paste the code.** Open the Worker → **Edit code**. Delete everything in the editor, paste all of `worker.js`, then select **Deploy**.
4. **Connect the storage.** Open the Worker → **Bindings** tab → **Add binding** → **KV namespace** → **Add binding**. For **Variable name**, type `TRENDS` (capitals). For the namespace, choose `moesnetwork-trends` → **Add binding**.
5. **Add the test password.** Go to **Settings** → **Variables and Secrets** → **Add**. Set type **Secret**, name `RUN_KEY`, and make the value a long random password you make up → **Deploy**.
6. **Add the daily schedule.** Go to **Settings** → **Triggers** → **Cron Triggers**, and add these three: `0 4 * * *`, `20 4 * * *` and `40 4 * * *`. Each one scans a third of the feeds, starting 06:00 SAST. That's 3 of the 5 free cron triggers. If you'd rather use one, add only `0 4 * * *` and every run scans all the feeds.
7. **Test it.** Open `https://moesnetwork-trends.<your-subdomain>.workers.dev/run?key=<your RUN_KEY>`. It scans all the feeds, which takes about 1–2 minutes because of the polite pauses, and shows a short report. Then open `.../trends.json` to see the data. Past runs are under **Settings** → **Trigger Events** → **View events**.
8. Your data URL is `https://moesnetwork-trends.<your-subdomain>.workers.dev/trends.json`.

The Worker allows the website `https://moesnetwork.pages.dev`, its preview URLs and `localhost:8000` to read the data (CORS). Edit `ALLOWED_ORIGINS` in `worker.js` if you add a custom domain.

#### Optional: AI summary (off by default)

The Worker can add a short plain-English paragraph about the day's trends, written by Claude (`claude-sonnet-5-5`). It only switches on if you add both of these under **Settings** → **Variables and Secrets**:

- a Secret named `ANTHROPIC_API_KEY`, with a key from console.anthropic.com
- a Variable named `ENABLE_AI_SUMMARY`, set to `true`

**Cost:** one call a day, with about 3–5k tokens in and under 1.5k out. At $2 / $10 per million tokens that's about US$0.01–0.03 a day, well under US$1 a month. Check current pricing first. The prompt tells it to describe news coverage only, make no health or efficacy claims, and flag that SA rules are changing. The page labels the paragraph as AI-written. If the call fails, the page simply shows no summary. GitHub Option A doesn't include this hook.

---

## Add the Trends tab to the website (later, together)

1. Copy **only** `trends.html` into `Website/`. The site already has `styles.css`, `nav.js` and `assets/`.
2. In `trends.html`, near the bottom, change
   `window.TRENDS_CONFIG = { trendsUrl: "sample-trends.json" };`
   to your data URL from Option A or B.
3. Add the nav link to every page's header (`index.html`, `find.html`, `shop.html`), after "Find a manufacturer":
   `<a href="trends.html">Trends</a>`
   `trends.html` already has it, marked as the current page.
4. Deploy the site as usual (Cloudflare Pages project `moesnetwork`).

## House rules built in

- Every story shows its publisher, date and a link to the original (opens in a new tab). Only headlines and short excerpts are shown.
- The page states that trends are news signals, not health advice, and that SA supplement rules are changing.
