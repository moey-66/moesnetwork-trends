#!/usr/bin/env python3
"""
Moe's Network weekly newsletter builder.

Takes the previous Monday-Friday of trend-scanner data, picks the #1 trend from each day
(5 different topics), adds 2-3 source articles for each, and writes:

    <out>/<slug>/email.html      full email (inline CSS, table layout) for previews/other providers
    <out>/<slug>/brevo.html      the same email for Brevo's API (with Brevo's {{ unsubscribe }} tag)
    <out>/<slug>/email.txt       plain-text version
    <out>/<slug>/issue.json      subject, preview text and the data used
    <archive>/<slug>.html        web archive page for the website
    <archive>/index.html         list of all issues
    <archive>/feed.xml           RSS feed of issues
    <archive>/issues.json        machine-readable list of issues

Where each day's ranking comes from (best first):
    1. history/YYYY-MM-DD.json snapshots (saved by the patched local_scan.py)
    2. old versions of trends.json in the repo's git history
    3. a replay: re-scores .local_state/items.json as it would have looked at the end of that day
       (uses local_scan.py's own scoring), so a first issue can be made before any snapshots exist

Usage:
    python3 weekly_digest.py --repo PATH_TO_TRENDS_REPO
    python3 weekly_digest.py --repo . --week-of 2026-11-02     # any date in the week you want
    python3 weekly_digest.py --repo . --no-replay              # snapshots/git only
    python3 weekly_digest.py --repo . --exclude-topics ""      # allow every topic (default leaves out recalls)

No network access, no API keys. Only headlines, short excerpts and links are used.
"""
import argparse, copy, glob, html, json, os, re, subprocess, sys
from datetime import datetime, timedelta, timezone, date

SAST = timezone(timedelta(hours=2), "SAST")
SITE = "https://moesnetwork.pages.dev"
LOGO_URL = SITE + "/assets/logo-gold.png"
TRENDS_URL = SITE + "/trends"
SIGNUP_URL = SITE + "/#newsletter"          # change once the signup block is on the site
ARCHIVE_BASE = SITE + "/newsletter/"         # where the archive pages will live on the site
DISCLAIMER = "News signals, not health advice."
# Topics never used in the weekly 5 (topic ids from local_scan.TOPICS). Recalls are mostly food
# recalls, not supplement trends. Override with --exclude-topics or NEWSLETTER_EXCLUDE_TOPICS
# (comma-separated ids, e.g. "recalls,supplement-reg"; an empty value allows every topic).
DEFAULT_EXCLUDE_TOPICS = "recalls"
BRAND = "Moe's Network"
RECENT_DAYS = 3

# Email colours (match Website/styles.css)
C_BG, C_PANEL, C_LINE = "#000000", "#14110b", "#3a2f17"
C_GOLD, C_GOLDHI, C_TEXT, C_MUTED, C_DIM = "#E2B857", "#F6D77B", "#F3EFE6", "#B4A88C", "#8a806b"
FONT = "Montserrat, -apple-system, 'Segoe UI', Arial, Helvetica, sans-serif"

