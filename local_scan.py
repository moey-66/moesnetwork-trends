#!/usr/bin/env python3
"""
Moe's Network Trend Scanner - local version.

Does the same job as worker.js (fetch feeds -> parse -> dedupe -> tag topics ->
score emerging trends) but runs on this Mac and writes sample-trends.json, so the
Trends tab can be previewed and the scoring checked before anything is deployed.

Usage:
    python3 local_scan.py               # fetch all feeds, update history, write sample-trends.json
    python3 local_scan.py --offline     # re-score saved history only (no network)
    python3 local_scan.py --offline --retag   # after editing TOPICS/STOPWORDS: re-tag saved items
    python3 local_scan.py --out trends.json   # write somewhere else (used by the GitHub Actions option)
    python3 local_scan.py --no-history        # don't write the daily snapshot in history/

Every online run also saves history/YYYY-MM-DD.json (UTC date of the run): the top topics
with their scores and up to 6 example articles each. The weekly newsletter
(newsletter/weekly_digest.py) reads these snapshots. A second run on the same day
overwrites that day's file. Offline runs only write a snapshot with --history.

History is kept in .local_state/items.json so repeated daily runs build up the
30-day baseline, exactly like the Worker does in Cloudflare KV.

Keep FEEDS, TOPICS and the scoring constants in step with worker.js.
"""
import json, math, os, re, sys, time, html, urllib.request, urllib.error, urllib.parse
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(HERE, ".local_state")
STATE_FILE = os.path.join(STATE_DIR, "items.json")
OUT_FILE = os.path.join(HERE, "sample-trends.json")
HISTORY_DIR = os.path.join(HERE, "history")   # daily snapshots for the weekly newsletter
HISTORY_TOPICS = 15      # topics kept per snapshot
HISTORY_ARTICLES = 6     # example articles kept per topic

USER_AGENT = "MoesNetworkTrendScanner/1.0 (+https://moesnetwork.pages.dev)"
WINDOW_DAYS = 30        # keep items this long
RECENT_DAYS = 3         # "now" window
SPARK_DAYS = 14         # sparkline length
MAX_ITEMS_STORED = 3000
MAX_ITEMS_OUT = 300     # items sent to the page
FETCH_TIMEOUT = 15      # seconds per feed
PAUSE_BETWEEN = 1.5     # seconds between requests (be polite)
REDDIT_PAUSE = 30      # seconds before each Reddit request (Reddit returns HTTP 429 if faster)


def gnews(q, region="US"):
    lang = "en-ZA" if region == "ZA" else "en-US"
    return ("https://news.google.com/rss/search?q=" + urllib.parse.quote_plus(q + " when:30d")
            + "&hl=%s&gl=%s&ceid=%s:en" % (lang, region, region))

# ---- FEEDS: keep identical to worker.js ------------------------------------
# category: news (supplement trade) | pharma | regulatory | sa | social
# filter: True -> keep only items that match at least one topic (general-health feeds)
FEEDS = [
    # Supplement / nutraceutical trade news
    {"name": "NutraIngredients", "url": "https://www.nutraingredients.com/arc/outboundfeeds/rss/", "category": "news"},
    {"name": "FoodNavigator", "url": "https://www.foodnavigator.com/arc/outboundfeeds/rss/", "category": "news", "filter": True},
    {"name": "Nutritional Outlook", "url": "https://www.nutritionaloutlook.com/rss.xml", "category": "news"},
    {"name": "SupplySide Supplement Journal", "url": "https://www.supplysidesj.com/rss.xml", "category": "news"},
    {"name": "Vitafoods Insights", "url": "https://www.vitafoodsinsights.com/rss.xml", "category": "news"},
    {"name": "Google News: nutraceutical trends", "url": gnews("nutraceutical trend"), "category": "news"},
    {"name": "Google News: probiotics", "url": gnews("probiotics supplement"), "category": "news"},
    {"name": "Google News: gut health", "url": gnews("gut health supplement"), "category": "news"},
    {"name": "Google News: creatine", "url": gnews("creatine supplement"), "category": "news"},
    {"name": "Google News: magnesium", "url": gnews("magnesium supplement"), "category": "news"},
    {"name": "Google News: collagen", "url": gnews("collagen supplement"), "category": "news"},
    {"name": "Google News: GLP-1 supplements", "url": gnews("GLP-1 supplements"), "category": "news"},
    # Pharma
    {"name": "BioPharma Dive", "url": "https://www.biopharmadive.com/feeds/news/", "category": "pharma"},
    {"name": "PharmaTimes", "url": "https://www.pharmatimes.com/feed/", "category": "pharma"},
    {"name": "STAT", "url": "https://www.statnews.com/feed/", "category": "pharma"},
    {"name": "Pharmaceutical Technology", "url": "https://www.pharmaceutical-technology.com/feed/", "category": "pharma"},
    {"name": "Google News: Fierce Pharma / Fierce Biotech", "url": gnews("site:fiercepharma.com OR site:fiercebiotech.com"), "category": "pharma"},
    # Regulatory
    {"name": "FDA press releases", "url": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml", "category": "regulatory", "filter": True},
    {"name": "FDA recalls", "url": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/food-safety-recalls/rss.xml", "category": "regulatory", "filter": True},
    {"name": "Google News: SAHPRA", "url": gnews("SAHPRA", "ZA"), "category": "regulatory"},
    # South Africa
    {"name": "Google News: supplements South Africa", "url": gnews("supplements South Africa", "ZA"), "category": "sa"},
    {"name": "Health-e News", "url": "https://health-e.org.za/feed/", "category": "sa", "filter": True},
    {"name": "Bhekisisa", "url": "https://bhekisisa.org/feed/", "category": "sa", "filter": True},
    {"name": "Medical Brief", "url": "https://www.medicalbrief.co.za/feed/", "category": "sa", "filter": True},
    {"name": "Food Business Africa", "url": "https://www.foodbusinessafrica.com/feed/", "category": "sa", "filter": True},
    # Social (only sources with free public feeds; no login, no API key)
    {"name": "Reddit r/Supplements", "url": "https://www.reddit.com/r/Supplements/.rss", "category": "social"},
    {"name": "Reddit r/Nootropics", "url": "https://www.reddit.com/r/Nootropics/.rss", "category": "social"},
    {"name": "Reddit r/southafrica (supplements)", "url": "https://www.reddit.com/r/southafrica/search.rss?q=supplements&restrict_sr=on&sort=new", "category": "social"},
    {"name": "YouTube: Andrew Huberman (example channel)", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC2D2CMWXMOVWx7giW1n3LIg", "category": "social", "filter": True},
]

# ---- TOPIC DICTIONARY: keep identical to worker.js -------------------------
# [id, label, group, regex]  (regex is matched case-insensitively on title + summary)
TOPICS = [
    # Ingredients
    ["probiotics", "Probiotics", "Ingredient", r"\bprobiotic|\bsynbiotic|\bpostbiotic|lactobacill|bifidobacter|\blive cultures?\b"],
    ["prebiotics", "Prebiotics & fibre", "Ingredient", r"\bprebiotic|\binulin\b|\bdietary fib(?:re|er)\b|\bpsyllium\b"],
    ["creatine", "Creatine", "Ingredient", r"\bcreatine\b"],
    ["magnesium", "Magnesium", "Ingredient", r"\bmagnesium\b"],
    ["mag-glycinate", "Magnesium glycinate", "Ingredient", r"\bmagnesium (?:bis)?glycinate\b|\bmagnesium threonate\b"],
    ["ashwagandha", "Ashwagandha", "Ingredient", r"\bashwagandha\b|\bwithania\b"],
    ["collagen", "Collagen", "Ingredient", r"\bcollagen\b"],
    ["nad", "NAD+ / NMN", "Ingredient", r"\bnad\+|\bnad\b|\bnmn\b|nicotinamide riboside"],
    ["glp1", "GLP-1 (and 'GLP-1 support')", "Topic", r"\bglp-?1\b|semaglutide|tirzepatide|ozempic|wegovy|mounjaro|zepbound|orforglipron"],
    ["colostrum", "Colostrum", "Ingredient", r"\bcolostrum\b"],
    ["sea-moss", "Sea moss", "Ingredient", r"\bsea ?moss\b|\birish moss\b"],
    ["berberine", "Berberine", "Ingredient", r"\bberberine\b"],
    ["electrolytes", "Electrolytes & hydration", "Ingredient", r"\belectrolyte|\bhydration\b"],
    ["protein", "Protein", "Ingredient", r"\bprotein (?:powder|shake|bar)s?\b|\bwhey\b|\bplant[- ]based protein\b|\bhigh[- ]protein\b"],
    ["vitamin-d", "Vitamin D", "Ingredient", r"\bvitamin d\d?\b"],
    ["omega-3", "Omega-3", "Ingredient", r"\bomega[- ]?3|\bfish oil\b|\bkrill\b|\balgal oil\b"],
    ["iron", "Iron", "Ingredient", r"\biron (?:supplement|deficien)|\banaemi|\banemi"],
    ["zinc", "Zinc", "Ingredient", r"\bzinc\b"],
    ["b-vitamins", "B vitamins & folate", "Ingredient", r"\bvitamin b\d{0,2}\b|\bfol(?:ate|ic acid)\b|\bmethylfolate\b"],
    ["curcumin", "Turmeric / curcumin", "Ingredient", r"\bturmeric\b|\bcurcumin\b"],
    ["mushrooms", "Functional mushrooms", "Ingredient", r"lion'?s mane|\breishi\b|\bcordyceps\b|\bchaga\b|functional mushroom"],
    ["melatonin", "Melatonin", "Ingredient", r"\bmelatonin\b"],
    ["caffeine", "Caffeine & energy", "Ingredient", r"\bcaffeine\b|\benergy drinks?\b|\bpre-?workout\b"],
    ["acv", "Apple cider vinegar", "Ingredient", r"apple cider vinegar"],
    ["cbd", "CBD / hemp", "Ingredient", r"\bcbd\b|\bcannabidiol\b|\bhemp\b"],
    ["creatine-women", "Creatine for women", "Topic", r"\bcreatine\b.{0,60}\bwomen\b|\bwomen\b.{0,60}\bcreatine\b"],
    # Formats
    ["gummies", "Gummies", "Format", r"\bgumm(?:y|ies)\b"],
    ["sachets", "Sachets & stick packs", "Format", r"\bsachets?\b|\bstick ?packs?\b"],
    ["powders", "Powders & drink mixes", "Format", r"\bpowders?\b|\bdrink mix"],
    ["shots", "Liquid shots", "Format", r"\b(?:wellness|liquid|functional) shots?\b"],
    ["effervescent", "Effervescent", "Format", r"\beffervescent\b"],
    ["softgels", "Softgels", "Format", r"\bsoftgels?\b"],
    # Health areas
    ["gut-health", "Gut health", "Health area", r"\bgut health\b|\bmicrobiome\b|\bdigestive health\b|\bgut[- ]brain\b"],
    ["sleep", "Sleep", "Health area", r"\bsleep\b|\binsomnia\b"],
    ["womens-health", "Women's health", "Health area", r"women'?s health|\bmenopaus|\bperimenopaus|\bpcos\b|\bprenatal\b|\bfertility\b|\bfemtech\b"],
    ["mens-health", "Men's health", "Health area", r"men'?s health|\btestosterone\b|\bprostate\b"],
    ["longevity", "Longevity & healthy ageing", "Health area", r"\blongevity\b|healthy ag(?:e)?ing|\banti-?ageing|\banti-?aging|\bhealthspan\b"],
    ["cognitive", "Brain & cognition", "Health area", r"\bcognit|\bnootropic|\bbrain health\b|\bbrain fog\b|\bmemory (?:loss|support|decline)\b"],
    ["stress-mood", "Stress & mood", "Health area", r"\bstress (?:relief|management|support|levels)\b|\banxiety\b|\bmood\b|\bcortisol\b|\badaptogen"],
    ["immunity", "Immunity", "Health area", r"\bimmunity\b|\bimmune (?:health|support|system|function)\b"],
    ["weight", "Weight management", "Health area", r"\bweight (?:loss|management)\b|\bobesity\b|\bsatiety\b|\bappetite\b"],
    ["metabolic", "Blood sugar & metabolic", "Health area", r"\bblood sugar\b|\bglucose\b|\binsulin\b|\bdiabet|\bmetabolic\b"],
    ["heart", "Heart health", "Health area", r"\bheart health\b|\bcardiovascular\b|\bcholesterol\b|\bblood pressure\b"],
    ["joints", "Joints & mobility", "Health area", r"\bjoint health\b|\bmobility\b|\bglucosamine\b|\barthritis\b"],
    ["skin-beauty", "Skin, hair & beauty", "Health area", r"\bbeauty\b|\bskin health\b|\bhair (?:loss|growth|health)\b|\bnutricosmetic"],
    ["sports", "Sports nutrition", "Health area", r"\bsports nutrition\b|\bathlet|\bmuscle\b|\bworkout\b|\bexercise\b|\bgym\b"],
    ["kids", "Kids & infant", "Health area", r"\bchildren'?s\b|\bkids\b|\binfant\b|\bpaediatric|\bpediatric"],
    ["eye", "Eye health", "Health area", r"\beye health\b|\blutein\b|\bmacular\b|\bvision (?:health|loss)\b"],
    ["menopause", "Menopause", "Health area", r"\bmenopaus|\bperimenopaus"],
    # Market & regulatory
    ["personalised", "Personalised nutrition", "Market", r"\bpersonali[sz]ed\b|\bprecision nutrition\b"],
    ["clean-label", "Clean label & natural", "Market", r"\bclean[- ]label\b|\bplant[- ]based\b|\bnatural ingredients?\b"],
    ["sustainability", "Sustainability & packaging", "Market", r"\bsustainab|\brecyclable\b|\bupcycl|\bcompostable\b"],
    ["ai", "AI in nutrition", "Market", r"\bartificial intelligence\b|\bai[- ](?:powered|driven|generated)\b|\bgenai\b"],
    ["m-and-a", "Deals & acquisitions", "Market", r"\bacqui(?:re|res|red|sition)\b|\bmerger\b|\bfunding round\b|\braises \$"],
    ["sahpra", "SAHPRA & SA regulation", "Regulatory", r"\bsahpra\b|\bcomplementary medicines?\b|\bmedicines control\b"],
    ["claims", "Health claims & labelling", "Regulatory", r"\bhealth claims?\b|\blabel(?:l)?ing\b|\bmisleading\b|\bfront[- ]of[- ]pack\b"],
    ["recalls", "Recalls & safety alerts", "Regulatory", r"\brecall|\bwarning letter\b|\badulterat|\bcontaminat|\bundeclared\b"],
    ["supplement-reg", "Supplement regulation", "Regulatory", r"\bdietary supplements?\b.{0,40}\b(?:rule|regulat|law|bill|ban)|\b(?:rule|regulat|law|bill|ban)\w*\b.{0,40}\bsupplements?\b"],
    ["tariffs", "Tariffs & supply chain", "Market", r"\btariff|\bsupply chain\b|\bshortage\b"],
]
TOPIC_RE = [(t[0], re.compile(t[3], re.I)) for t in TOPICS]
TOPIC_META = {t[0]: {"label": t[1], "group": t[2]} for t in TOPICS}

# Words ignored when looking for new phrases (n-grams) in headlines.
STOPWORDS = set("""a an and are as at be been being but by can could did do does for from had has have how i if in into is it
its just may more most new news not of on or our out over says say said so than that the their them then there these they this
those to too up us was we were what when where which who why will with without would you your about after against all also
amid any back before best between both during each first five four get gets help helps here inside its latest like make makes
many much need needs now off one only other per report reports set sets shows show study studies three two top under via vs way
week year years day days still could should while new launch launches launched update updates using use used based amid
supplement supplements health healthy industry market company companies product products brand brands consumer consumers
data research researchers trial trials drug drugs pharma people says thing things know review reviews video episode podcast
reddit question questions anyone help advice south africa african africa ceo deal deals sales growth global
next big biggest really actually going look looking guide trend trends everything something anything better good bad
real true right left long short high low full free part start starts end ends take takes taking taken gets got""".split())


# ---- helpers ----------------------------------------------------------------
def fnv1a(s):
    h = 0x811C9DC5
    for ch in s.encode("utf-8"):
        h ^= ch
        h = (h * 0x01000193) & 0xFFFFFFFF
    return "%08x" % h


def clean_text(s):
    if not s:
        return ""
    s = re.sub(r"<!\[CDATA\[([\s\S]*?)\]\]>", r"\1", s)
    s = html.unescape(s)          # entity-encoded HTML -> HTML
    s = re.sub(r"<!\[CDATA\[([\s\S]*?)\]\]>", r"\1", s)  # some feeds escape their CDATA too
    s = re.sub(r"<[^>]+>", " ", s)  # strip tags
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def tag(block, name):
    m = re.search(r"<%s\b[^>]*>([\s\S]*?)</%s>" % (re.escape(name), re.escape(name)), block, re.I)
    return m.group(1) if m else ""


def parse_date(s):
    s = clean_text(s)
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        d = None
    if d is None:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def normalise_link(u):
    u = u.strip()
    u = re.sub(r"[?&]utm_[^&#]*", "", u)
    u = re.sub(r"\?&", "?", u).rstrip("?&")
    return u


def title_key(t):
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()[:90]


def parse_feed(xml, feed):
    out = []
    for m in re.finditer(r"<(item|entry)\b[\s\S]*?</\1>", xml, re.I):
        b = m.group(0)
        title = clean_text(tag(b, "title"))
        link = ""
        lm = re.search(r"<link\b[^>]*rel=[\"']alternate[\"'][^>]*href=[\"']([^\"']+)", b, re.I) \
            or re.search(r"<link\b[^>]*href=[\"']([^\"']+)", b, re.I)
        if lm:
            link = html.unescape(lm.group(1))
        else:
            link = clean_text(tag(b, "link"))
        if not title or not link.startswith("http"):
            continue
        date = parse_date(tag(b, "pubDate") or tag(b, "published") or tag(b, "updated") or tag(b, "dc:date"))
        summary = clean_text(tag(b, "description") or tag(b, "summary") or tag(b, "media:description") or tag(b, "content"))
        source = feed["name"]
        sm = re.search(r"<source\b[^>]*>([\s\S]*?)</source>", b, re.I)
        if sm and "news.google.com" in feed["url"]:
            pub = clean_text(sm.group(1))
            if pub:
                source = pub + " (via Google News)"
                title = re.sub(r"\s+-\s+" + re.escape(pub) + r"\s*$", "", title)
                summary = ""  # Google News descriptions only repeat the headline
        if summary.lower().startswith(title.lower()[:40]):
            summary = summary[len(title):].strip(" -:")
        summary = re.sub(r"\s*submitted by\s+/u/\S+.*$", "", summary)  # Reddit boilerplate
        if len(summary) > 240:
            summary = summary[:237].rsplit(" ", 1)[0] + "..."
        out.append({"title": title[:220], "link": normalise_link(link), "date": date, "summary": summary,
                    "source": source, "feed": feed["name"], "category": feed["category"]})
    return out


def topics_for(text):
    return [tid for tid, rx in TOPIC_RE if rx.search(text)]


def grams_for(title):
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9+\-']*", title.lower())]
    toks = [w.strip("'") for w in words]
    grams = set()
    for n in (2, 3):
        for i in range(len(toks) - n + 1):
            g = toks[i:i + n]
            if g[0] in STOPWORDS or g[-1] in STOPWORDS:
                continue
            if any(len(w) < 3 or w.isdigit() for w in g):
                continue
            grams.add(" ".join(g))
    return sorted(grams)[:12]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
        return r.status, r.read(3_000_000).decode("utf-8", "replace")


# ---- scoring (same maths as worker.js) --------------------------------------
def score_series(recent, base, cats_recent, srcs_recent):
    expected = base / (WINDOW_DAYS - RECENT_DAYS) * RECENT_DAYS
    growth = (recent + 1.0) / (expected + 1.0)
    s = math.log2(growth) * 30 + min(recent, 20) * 2 + (cats_recent - 1) * 8 + (min(srcs_recent, 8) - 1) * 3
    if srcs_recent < 2:
        s *= 0.5  # one source talking a lot is not a trend yet
    if base == 0 and recent >= 2:
        direction = "new"
    elif growth >= 1.5:
        direction = "up"
    elif growth <= 0.67:
        direction = "down"
    else:
        direction = "flat"
    return max(0, round(s)), direction, round(growth, 2)


def build_output(items, statuses, now):
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    recent_cut = now - timedelta(days=RECENT_DAYS)

    def day_index(iso):
        d = datetime.fromisoformat(iso)
        return (day0 - d.replace(hour=0, minute=0, second=0, microsecond=0)).days

    def aggregate(key_fn):
        agg = {}
        for it in items:
            keys = key_fn(it)
            if not keys:
                continue
            d = datetime.fromisoformat(it["date"])
            is_recent = d >= recent_cut
            di = day_index(it["date"])
            for k in keys:
                a = agg.setdefault(k, {"recent": 0, "base": 0, "total": 0, "cats": set(), "srcs": set(),
                                        "allsrcs": set(), "spark": [0] * SPARK_DAYS, "links": [], "slinks": []})
                a["total"] += 1
                a["allsrcs"].add(it["source"])
                if is_recent:
                    a["recent"] += 1
                    a["cats"].add(it["category"])
                    a["srcs"].add(it["source"])
                    # example headlines: prefer publishers over forum posts
                    lst = a["slinks"] if it["category"] == "social" else a["links"]
                    if len(lst) < 3:
                        lst.append({"title": it["title"], "link": it["link"], "source": it["source"]})
                else:
                    a["base"] += 1
                if 0 <= di < SPARK_DAYS:
                    a["spark"][SPARK_DAYS - 1 - di] += 1
        return agg

    items.sort(key=lambda x: x["date"], reverse=True)
    trends = []
    for tid, a in aggregate(lambda it: it["topics"]).items():
        if a["recent"] < 2:
            continue
        score, direction, growth = score_series(a["recent"], a["base"], len(a["cats"]), len(a["srcs"]))
        trends.append({"id": tid, "label": TOPIC_META[tid]["label"], "group": TOPIC_META[tid]["group"], "kind": "topic",
                       "score": score, "direction": direction, "growth": growth, "recent": a["recent"],
                       "baseline": a["base"], "mentions": a["total"], "sources": len(a["allsrcs"]),
                       "recentSources": len(a["srcs"]), "categories": sorted(a["cats"]), "spark": a["spark"],
                       "examples": (a["links"] + a["slinks"])[:3]})
    trends.sort(key=lambda t: (-t["score"], -t["recent"]))

    # New phrases not in the dictionary: need >=2 different sources recently.
    labels_lc = " | ".join(t[1].lower() for t in TOPICS)
    phrases = []
    for g, a in aggregate(lambda it: it.get("grams", [])).items():
        if a["recent"] < 3 or len(a["srcs"]) < 2 or g in labels_lc:
            continue
        score, direction, growth = score_series(a["recent"], a["base"], len(a["cats"]), len(a["srcs"]))
        if direction in ("down", "flat"):
            continue
        phrases.append({"id": "phrase:" + g, "label": g, "group": "New phrase", "kind": "phrase", "score": score,
                        "direction": direction, "growth": growth, "recent": a["recent"], "baseline": a["base"],
                        "mentions": a["total"], "sources": len(a["allsrcs"]), "recentSources": len(a["srcs"]),
                        "categories": sorted(a["cats"]), "spark": a["spark"], "examples": (a["links"] + a["slinks"])[:3]})
    phrases.sort(key=lambda t: (-t["score"], -t["recent"]))
    # drop phrases that are sub-phrases of a higher-ranked phrase
    kept = []
    for p in phrases:
        if any(p["label"] in k["label"] or k["label"] in p["label"] for k in kept):
            continue
        kept.append(p)
        if len(kept) >= 8:
            break

    out_items = [{k: it[k] for k in ("id", "title", "link", "date", "source", "category", "summary", "topics")}
                 for it in items[:MAX_ITEMS_OUT]]
    return {
        "generatedAt": now.isoformat().replace("+00:00", "Z"),
        "generator": "local_scan.py",
        "windowDays": WINDOW_DAYS, "recentDays": RECENT_DAYS, "sparkDays": SPARK_DAYS,
        "topicLabels": {t[0]: t[1] for t in TOPICS},
        "trends": trends[:24],
        "phrases": kept,
        "items": out_items,
        "itemCount": len(items),
        "sources": statuses,
        "summary": None,
    }


def write_snapshot(out, items, now, history_dir=HISTORY_DIR):
    """Save a small daily snapshot (top topics + example articles with dates) for the newsletter."""
    recent_cut = now - timedelta(days=RECENT_DAYS)
    recent = [it for it in items if datetime.fromisoformat(it["date"]) >= recent_cut]
    recent.sort(key=lambda x: x["date"], reverse=True)
    topics = []
    for t in out["trends"][:HISTORY_TOPICS]:
        mine = [it for it in recent if t["id"] in it.get("topics", [])]
        # publishers first, forum posts last; newest first inside each group
        mine.sort(key=lambda it: (it["category"] == "social", -datetime.fromisoformat(it["date"]).timestamp()))
        arts = [{k: it.get(k, "") for k in ("title", "link", "source", "date", "category", "summary")}
                for it in mine[:HISTORY_ARTICLES]]
        row = {k: t[k] for k in ("id", "label", "group", "score", "direction", "growth", "recent",
                                 "baseline", "recentSources", "categories")}
        row["articles"] = arts
        topics.append(row)
    snap = {"date": now.strftime("%Y-%m-%d"), "generatedAt": out["generatedAt"], "generator": "local_scan.py",
            "recentDays": RECENT_DAYS, "trends": topics,
            "phrases": [{"label": p["label"], "score": p["score"]} for p in out["phrases"][:5]]}
    os.makedirs(history_dir, exist_ok=True)
    path = os.path.join(history_dir, snap["date"] + ".json")
    with open(path, "w") as f:
        json.dump(snap, f, indent=1, ensure_ascii=False)
    return path


def main():
    offline = "--offline" in sys.argv
    out_file = OUT_FILE
    if "--out" in sys.argv:
        out_file = os.path.abspath(sys.argv[sys.argv.index("--out") + 1])
    now = datetime.now(timezone.utc)
    os.makedirs(STATE_DIR, exist_ok=True)
    state = {"items": [], "statuses": []}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            state = json.load(f)
    by_id = {it["id"]: it for it in state["items"]}
    seen_titles = {title_key(it["title"]) for it in state["items"]}
    statuses = state.get("statuses", [])

    if not offline:
        statuses = []
        for i, feed in enumerate(FEEDS):
            if i:
                # Reddit rate-limits unauthenticated feed readers hard, so wait longer before those.
                time.sleep(REDDIT_PAUSE if "reddit.com" in feed["url"] else PAUSE_BETWEEN)
            st = {"name": feed["name"], "url": feed["url"], "category": feed["category"], "ok": False, "items": 0, "new": 0}
            try:
                code, body = fetch(feed["url"])
                parsed = parse_feed(body, feed)
                st["ok"] = bool(parsed)
                st["items"] = len(parsed)
                if not parsed:
                    st["error"] = "no items parsed (HTTP %s)" % code
                for it in parsed:
                    if it["date"] is None:
                        it["date"] = now
                    if it["date"] < now - timedelta(days=WINDOW_DAYS) or it["date"] > now + timedelta(days=1):
                        continue
                    tk = title_key(it["title"])
                    iid = fnv1a(it["link"])
                    if iid in by_id or tk in seen_titles:
                        continue
                    text = it["title"] + " " + it["summary"]
                    it["topics"] = topics_for(text)
                    if feed.get("filter") and not it["topics"]:
                        continue
                    it["grams"] = grams_for(it["title"])
                    it["id"] = iid
                    it["date"] = it["date"].isoformat()
                    by_id[iid] = it
                    seen_titles.add(tk)
                    st["new"] += 1
            except urllib.error.HTTPError as e:
                st["error"] = "HTTP %s" % e.code
            except Exception as e:  # per-feed isolation: one bad feed never stops the run
                st["error"] = type(e).__name__ + ": " + str(e)[:120]
            statuses.append(st)
            print(("OK  " if st["ok"] else "ERR ") + "%-45s items=%-3s new=%-3s %s" % (
                feed["name"], st["items"], st["new"], st.get("error", "")))

    names = {f["name"] for f in FEEDS}
    statuses = [st for st in statuses if st["name"] in names]  # forget feeds removed from FEEDS
    if "--retag" in sys.argv:
        for it in by_id.values():
            it["topics"] = topics_for(it["title"] + " " + it["summary"])
            it["grams"] = grams_for(it["title"])

    cutoff = now - timedelta(days=WINDOW_DAYS)
    items = [it for it in by_id.values() if datetime.fromisoformat(it["date"]) >= cutoff]
    items.sort(key=lambda x: x["date"], reverse=True)
    items = items[:MAX_ITEMS_STORED]
    with open(STATE_FILE, "w") as f:
        json.dump({"items": items, "statuses": statuses}, f)

    t0 = time.process_time()
    out = build_output(items, statuses, now)
    cpu_ms = (time.process_time() - t0) * 1000
    with open(out_file, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    if "--no-history" not in sys.argv and (not offline or "--history" in sys.argv):
        print("Snapshot:", write_snapshot(out, items, now))
    ok = sum(1 for s in statuses if s["ok"])
    print("\nFeeds OK: %d/%d   items kept (30 days): %d   scoring CPU: %.0f ms (Python)" % (ok, len(statuses), len(items), cpu_ms))
    print("Top emerging:")
    for t in out["trends"][:10]:
        print("  %-32s score=%-4s %-5s recent=%-3s base=%-3s sources=%s" % (
            t["label"], t["score"], t["direction"], t["recent"], t["baseline"], t["recentSources"]))
    print("New phrases:", ", ".join(p["label"] for p in out["phrases"]) or "(none yet)")
    print("Wrote", out_file)


if __name__ == "__main__":
    main()