# ---- "What it means / product angle": one plain sentence per topic. ---------------------
# Market and product observations only. Never say an ingredient does anything for health.
ANGLES = {
    "probiotics": "Probiotic launches keep coming, so a new one needs a clear strain story, a stated count at end of shelf life and packaging that protects it.",
    "prebiotics": "Fibre is being repositioned from 'boring' to 'gut-friendly', which opens room for easy-mix powders and fibre added to everyday formats.",
    "creatine": "Creatine has moved beyond the gym crowd; formats like gummies and flavoured sachets, plus independent purity testing, are where brands are competing.",
    "magnesium": "Magnesium is a crowded shelf, so the form (glycinate, citrate and so on), the dose per serving and the price per day are what set products apart.",
    "mag-glycinate": "Shoppers now ask for the magnesium form by name, so naming it clearly on the front of pack matters.",
    "ashwagandha": "Ashwagandha gets both buzz and safety scrutiny, so check current local rules and keep label wording conservative before launching.",
    "collagen": "Collagen is shifting from plain powders to blends, drinks and gummies, and source (marine or bovine) is a key choice on pack.",
    "nad": "NAD+ and NMN sit in the 'longevity' space where rules differ by country, so confirm what is allowed in South Africa before planning a product.",
    "glp1": "Weight-loss drug news keeps spilling into supplements; 'GLP-1 support' wording draws regulator attention, so stick to claims you can back and are allowed to make.",
    "colostrum": "Colostrum is getting fresh attention; sourcing, dairy-allergen labelling and cost per serving are the practical hurdles.",
    "sea-moss": "Sea moss is a social-media favourite; consistent sourcing and testing for heavy metals are what serious brands highlight.",
    "berberine": "Berberine is often marketed next to weight-loss drugs, so expect scrutiny of claims and keep wording conservative.",
    "electrolytes": "Hydration products are moving from sport to everyday use; low-sugar stick packs and clear sodium content per serving are common angles.",
    "protein": "Protein is turning up in snacks, drinks and coffee; taste, texture and price per gram of protein decide repeat buys.",
    "vitamin-d": "Vitamin D is a staple, so differentiation comes from format (drops, sprays, gummies) and combination products.",
    "omega-3": "Omega-3 buyers look at source and freshness; algae-based and smaller-capsule options are growing niches.",
    "iron": "Iron is a big topic in women's and children's nutrition, and gentle formats and clear dosing are what shoppers compare.",
    "zinc": "Zinc is usually sold in blends, so it's worth checking which combinations competitors are bundling it into.",
    "b-vitamins": "B vitamins and folate are basics where the form on the label (for example methylfolate) is becoming a selling point.",
    "curcumin": "Turmeric products compete on standardised extracts and absorption technology, so the supplier's documentation matters.",
    "mushrooms": "Functional mushrooms are moving into coffees, gummies and powders; extract type and fruiting body versus mycelium are the questions buyers ask.",
    "melatonin": "Melatonin rules differ by country and by dose, so check the local position before planning a sleep product around it.",
    "caffeine": "Energy products are under watch for caffeine levels and sales to young people; clear per-serving amounts and warnings protect a brand.",
    "acv": "Apple cider vinegar has moved from bottles to gummies and capsules; taste and sugar content are the main format trade-offs.",
    "cbd": "CBD and hemp rules change often and differ by country; confirm SAHPRA's current position before investing in this category.",
    "creatine-women": "Creatine marketed to women is a fast-growing niche, with product design, flavour and messaging aimed at a new audience.",
    "gummies": "Gummies remain a top format; sugar content, heat stability in transport and how much active fits in each gummy are the real constraints.",
    "sachets": "Single-serve sachets and stick packs suit on-the-go use and trial sizes, which helps a new brand get its first buyers.",
    "powders": "Powders and drink mixes let brands fit higher doses per serving; flavour and how easily it mixes decide reviews.",
    "shots": "Liquid shots are a small, premium format; shelf life and cold-chain needs should be checked early.",
    "effervescent": "Effervescent tablets are an easy-to-take format; moisture-proof packaging is essential.",
    "softgels": "Softgels suit oils; gelatine versus plant-based shells is a key choice for halaal, kosher or vegan buyers.",
    "gut-health": "Gut health is a broad umbrella; brands are narrowing in on a specific occasion or user to stand out.",
    "sleep": "Sleep products are crowded; non-melatonin blends and evening rituals (drinks, sprays) are common positioning angles.",
    "womens-health": "Women's health is being split into life stages, and products built for a specific stage are drawing attention and investment.",
    "mens-health": "Men's health products are moving from vague 'vitality' to specific needs, and claims in this space are closely watched.",
    "longevity": "'Longevity' is a hot marketing word; regulators expect wording you can support, so treat it as positioning, not a promise.",
    "cognitive": "Brain-and-focus products are popular with students and professionals, but claims are scrutinised, so wording needs care.",
    "stress-mood": "Stress and mood products are growing, and regulators watch mental-health wording closely, so keep it about everyday routines.",
    "immunity": "Immunity is a mature category; seasonal timing and combination products are how brands keep it fresh.",
    "weight": "Weight-management news is dominated by prescription drugs, and supplement claims in this area get extra scrutiny.",
    "metabolic": "Blood-sugar topics are growing alongside weight-loss drug news, and health claims here are tightly controlled.",
    "heart": "Heart-health products are an older shopper's category; trust signals like testing and clear dosing matter most.",
    "joints": "Joint products sell to active and older shoppers; format size and number of capsules per day affect reviews.",
    "skin-beauty": "'Beauty from within' keeps growing; collagen, hair and skin blends compete on format and packaging design.",
    "sports": "Sports nutrition is widening to casual gym-goers; tested-for-sport certification and taste are strong selling points.",
    "kids": "Children's products face stricter rules and parents read labels closely, so check dosing and format safety early.",
    "eye": "Eye-health products target screen-heavy lifestyles and older shoppers; ingredient amounts per serving are what get compared.",
    "menopause": "Menopause is moving from taboo to a mainstream category, with products and content built for this stage.",
    "personalised": "Personalised nutrition is mostly quizzes and subscriptions for now; a simple 'choose your blend' can capture some of the appeal.",
    "clean-label": "Shoppers check ingredient lists, so short labels and fewer additives are a selling point.",
    "sustainability": "Packaging is under the spotlight; recyclable or refillable options and honest wording help avoid greenwashing complaints.",
    "ai": "AI is showing up in formulation, marketing and personalisation; for a small brand the practical use is faster research and content.",
    "m-and-a": "Deal activity shows where big companies see growth, which is a useful signal for where a new brand might compete or partner.",
    "sahpra": "SAHPRA news can change what you may sell and say in South Africa, so check how it affects your category and labels.",
    "claims": "Claims and labelling are being watched closely, so keep wording to what the rules allow before you print labels.",
    "recalls": "Recalls show what can go wrong (contamination, undeclared ingredients), which is a reminder to ask manufacturers for test certificates.",
    "supplement-reg": "Rule changes elsewhere often hint at what may come locally, so it's worth knowing before you plan a launch.",
    "tariffs": "Tariffs and supply-chain news affect ingredient costs and lead times, so build some buffer into pricing and stock plans.",
}
GROUP_ANGLES = {
    "Ingredient": "Interest in this ingredient is rising; check sourcing, the form used, cost per serving and what the label may say locally.",
    "Format": "Format trends shape what shoppers pick up first; check manufacturing minimums and packaging needs before choosing one.",
    "Health area": "This need state is getting attention; keep wording to everyday routines and what local rules allow.",
    "Market": "This market shift could affect pricing, positioning or partners; worth watching before you commit to a plan.",
    "Regulatory": "Regulatory news can change what you may sell or say, so check how it applies to your category.",
    "Topic": "This topic is getting more coverage; read the sources before deciding whether it fits your brand.",
}


# ---- small helpers -----------------------------------------------------------------------
def esc(s):
    return html.escape(s or "", quote=True)


def brace_safe(s):
    """Escape text and neutralise curly braces so Brevo's template language never sees article text as code."""
    return esc(s).replace("{", "&#123;").replace("}", "&#125;")


def parse_dt(s):
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def publisher(src):
    src = (src or "").strip()
    src = re.sub(r"\s*\(via Google News\)$", "", src)
    if src.startswith("Google News:"):
        return "Google News"
    return src or "Source"


def day_label(d, long=False):
    return d.strftime("%A %-d %B") if long else d.strftime("%a %-d %b")


def short_date(dt):
    return dt.astimezone(SAST).strftime("%-d %b %Y")


def excerpt(s, n=150):
    s = re.sub(r"\s+", " ", s or "").strip()
    if len(s) <= n:
        return s
    return s[:n - 1].rsplit(" ", 1)[0].rstrip(",.;:-") + "…"


def week_range(any_day):
    mon = any_day - timedelta(days=any_day.weekday())
    return [mon + timedelta(days=i) for i in range(5)]


def previous_week(today):
    this_mon = today - timedelta(days=today.weekday())
    return week_range(this_mon - timedelta(days=7))


# ---- loading snapshots -------------------------------------------------------------------
def normalise_snapshot(trends, articles_for, generated_at, kind, ref):
    """Common shape: {generatedAt, kind, ref, trends:[{id,label,group,score,...,articles:[...]}]}."""
    out = []
    for t in trends:
        if t.get("kind", "topic") != "topic":
            continue
        row = {k: t.get(k) for k in ("id", "label", "group", "score", "direction", "growth", "recent",
                                     "baseline", "recentSources", "categories")}
        row["articles"] = articles_for(t)
        out.append(row)
    out.sort(key=lambda r: (-(r["score"] or 0), -(r["recent"] or 0)))
    return {"generatedAt": parse_dt(generated_at), "kind": kind, "ref": ref, "trends": out}


def articles_from_items(items, now, tid):
    cut = now - timedelta(days=RECENT_DAYS)
    arts = []
    for it in items:
        if tid not in (it.get("topics") or []):
            continue
        d = parse_dt(it["date"])
        if cut <= d <= now:
            arts.append({k: it.get(k, "") for k in ("title", "link", "source", "date", "category", "summary")})
    return arts


def load_history(repo):
    snaps = []
    for p in sorted(glob.glob(os.path.join(repo, "history", "*.json"))):
        try:
            with open(p) as f:
                s = json.load(f)
            snaps.append(normalise_snapshot(s["trends"], lambda t: t.get("articles", []), s["generatedAt"],
                                            "snapshot", os.path.relpath(p, repo)))
        except Exception as e:
            print("  skipped %s (%s)" % (p, e), file=sys.stderr)
    return snaps


def load_git(repo, since, until):
    """Every committed version of trends.json between two dates."""
    try:
        log = subprocess.run(["git", "-C", repo, "log", "--format=%H %cI", "--since", since.isoformat(),
                              "--until", until.isoformat(), "--", "trends.json"],
                             capture_output=True, text=True, check=True).stdout.split("\n")
    except Exception as e:
        print("  git history not available (%s)" % e, file=sys.stderr)
        return []
    snaps = []
    for line in log:
        if not line.strip():
            continue
        sha = line.split()[0]
        try:
            raw = subprocess.run(["git", "-C", repo, "show", sha + ":trends.json"],
                                 capture_output=True, text=True, check=True).stdout
            t = json.loads(raw)
            now = parse_dt(t["generatedAt"])
            items = t.get("items", [])
            snaps.append(normalise_snapshot(t["trends"], lambda tr, items=items, now=now:
                                            articles_from_items(items, now, tr["id"]),
                                            t["generatedAt"], "git", "git " + sha[:7]))
        except Exception as e:
            print("  skipped commit %s (%s)" % (sha[:7], e), file=sys.stderr)
    return snaps


def replay_day(repo, day):
    """Re-score the saved items as they would have looked at 23:59 SAST on `day`."""
    state = os.path.join(repo, ".local_state", "items.json")
    if not (os.path.exists(state) and os.path.exists(os.path.join(repo, "local_scan.py"))):
        return None
    sys.path.insert(0, repo)
    try:
        import local_scan
    finally:
        sys.path.pop(0)
    with open(state) as f:
        items = json.load(f)["items"]
    now = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=SAST).astimezone(timezone.utc)
    window = [copy.deepcopy(it) for it in items
              if now - timedelta(days=local_scan.WINDOW_DAYS) <= parse_dt(it["date"]) <= now]
    if not window:
        return None
    out = local_scan.build_output(window, [], now)
    return normalise_snapshot(out["trends"], lambda t: articles_from_items(window, now, t["id"]),
                              out["generatedAt"], "replay", "replay of items.json at end of " + day.isoformat())


def pick_snapshot(day, pools):
    """The snapshot nearest to midnight at the end of `day` (SAST), taken between noon that day and noon the next."""
    target = datetime(day.year, day.month, day.day, tzinfo=SAST) + timedelta(days=1)
    lo, hi = target - timedelta(hours=12), target + timedelta(hours=12)
    for pool in pools:   # pools are in priority order
        cands = [s for s in pool if lo <= s["generatedAt"] <= hi]
        if cands:
            return min(cands, key=lambda s: abs((s["generatedAt"] - target).total_seconds()))
    return None


# ---- choosing the 5 trends ----------------------------------------------------------------
# Publishers we prefer as sources (trade, pharma, regulators, SA health news, major outlets).
TRUSTED = {p.lower() for p in """NutraIngredients|NutraIngredients-USA|NutraIngredients-Asia|FoodNavigator|FoodNavigator-USA|
Nutritional Outlook|SupplySide Supplement Journal|Natural Products Insider|Vitafoods Insights|Nutraceuticals World|
Food Dive|Food Business News|Food Business Africa|BioPharma Dive|PharmaTimes|STAT|Pharmaceutical Technology|Fierce Pharma|
Fierce Biotech|Endpoints News|FDA press releases|FDA recalls|U.S. Food and Drug Administration (.gov)|Health-e News|Bhekisisa|
Medical Brief|Reuters|Bloomberg|Financial Times|The New York Times|The Washington Post|The Guardian|BBC|CNN|NPR|
The Wall Street Journal|Associated Press|AP News|CNBC|Forbes|Axios|News24|IOL|Daily Maverick|TimesLIVE|BusinessLIVE|
Business Day|Moneyweb|Bizcommunity|MyBroadband|TechCentral|The Citizen|Sowetan|SABC News|eNCA|Mail & Guardian|
GroundUp|Spotlight|Personal Care Insights|Nutrition Insight|Food Ingredients First|Just Food|The Grocer|Packaging Insights|
Beverage Daily|BeverageDaily|Dairy Reporter|Cosmetics Design|Healthline|Verywell Health|Medical News Today""".replace("\n", "").split("|")}
LISTICLE = re.compile(r"\b(best|top \d+|\d+ best|ranked|picks|buyer'?s guide|reviews?\b|coupon|discount|% off|deals?\b|"
                      r"where to buy|should you buy|tested|i tried|we tried)\b", re.I)
PR_WIRE = re.compile(r"openpr|ein ?presswire|prnewswire|pr newswire|globenewswire|business ?wire|newswire|"
                     r"market\.us|marketresearch|news ?channel nebraska|barchart|yahoo finance", re.I)
OFF_TOPIC = re.compile(r"\b(dogs?|cats?|pets?|puppy|horses?|equine|veterinar)\b", re.I)


TOPIC_REGEX = {}   # filled from local_scan.TOPICS when the repo is loaded


def load_topic_regex(repo):
    sys.path.insert(0, repo)
    try:
        import local_scan
        TOPIC_REGEX.update({tid: rx for tid, rx in local_scan.TOPIC_RE})
    except Exception as e:
        print("  topic list not loaded (%s); headline matching off" % e, file=sys.stderr)
    finally:
        sys.path.pop(0)


def article_quality(a):
    """Higher is better. Prefers trade/pharma/SA publishers; pushes down listicles, shop pages and press-release wires."""
    title, pub = a.get("title", ""), publisher(a.get("source", ""))
    q = 0
    if pub.lower() in TRUSTED:
        q += 3
    if a.get("category") in ("pharma", "regulatory", "sa"):
        q += 1
    if a.get("category") == "social":
        q -= 3
    if LISTICLE.search(title):
        q -= 4
    if PR_WIRE.search(pub) or PR_WIRE.search(a.get("link", "")):
        q -= 2
    if OFF_TOPIC.search(title):
        q -= 4
    if re.match(r"^(https?://|www\.)", pub) or "." in pub and " " not in pub and pub.lower() not in TRUSTED:
        q -= 2   # publisher shown only as a web address: usually a small blog or shop
    if len(title) > 140 or title.count("|") >= 1 or title.count(",") >= 4:
        q -= 3   # product listings ("X, 60 Caps | ... for Men & Women")
    if re.search(r"\b\d+\s?(caps|capsules|count|ct|softgels|gummies|tablets|servings)\b", title, re.I):
        q -= 4   # shop listing, not news
    return q


def choose_articles(trend, day, used_links, n_max=3, min_quality=None):
    end = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=SAST)
    start = end - timedelta(days=RECENT_DAYS)
    seen_titles, seen_pubs, picks = set(), set(), []
    cands = []
    for a in trend["articles"]:
        try:
            d = parse_dt(a["date"])
        except Exception:
            continue
        if not (start <= d <= end + timedelta(hours=12)):
            continue
        on_day = d.astimezone(SAST).date() == day
        if OFF_TOPIC.search(a.get("title", "")):
            continue   # pet and animal stories are never used as sources
        q = article_quality(a)
        rx = TOPIC_REGEX.get(trend["id"])
        if rx is not None and rx.search(a.get("title", "")):
            q += 2   # the headline itself names the topic
        if min_quality is not None and q < min_quality:
            continue
        cands.append((-(q + (1 if on_day else 0)), -d.timestamp(), a, d, q))
    cands.sort(key=lambda c: c[:2])
    for pass_no in (1, 2):   # first pass: one article per publisher; second pass: fill up
        for _, _, a, d, q in cands:
            if len(picks) >= n_max:
                break
            if len(picks) >= 2 and q < 0:
                continue   # a weak source may fill a gap, but never a third slot
            key = re.sub(r"[^a-z0-9]+", " ", a["title"].lower()).strip()[:70]
            pub = publisher(a["source"])
            if a["link"] in used_links or key in seen_titles or (pass_no == 1 and pub in seen_pubs):
                continue
            seen_titles.add(key)
            seen_pubs.add(pub)
            title = a["title"] if len(a["title"]) <= 150 else a["title"][:147].rsplit(" ", 1)[0] + "…"
            picks.append({"title": title, "link": a["link"], "publisher": pub, "date": d.isoformat(),
                          "dateLabel": short_date(d), "excerpt": excerpt(a.get("summary", "")),
                          "social": a.get("category") == "social"})
    return picks


def why_line(t, day):
    rec, src = t.get("recent") or 0, t.get("recentSources") or 0
    g, direction = t.get("growth") or 1, t.get("direction")
    if direction == "new":
        pace = "a topic that barely featured in the month before"
    elif direction == "up":
        pace = "about %s× its usual pace" % (("%.1f" % g).rstrip("0").rstrip("."))
    elif direction == "down":
        pace = "below its usual pace, but still among the most-covered topics that day"
    else:
        pace = "steady coverage that kept it near the top of the day's ranking"
    return "%d %s from %d %s in the three days to %s: %s." % (
        rec, "story" if rec == 1 else "stories", src, "publisher" if src == 1 else "publishers", day_label(day), pace)


def build_issue(repo, days, use_git=True, use_replay=True, exclude=()):
    since = datetime(days[0].year, days[0].month, days[0].day, tzinfo=SAST) - timedelta(days=1)
    until = datetime(days[-1].year, days[-1].month, days[-1].day, tzinfo=SAST) + timedelta(days=2)
    print("Loading snapshots for %s to %s" % (days[0], days[-1]))
    load_topic_regex(repo)
    hist = load_history(repo)
    gits = load_git(repo, since, until) if use_git else []
    print("  history files: %d, git versions of trends.json: %d" % (len(hist), len(gits)))
    used_topics, used_links, picks, per_day, extras_pool = set(exclude), set(), [], [], []
    if exclude:
        print("  left out of the weekly 5: %s" % ", ".join(sorted(exclude)))
    today = datetime.now(SAST).date()
    for day in days:
        if day >= today:
            print("  %s: not over yet, skipped" % day)
            per_day.append({"day": day.isoformat(), "source": None, "note": "not over yet"})
            continue
        snap = pick_snapshot(day, [hist, gits])
        if snap is None and use_replay:
            snap = replay_day(repo, day)
        if snap is None:
            print("  %s: no data" % day)
            per_day.append({"day": day.isoformat(), "source": None})
            continue
        chosen = None
        # The best-ranked unused topic with 2+ decent sources (normally the day's #1).
        for rank, t in enumerate(snap["trends"], 1):
            if t["id"] in used_topics:
                continue
            for min_q in (0, -2):
                arts = choose_articles(t, day, used_links, min_quality=min_q)
                if len(arts) >= 2:
                    chosen = (rank, t, arts)
                    break
            if chosen:
                break
        if not chosen:   # thin day: accept weaker sources rather than skip the day
            for min_articles in (2, 1):
                for rank, t in enumerate(snap["trends"], 1):
                    if t["id"] not in used_topics:
                        arts = choose_articles(t, day, used_links)
                        if len(arts) >= min_articles:
                            chosen = (rank, t, arts)
                            break
                if chosen:
                    break
        extras_pool += [(t, day) for t in snap["trends"][:8]]
        if not chosen:
            per_day.append({"day": day.isoformat(), "source": snap["ref"], "pick": None})
            continue
        rank, t, arts = chosen
        used_topics.add(t["id"])
        used_links.update(a["link"] for a in arts)
        picks.append(make_pick(t, day, rank, arts, snap, label=day_label(day, long=True)))
        print("  %s: #%d %-28s score %-4s (%s, %d articles)" % (day, rank, t["label"], t["score"], snap["kind"], len(arts)))
        per_day.append({"day": day.isoformat(), "source": snap["ref"], "kind": snap["kind"], "pick": t["id"], "rank": rank})
    # A day with no data: fill with the best remaining topic of the week so the issue still has 5.
    extras_pool.sort(key=lambda x: -(x[0]["score"] or 0))
    for t, day in extras_pool:
        if len(picks) >= 5:
            break
        if t["id"] in used_topics:
            continue
        arts = choose_articles(t, day, used_links, min_quality=0)
        if len(arts) >= 2:
            used_topics.add(t["id"])
            used_links.update(a["link"] for a in arts)
            picks.append(make_pick(t, day, None, arts, {"kind": "fill", "ref": "best remaining topic of the week"},
                                   label="Also trending this week"))
    return picks, per_day


def make_pick(t, day, rank, arts, snap, label):
    return {"day": day.isoformat(), "dayLabel": label, "rankThatDay": rank, "id": t["id"], "label": t["label"],
            "group": t["group"], "score": t["score"], "direction": t["direction"], "growth": t["growth"],
            "angle": ANGLES.get(t["id"], GROUP_ANGLES.get(t["group"], GROUP_ANGLES["Topic"])),
            "why": why_line(t, day), "articles": arts, "dataSource": snap["kind"]}


# ---- rendering: email --------------------------------------------------------------------
def subject_for(picks, days):
    names = [p["label"].split(" (")[0] for p in picks]
    span = "%d–%d %s" % (days[0].day, days[-1].day, days[-1].strftime("%b")) if days[0].month == days[-1].month \
        else "%s – %s" % (days[0].strftime("%-d %b"), days[-1].strftime("%-d %b"))
    lead = ", ".join(names[:2]) + (" + %d more" % (len(names) - 2) if len(names) > 2 else "")
    return "Top %d supplement trends (%s): %s" % (len(names), span, lead)


def email_html(issue, mode):
    """mode: 'full' = preview document (placeholder unsubscribe link);
    'brevo' = the document sent through Brevo's API, with Brevo's mandatory {{ unsubscribe }} tag.
    The web version is archive_html."""
    T = brace_safe if mode == "brevo" else esc
    unsub = "{{ unsubscribe }}" if mode == "brevo" else "%UNSUBSCRIBE_URL%"
    p = []
    a = p.append
    a('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" bgcolor="%s" style="background:%s;margin:0;padding:0;width:100%%;">' % (C_BG, C_BG))
    a('<tr><td align="center" style="padding:24px 12px;">')
    a('<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:%s;opacity:0;">%s</div>' % (C_BG, T(issue["preview"])))
    a('<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%%;max-width:600px;background:%s;border:1px solid %s;border-radius:14px;">' % (C_BG, C_LINE))
    # header
    a('<tr><td align="center" style="padding:28px 24px 8px;">'
      '<a href="%s" style="text-decoration:none;"><img src="%s" width="200" height="100" alt="%s" '
      'style="display:block;width:200px;max-width:60%%;height:auto;border:0;outline:none;color:%s;font-family:%s;font-size:22px;font-weight:bold;"></a></td></tr>'
      % (SITE, LOGO_URL, esc(BRAND), C_GOLD, FONT))
    a('<tr><td align="center" style="padding:4px 24px 0;font-family:%s;font-size:12px;letter-spacing:4px;text-transform:uppercase;color:%s;font-weight:bold;">Weekly trend brief · %s</td></tr>'
      % (FONT, C_GOLD, T(issue["weekLabel"])))
    a('<tr><td align="center" style="padding:10px 28px 6px;font-family:%s;font-size:26px;line-height:32px;color:%s;font-weight:800;">The top %d supplement trends of last week</td></tr>' % (FONT, C_TEXT, len(issue["trends"])))
    a('<tr><td align="center" style="padding:0 32px 20px;font-family:%s;font-size:15px;line-height:23px;color:%s;">'
      'The highest-scoring trend from each weekday, picked by our free news scanner. Each one links to the articles behind it.</td></tr>' % (FONT, C_MUTED))
    # trends
    for i, t in enumerate(issue["trends"], 1):
        a('<tr><td style="padding:0 20px 16px;">')
        a('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" bgcolor="%s" style="background:%s;border:1px solid %s;border-radius:12px;">' % (C_PANEL, C_PANEL, C_LINE))
        a('<tr><td style="padding:18px 20px 4px;font-family:%s;font-size:11px;letter-spacing:3px;text-transform:uppercase;color:%s;font-weight:bold;">%d · %s</td></tr>'
          % (FONT, C_GOLD, i, T(t["dayLabel"])))
        a('<tr><td style="padding:2px 20px 6px;font-family:%s;font-size:21px;line-height:27px;color:%s;font-weight:800;">%s</td></tr>' % (FONT, C_GOLDHI, T(t["label"])))
        a('<tr><td style="padding:0 20px 10px;font-family:%s;font-size:15px;line-height:23px;color:%s;">%s</td></tr>' % (FONT, C_TEXT, T(t["angle"])))
        a('<tr><td style="padding:0 20px 12px;font-family:%s;font-size:12px;line-height:18px;color:%s;">Why it made the list: %s</td></tr>' % (FONT, C_DIM, T(t["why"])))
        a('<tr><td style="padding:0 20px 6px;font-family:%s;font-size:11px;letter-spacing:2px;text-transform:uppercase;color:%s;font-weight:bold;">Read the sources</td></tr>' % (FONT, C_MUTED))
        for art in t["articles"]:
            ex = ('<br><span style="color:%s;font-size:13px;line-height:19px;">%s</span>' % (C_MUTED, T(art["excerpt"]))) if art["excerpt"] else ""
            a('<tr><td style="padding:4px 20px 10px;font-family:%s;font-size:15px;line-height:21px;">'
              '<a href="%s" target="_blank" style="color:%s;text-decoration:underline;font-weight:bold;">%s</a>'
              '<br><span style="color:%s;font-size:12px;">%s · %s</span>%s</td></tr>'
              % (FONT, esc(art["link"]), C_GOLD, T(art["title"]), C_DIM, T(art["publisher"]), T(art["dateLabel"]), ex))
        a('<tr><td style="padding:0 0 8px;font-size:1px;line-height:1px;">&nbsp;</td></tr></table></td></tr>')
    # CTA
    a('<tr><td align="center" style="padding:8px 24px 6px;">'
      '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
      '<td align="center" bgcolor="%s" style="border-radius:10px;background:%s;">'
      '<a href="%s" target="_blank" style="display:inline-block;padding:13px 26px;font-family:%s;font-size:15px;font-weight:800;color:#000000;text-decoration:none;border-radius:10px;">See the live Trends page</a>'
      '</td></tr></table></td></tr>' % (C_GOLD, C_GOLD, TRENDS_URL, FONT))
    a('<tr><td align="center" style="padding:6px 28px 22px;font-family:%s;font-size:13px;line-height:20px;color:%s;">Updated every morning: <a href="%s" style="color:%s;">moesnetwork.pages.dev/trends</a></td></tr>'
      % (FONT, C_MUTED, TRENDS_URL, C_GOLD))
    # footer
    a('<tr><td style="padding:16px 28px 26px;border-top:1px solid %s;font-family:%s;font-size:12px;line-height:19px;color:%s;" align="center">' % (C_LINE, FONT, C_DIM))
    a('<strong style="color:%s;">%s</strong> We share headlines, short excerpts and links to the original publishers. '
      'Scores come from how often a topic appears in public news feeds; they are not reviews, endorsements or medical information. '
      'Talk to a qualified professional about your own health, and check SAHPRA rules before making any product claim.<br><br>' % (C_MUTED, esc(DISCLAIMER)))
    a('You get this because you signed up for the free %s weekly brief and confirmed your email. '
      '<a href="%s" style="color:%s;">Unsubscribe</a> at any time.<br>' % (esc(BRAND), unsub, C_GOLD))
    a('%s · South Africa · <a href="%s" style="color:%s;">moesnetwork.pages.dev</a>' % (esc(BRAND), SITE, C_GOLD))
    a('</td></tr></table></td></tr></table>')
    body = "\n".join(p)
    return ('<!DOCTYPE html>\n<html lang="en-ZA"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="color-scheme" content="dark"><meta name="supported-color-schemes" content="dark">'
            '<title>%s</title></head><body style="margin:0;padding:0;background:%s;">\n%s\n</body></html>\n'
            % (esc(issue["subject"]), C_BG, body))


def email_text(issue):
    L = [BRAND.upper() + " — WEEKLY TREND BRIEF", issue["weekLabel"], "",
         "The top %d supplement trends of last week: the highest-scoring trend from each weekday," % len(issue["trends"]),
         "picked by our free news scanner, with links to the articles behind it.", ""]
    for i, t in enumerate(issue["trends"], 1):
        L += ["-" * 60, "%d. %s — %s" % (i, t["label"], t["dayLabel"]), "", t["angle"], "",
              "Why it made the list: " + t["why"], "", "Read the sources:"]
        for a in t["articles"]:
            L += ["  * %s" % a["title"], "    %s, %s" % (a["publisher"], a["dateLabel"]), "    %s" % a["link"]]
        L.append("")
    L += ["-" * 60, "Live Trends page (updated every morning): " + TRENDS_URL, "",
          DISCLAIMER + " We share headlines, short excerpts and links to the original publishers.",
          "Talk to a qualified professional about your own health, and check SAHPRA rules before making any product claim.", "",
          "You get this because you signed up for the free %s weekly brief." % BRAND,
          "Unsubscribe: %UNSUBSCRIBE_URL%", BRAND + " · " + SITE]
    return "\n".join(L) + "\n"


# ---- rendering: web archive -------------------------------------------------------------
WEB_CSS = """
:root{--bg:#000;--bg2:#0d0b07;--panel:#14110b;--line:rgba(214,168,62,.28);--gold:#E2B857;--gold2:#C9962F;--goldhi:#F6D77B;--text:#F3EFE6;--muted:#B4A88C;--dim:#8a806b}
*{box-sizing:border-box}
body{margin:0;font-family:Montserrat,-apple-system,Arial,sans-serif;color:var(--text);line-height:1.6;
 background:radial-gradient(ellipse at 80% -10%,#2a2110 0%,var(--bg2) 35%,var(--bg) 70%) fixed;background-color:#000}
a{color:var(--gold)}
.wrap{max-width:760px;margin:0 auto;padding:28px 16px 60px}
.top{display:flex;justify-content:space-between;align-items:center;gap:12px;padding-bottom:18px;border-bottom:1px solid var(--line)}
.top img{height:44px;display:block;mix-blend-mode:screen}
.top a.sub{border:1px solid var(--gold);padding:8px 14px;border-radius:999px;text-decoration:none;font-weight:600;font-size:14px}
.kick{color:var(--gold);letter-spacing:4px;text-transform:uppercase;font-weight:600;font-size:12px;margin:34px 0 8px}
h1{font-size:clamp(28px,5vw,42px);line-height:1.1;margin:0 0 12px;font-weight:800}
.lead{color:var(--muted);font-size:17px;margin:0 0 28px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:22px;margin:0 0 18px}
.card .day{color:var(--gold);letter-spacing:3px;text-transform:uppercase;font-weight:700;font-size:11px}
.card h2{margin:4px 0 10px;font-size:23px;color:var(--goldhi)}
.card .why{color:var(--dim);font-size:13px}
.card h3{color:var(--muted);letter-spacing:2px;text-transform:uppercase;font-size:11px;margin:16px 0 6px}
.card ul{list-style:none;padding:0;margin:0}
.card li{padding:8px 0;border-top:1px solid rgba(214,168,62,.12)}
.card li a{font-weight:700}
.card li .meta{display:block;color:var(--dim);font-size:12px}
.card li .ex{display:block;color:var(--muted);font-size:14px}
.cta{display:inline-block;background:linear-gradient(180deg,var(--goldhi),var(--gold2));color:#000;font-weight:800;padding:13px 24px;border-radius:12px;text-decoration:none}
.foot{color:var(--dim);font-size:13px;margin-top:34px;border-top:1px solid var(--line);padding-top:18px}
.list li{margin:0 0 12px}
"""


def page_shell(title, desc, body, canonical):
    return ('<!DOCTYPE html>\n<html lang="en-ZA"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>%s</title><meta name="description" content="%s"><link rel="canonical" href="%s">'
            '<link rel="icon" href="%s/assets/badge.png">'
            '<link rel="alternate" type="application/rss+xml" title="%s weekly brief" href="%sfeed.xml">'
            '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            '<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700;800&display=swap" rel="stylesheet">'
            '<style>%s</style></head><body><div class="wrap">'
            '<div class="top"><a href="%s"><img src="%s" alt="%s"></a><a class="sub" href="%s">Get it free every Monday</a></div>'
            '%s<div class="foot"><strong>%s</strong> We share headlines, short excerpts and links to the original publishers. '
            'Scores come from how often a topic appears in public news feeds; they are not reviews, endorsements or medical information. '
            'Check SAHPRA rules before making any product claim.<br><a href="%s">All issues</a> · <a href="%s">Live Trends</a> · '
            '<a href="%sfeed.xml">RSS feed</a></div></div></body></html>\n'
            % (esc(title), esc(desc), esc(canonical), SITE, esc(BRAND), ARCHIVE_BASE, WEB_CSS, SITE, LOGO_URL, esc(BRAND),
               SIGNUP_URL, body, esc(DISCLAIMER), ARCHIVE_BASE, TRENDS_URL, ARCHIVE_BASE))


def archive_html(issue):
    b = ['<div class="kick">Weekly trend brief · %s</div><h1>The top %d supplement trends of last week</h1>'
         '<p class="lead">The highest-scoring trend from each weekday, picked by our free news scanner. Each one links to the articles behind it.</p>'
         % (esc(issue["weekLabel"]), len(issue["trends"]))]
    for i, t in enumerate(issue["trends"], 1):
        b.append('<div class="card"><div class="day">%d · %s</div><h2>%s</h2><p>%s</p><p class="why">Why it made the list: %s</p><h3>Read the sources</h3><ul>'
                 % (i, esc(t["dayLabel"]), esc(t["label"]), esc(t["angle"]), esc(t["why"])))
        for a in t["articles"]:
            b.append('<li><a href="%s" target="_blank" rel="noopener">%s</a><span class="meta">%s · %s</span>%s</li>'
                     % (esc(a["link"]), esc(a["title"]), esc(a["publisher"]), esc(a["dateLabel"]),
                        ('<span class="ex">%s</span>' % esc(a["excerpt"])) if a["excerpt"] else ""))
        b.append("</ul></div>")
    b.append('<p><a class="cta" href="%s">See the live Trends page</a></p>' % TRENDS_URL)
    return page_shell(issue["subject"] + " | " + BRAND, issue["preview"], "\n".join(b), ARCHIVE_BASE + issue["slug"] + ".html")


def write_archive_index(archive_dir):
    issues = []
    for p in sorted(glob.glob(os.path.join(archive_dir, "*.json"))):
        if os.path.basename(p) == "issues.json":
            continue
        with open(p) as f:
            issues.append(json.load(f))
    issues.sort(key=lambda x: x["slug"], reverse=True)
    rows = "".join('<li><a href="%s.html">%s</a><br><span style="color:var(--dim);font-size:13px">%s</span></li>'
                   % (esc(i["slug"]), esc(i["weekLabel"]), esc(", ".join(i["topics"]))) for i in issues)
    body = ('<div class="kick">Newsletter archive</div><h1>Every weekly trend brief</h1>'
            '<p class="lead">Free every Monday: the top supplement trend from each weekday, with links to the sources.</p>'
            '<ul class="list">%s</ul><p><a class="cta" href="%s">Get it free every Monday</a></p>' % (rows, SIGNUP_URL))
    with open(os.path.join(archive_dir, "index.html"), "w") as f:
        f.write(page_shell("Weekly trend brief archive | " + BRAND, "All past issues of the free weekly supplement trend brief.", body, ARCHIVE_BASE))
    with open(os.path.join(archive_dir, "issues.json"), "w") as f:
        json.dump(issues, f, indent=1, ensure_ascii=False)
    from email.utils import format_datetime
    items = "".join("<item><title>%s</title><link>%s%s.html</link><guid isPermaLink=\"true\">%s%s.html</guid>"
                    "<pubDate>%s</pubDate><description>%s</description></item>"
                    % (esc(i["subject"]), ARCHIVE_BASE, esc(i["slug"]), ARCHIVE_BASE, esc(i["slug"]),
                       format_datetime(parse_dt(i["builtAt"])), esc(i["preview"])) for i in issues[:30])
    with open(os.path.join(archive_dir, "feed.xml"), "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel><title>%s weekly trend brief</title>'
                '<link>%s</link><description>Free weekly brief: the top supplement trend from each weekday, with sources. %s</description>'
                '<language>en-za</language>%s</channel></rss>\n' % (esc(BRAND), ARCHIVE_BASE, esc(DISCLAIMER), items))
    return issues


# ---- main ----------------------------------------------------------------------------------
def main():
    here = os.path.dirname(os.path.abspath(__file__))
    default_repo = os.path.dirname(here) if os.path.exists(os.path.join(os.path.dirname(here), "local_scan.py")) else here
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=default_repo, help="trends repo folder (has local_scan.py, history/, .git)")
    ap.add_argument("--week-of", help="any date (YYYY-MM-DD) in the Mon-Fri week to cover; default: last week")
    ap.add_argument("--out", default=os.path.join(here, "issues"), help="folder for email files")
    ap.add_argument("--archive", default=os.path.join(here, "archive"), help="folder for web archive pages")
    ap.add_argument("--no-git", action="store_true", help="don't read old trends.json versions from git")
    ap.add_argument("--no-replay", action="store_true", help="don't re-score items.json for missing days")
    ap.add_argument("--exclude-topics", default=os.environ.get("NEWSLETTER_EXCLUDE_TOPICS", DEFAULT_EXCLUDE_TOPICS),
                    help="comma-separated topic ids to leave out (default: %(default)r; empty = none)")
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    today = datetime.now(SAST).date()
    days = week_range(date.fromisoformat(args.week_of)) if args.week_of else previous_week(today)
    exclude = {x.strip() for x in (args.exclude_topics or "").split(",") if x.strip()}
    picks, per_day = build_issue(repo, days, use_git=not args.no_git, use_replay=not args.no_replay, exclude=exclude)
    if not picks:
        print("No trend data for that week; nothing written.", file=sys.stderr)
        return 2
    if len(picks) < 5:
        print("WARNING: only %d distinct trends found for this week." % len(picks), file=sys.stderr)

    slug = "week-" + days[0].isoformat()
    week_label = "%s – %s" % (days[0].strftime("%-d %b"), days[-1].strftime("%-d %b %Y"))
    issue = {"slug": slug, "weekStart": days[0].isoformat(), "weekEnd": days[-1].isoformat(), "weekLabel": week_label,
             "builtAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
             "subject": subject_for(picks, days),
             "preview": "The top trend from each weekday, %s, with links to the original articles. Free, every Monday." % week_label,
             "topics": [p["label"] for p in picks], "trends": picks, "days": per_day,
             "excludedTopics": sorted(exclude), "disclaimer": DISCLAIMER, "trendsUrl": TRENDS_URL, "archiveUrl": ARCHIVE_BASE + slug + ".html"}

    out_dir = os.path.join(os.path.abspath(args.out), slug)
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(args.archive, exist_ok=True)
    files = {"email.html": email_html(issue, "full"), "brevo.html": email_html(issue, "brevo"),
             "email.txt": email_text(issue), "issue.json": json.dumps(issue, indent=1, ensure_ascii=False)}
    for name, text in files.items():
        with open(os.path.join(out_dir, name), "w") as f:
            f.write(text)
    with open(os.path.join(args.archive, slug + ".html"), "w") as f:
        f.write(archive_html(issue))
    summary = {k: issue[k] for k in ("slug", "weekLabel", "builtAt", "subject", "preview", "topics", "archiveUrl")}
    with open(os.path.join(args.archive, slug + ".json"), "w") as f:
        json.dump(summary, f, indent=1, ensure_ascii=False)
    write_archive_index(args.archive)
    print("\nSubject: " + issue["subject"])
    print("Wrote %s/ (email.html, brevo.html, email.txt, issue.json)" % out_dir)
    print("Wrote %s/%s.html, index.html, feed.xml" % (args.archive, slug))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write("slug=%s\nissue_dir=%s\n" % (slug, out_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
