"""
fetch_news.py — pulls headlines from a curated set of free RSS feeds,
dedupes, classifies (upgrade/earnings/M&A/etc), tags with tickers, and
writes docs/data/headlines.json.

Idempotent: re-running merges with the existing log so nothing is lost
between cron runs. Capped at MAX_KEEP most-recent stories.
"""
import json
import re
import sys
import time
import hashlib
import html
from html.parser import HTMLParser
import warnings
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlparse

import socket
import feedparser

# Cap per-feed network IO at 15 seconds — some feeds hang otherwise
socket.setdefaulttimeout(15)
warnings.filterwarnings("ignore")

ROOT = Path(__file__).parent.parent
DATA = ROOT / "docs" / "data"
TICKERS_FILE = Path(__file__).parent / "universe_tickers.csv"
NAMES_FILE   = Path(__file__).parent / "universe_names.csv"
ALIASES_FILE = Path(__file__).parent / "universe_aliases.csv"
HEADLINES_PATH = DATA / "headlines.json"

MAX_KEEP = 600
USER_AGENT = "numichart-news/0.2"
SUMMARY_TEXT_LIMIT = 1000
ARTICLE_TEXT_LIMIT = 8000
ARTICLE_FETCH_LIMIT = 35
ARTICLE_FETCH_TIMEOUT = 5
ARTICLE_MIN_CHARS = 280
ARTICLE_SKIP_DOMAINS = {
    "ft.com",
    "wsj.com",
    "nytimes.com",
}
BIZTOC_EXTERNAL_SKIP_DOMAINS = {
    "biztoc.com",
    "alltoc.com",
    "chat.openai.com",
    "x.com",
    "bsky.app",
    "facebook.com",
    "t.betteruptime.com",
}

# ── Feed list ────────────────────────────────────────────────────────────────
# (display_name, category, url)
FEEDS = [
    # General market news
    ("MarketWatch",       "markets",   "http://feeds.marketwatch.com/marketwatch/topstories/"),
    ("MarketWatch RT",    "markets",   "http://feeds.marketwatch.com/marketwatch/marketpulse/"),
    ("CNBC Top",          "markets",   "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
    ("CNBC Earnings",     "earnings",  "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839135"),
    ("CNBC Economy",      "macro",     "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"),
    ("CNBC Investing",    "markets",   "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839263"),
    ("NASDAQ Stocks",     "stocks",    "https://www.nasdaq.com/feed/rssoutbound?category=Stocks"),
    ("NASDAQ Markets",    "markets",   "https://www.nasdaq.com/feed/rssoutbound?category=Markets"),
    ("Investing.com",     "markets",   "https://www.investing.com/rss/news.rss"),
    ("Investing Stock",   "stocks",    "https://www.investing.com/rss/news_25.rss"),
    ("Investing Forex",   "fx",        "https://www.investing.com/rss/news_1.rss"),
    ("Seeking Alpha",     "stocks",    "https://seekingalpha.com/feed.xml"),
    ("Seeking Alpha M&A", "ma",        "https://seekingalpha.com/market_currents.xml"),
    ("WSJ Markets",       "markets",   "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
    ("WSJ Business",      "markets",   "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml"),
    ("WSJ World",         "macro",     "https://feeds.a.dj.com/rss/RSSWorldNews.xml"),
    ("Markets Insider",   "stocks",    "https://markets.businessinsider.com/rss/news"),
    ("BI Finance",        "markets",   "https://www.businessinsider.com/finance.rss"),
    ("NYT Business",      "macro",     "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("NYT Economy",       "macro",     "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml"),
    ("FT Markets",        "markets",   "https://www.ft.com/markets?format=rss"),
    ("FT Companies",      "stocks",    "https://www.ft.com/companies?format=rss"),
    # Press release wires (lots of stock-specific filings)
    ("PR Newswire Fin",   "press",     "https://www.prnewswire.com/rss/financial-services-latest-news/financial-services-latest-news-list.rss"),
    ("PR Newswire All",   "press",     "https://www.prnewswire.com/rss/news-releases-list.rss"),
    ("GlobeNewswire",     "press",     "https://www.globenewswire.com/RssFeed/orgclass/1/feedTitle/GlobeNewswire%20-%20News%20about%20Public%20Companies"),
    # Aggregators
    ("BizToc",            "aggregator","https://biztoc.com/feed"),
    # SEC filings — actual current-events feed (was broken in v1)
    ("SEC 8-K Filings",   "filings",   "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&dateb=&owner=include&count=40&output=atom"),
    ("SEC 13D/G",         "filings",   "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=SC+13D&company=&dateb=&owner=include&count=40&output=atom"),
]

# ── Ticker universe ──────────────────────────────────────────────────────────
def load_tickers() -> set[str]:
    if not TICKERS_FILE.exists():
        return set()
    return {
        s.strip().upper()
        for s in TICKERS_FILE.read_text(encoding="utf-8").splitlines()
        if s.strip() and not s.strip().startswith("#")
    }


def load_name_map() -> list[tuple[str, str]]:
    """Return list of (company_name, ticker), longest-first. Pulls from BOTH
    universe_names.csv (canonical names) and universe_aliases.csv (brand /
    product / subsidiary aliases). De-duped by lowercase name."""
    pairs: list[tuple[str, str]] = []
    seen_keys = set()

    for path in (NAMES_FILE, ALIASES_FILE):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = s.split(",", 1)
            if len(parts) != 2:
                continue
            ticker = parts[0].strip().upper()
            name = parts[1].strip()
            if not name or not ticker or len(name) < 3:
                continue
            key = name.lower()
            if key in seen_keys:
                continue
            seen_keys.add(key)
            pairs.append((name, ticker))

    pairs.sort(key=lambda p: -len(p[0]))
    return pairs


TICKERS = load_tickers()
NAME_MAP = load_name_map()

# Common English words / acronyms that look like tickers — exclude
TICKER_BLOCKLIST = {
    "A","I","AT","BE","BY","DO","FOR","GO","IF","IN","IS","IT","OF","ON","OR",
    "SO","TO","UP","US","WE","AM","PM","AN","NO","AS","HE","ME","MY","OK",
    "ALL","AND","ANY","ARE","BUT","CAN","DAY","FED","GET","GMT","GOP","GOV",
    "HAS","HOW","ITS","NEW","NOT","NOW","OUT","SAY","SEC","SET","THE","TOP",
    "WAR","WHO","WHY","YOU","CEO","CFO","COO","COP","EPA","EST","ETC","FBI",
    "GDP","IPO","IRS","NEWS","OPEC","UK","EU","UN","AI","ML","UI","UX","VP",
    "API","CDC","CIA","FDA","FAA","CSV","ETF","ETFS","PDF","PR","SDK","HQ",
    "NYC","LA","SF","DC","Q1","Q2","Q3","Q4","M&A","JR","SR","INC","LLC",
    "CO","CORP","LTD","NV","SA","AG","PLC","USA","USD","BST","EDT","CET",
    "WTI","BPS","YOY","QOQ","MOM","WOW","WSJ","FT","CNBC","BBG","RT","UAW",
    "DOJ","DOE","DHS","DOL","DOT","HUD","TSA","ESG","SPAC","TLDR","FYI",
    "RNG","ION","KEY","TRUE","FALSE","JUST","NOW","NEXT","ONE","TWO","ROW",
    "OWN","OFF","BIG","TOP","WIN","HIT","WAY","BAD","GOOD","FREE","SEE",
    "MEN","RUN","BEAT","WANT","HOME","WORK","CALL","PUT","BUY","SELL","HOLD",
    "RATE","JOB","CUT","WIN","ALSO","LATE","REAL","HUGE","SAME","BLUE","RED",
    "MOST","FUND","LOAN","BANK","TREE","ROAD","GAS","OIL","GOLD",
}

_TICKER_PATTERNS = [
    re.compile(r"\$([A-Z]{1,5})\b"),                        # $TSLA
    re.compile(r"\(([A-Z]{1,5})\)"),                        # (TSLA)
    re.compile(r"\b(?:NYSE|NASDAQ|NYSEARCA|AMEX):\s*([A-Z]{1,5})"),  # NYSE: TSLA
]
_BARE_TICKER = re.compile(r"\b([A-Z]{2,5})\b")


def extract_tickers(text: str) -> list[str]:
    """Return up to 4 tickers found in text, ranked by confidence."""
    if not text:
        return []
    found: dict[str, int] = {}

    for pat in _TICKER_PATTERNS:
        for m in pat.findall(text):
            t = (m[-1] if isinstance(m, tuple) else m).upper()
            if t in TICKERS and t not in TICKER_BLOCKLIST:
                found[t] = found.get(t, 0) + 5

    for m in _BARE_TICKER.findall(text):
        t = m.upper()
        if t in TICKERS and t not in TICKER_BLOCKLIST:
            found[t] = found.get(t, 0) + 1

    # Company-name + alias matches (case-sensitive — proper-noun company
    # names start with a capital in news copy; "Apple" matches but "apple"
    # the fruit doesn't). Allow optional possessive 's / ' suffix.
    for name, ticker in NAME_MAP:
        if ticker in TICKER_BLOCKLIST:
            continue
        try:
            # \b name (?:'s|’s|')? \b — possessive optional
            pattern = r"\b" + re.escape(name) + r"(?:'s|’s|')?\b"
            if re.search(pattern, text):
                # Boost name matches over bare-token matches
                found[ticker] = found.get(ticker, 0) + 4
        except re.error:
            continue

    if not found:
        return []
    ranked = sorted(found.items(), key=lambda kv: -kv[1])
    return [t for t, _ in ranked[:4]]


# ── Headline classifier ─────────────────────────────────────────────────────
# Map a headline to an "action type" — used by the In Play view for color/icon
_TYPE_PATTERNS = [
    ("upgrade",   re.compile(r"\b(?:upgrades?|raised?|raises|hikes?|boost(?:s|ed)?|positive ratings?)\b", re.I)),
    ("downgrade", re.compile(r"\b(?:downgrades?|cuts?|lowers?|slashes?|reduces?|negative ratings?)\b", re.I)),
    ("analyst",   re.compile(r"\b(?:initiates? coverage|reiterates?|maintains?|price target|PT raised|PT cut)\b", re.I)),
    ("earnings",  re.compile(r"\b(?:Q[1-4]|earnings (?:beat|miss|report)|EPS|reported|results|quarterly|guidance)\b", re.I)),
    ("ma",        re.compile(r"\b(?:acquires?|acquisition|merger|to buy|buyout|takeover|deal|combine)\b", re.I)),
    ("fda",       re.compile(r"\b(?:FDA|approval|approved|rejected|trial results|phase \d|EUA|biologic)\b", re.I)),
    ("legal",     re.compile(r"\b(?:lawsuit|sued?|settlement|fine|probe|investigation|charges?|fraud)\b", re.I)),
    ("guidance",  re.compile(r"\b(?:raises? guidance|cuts? guidance|outlook|forecast|sees? Q|prelim)\b", re.I)),
    ("dividend",  re.compile(r"\b(?:dividend|buyback|repurchase|stock split)\b", re.I)),
    ("offering",  re.compile(r"\b(?:offering|prices? offering|secondary|debt offering|notes due|IPO)\b", re.I)),
    ("8k",        re.compile(r"\b(?:8-K|13D|13G|S-1|10-Q|10-K|prospectus)\b", re.I)),
    ("macro",     re.compile(r"\b(?:Fed|inflation|CPI|PCE|jobs report|GDP|unemployment|nonfarm|FOMC|rates?|tariff)\b", re.I)),
]


def classify(title: str, category: str) -> str:
    """Return short action type, falls back to category."""
    for typ, pat in _TYPE_PATTERNS:
        if pat.search(title):
            return typ
    return category or "news"


# ── Sentiment classifier (conservative — only color when confident) ─────────
_POS_RE = re.compile(
    r"\b(beats?|beat|surge[ds]?|soar[ds]?|jump[s]?|jumped|rall(?:y|ies|ied)|"
    r"climb[s]?|climbed|gain[s]?|gained|rise[s]?|rose|hike[ds]?|raise[ds]?|"
    r"upgrade[ds]?|outperform[s]?|tops?|topped|exceeds?|exceeded|strong|"
    r"record\s+high|all[-\s]time\s+high|profit[s]?|profitable|expand[s]?|"
    r"approves?|approved|wins?|won|secures?|secured|launches?|launched)\b",
    re.I,
)
_NEG_RE = re.compile(
    r"\b(misses?|missed|plunge[ds]?|slump[s]?|slumped|slide[ds]?|slid|"
    r"falls?|fell|drop[s]?|dropped|tumble[ds]?|crash(?:es|ed)?|sink[s]?|sank|"
    r"downgrade[ds]?|cut[s]?|slashe[ds]?|reduce[ds]?|lower[ed]?|"
    r"lawsuit|sued?|charged?|fined?|fraud|scandal|probe|investigation|"
    r"recall[s]?|halt[s]?|halted|suspend[s]?|suspended|"
    r"bankrupt(?:cy)?|insolven(?:t|cy)|layoffs?|fired|resign[s]?|resigned|"
    r"warn(?:s|ed|ing)?|weak|disappoint(?:s|ing|ed)?|"
    r"shrink[s]?|shrank|loss(?:es)?|missed\s+estimates?|guides?\s+down)\b",
    re.I,
)


def sentiment(title: str, type_: str) -> str:
    """positive / negative / neutral. Falls through to the action-type
    semantics when the verb-based check is ambiguous."""
    t = title or ""
    has_pos = bool(_POS_RE.search(t))
    has_neg = bool(_NEG_RE.search(t))
    if has_pos and not has_neg:
        return "positive"
    if has_neg and not has_pos:
        return "negative"
    # Type-based fallback
    if type_ in ("upgrade", "dividend"):
        return "positive"
    if type_ in ("downgrade", "legal"):
        return "negative"
    return "neutral"


# ── Feed parsing ─────────────────────────────────────────────────────────────
def _hash_url(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:12]


def _parse_published(entry) -> str | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc).isoformat()
            except Exception:
                pass
    return None


def _clean_title(title: str) -> str:
    title = re.sub(r"<[^>]+>", "", title or "").strip()
    title = re.sub(r"\s+", " ", title)
    return title


def _soft_limit(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    shortened = text[:limit].rstrip()
    return shortened.rsplit(" ", 1)[0] or shortened


def _looks_truncated(text: str) -> bool:
    stripped = html.unescape(text or "").strip()
    return stripped.endswith("…") or stripped.endswith("...")


def _clean_summary(summary: str) -> str:
    return _soft_limit(_clean_title(html.unescape(summary or "")), SUMMARY_TEXT_LIMIT)


class _ArticleTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._capture_depth = 0
        self._parts: list[str] = []
        self._json_ld: list[str] = []
        self._in_json_ld = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = {k.lower(): (v or "") for k, v in attrs}
        if tag in ("script", "style", "noscript", "svg", "iframe", "form", "nav", "footer", "header", "aside"):
            if tag == "script" and "ld+json" in attrs_dict.get("type", ""):
                self._in_json_ld = True
            else:
                self._skip += 1
            return
        if self._skip:
            return
        attr_text = " ".join(attrs_dict.values()).lower()
        if tag in ("article", "main") or any(x in attr_text for x in ("article", "story-body", "entry-content", "post-content")):
            self._capture_depth += 1
        if tag in ("p", "h2", "li", "blockquote") and self._capture_depth:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if self._in_json_ld and tag == "script":
            self._in_json_ld = False
            return
        if self._skip and tag in ("script", "style", "noscript", "svg", "iframe", "form", "nav", "footer", "header", "aside"):
            self._skip -= 1
            return
        if tag in ("article", "main") and self._capture_depth:
            self._capture_depth -= 1
        if tag in ("p", "h2", "li", "blockquote") and self._capture_depth:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._in_json_ld:
            self._json_ld.append(data)
            return
        if self._skip or not self._capture_depth:
            return
        text = data.strip()
        if text:
            self._parts.append(text)

    def text(self) -> str:
        body = _normalize_article_text(" ".join(self._parts))
        if len(body) >= ARTICLE_MIN_CHARS:
            return body
        for raw in self._json_ld:
            found = _article_body_from_json_ld(raw)
            if found:
                return found
        return body


def _keep_headline(title: str) -> bool:
    title = (title or "").strip()
    return not (title.startswith("Is ") or title.endswith("?"))


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def _normalize_article_text(text: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return _soft_limit(text, ARTICLE_TEXT_LIMIT)


def _article_body_from_json_ld(raw: str) -> str:
    try:
        data = json.loads(raw)
    except Exception:
        return ""
    stack = data if isinstance(data, list) else [data]
    while stack:
        item = stack.pop(0)
        if not isinstance(item, dict):
            continue
        body = item.get("articleBody")
        if isinstance(body, str):
            return _normalize_article_text(body)
        graph = item.get("@graph")
        if isinstance(graph, list):
            stack.extend(graph)
    return ""


def _entry_article_text(entry) -> str:
    candidates = []
    for key in ("content", "content_detail"):
        value = entry.get(key)
        if isinstance(value, list):
            candidates.extend(v.get("value", "") for v in value if isinstance(v, dict))
        elif isinstance(value, dict):
            candidates.append(value.get("value", ""))
    best = max((_normalize_article_text(c) for c in candidates), key=len, default="")
    if len(best) < ARTICLE_MIN_CHARS or _looks_truncated(best):
        return ""
    return best


def _fetch_html(url: str) -> str:
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
        with urlopen(req, timeout=ARTICLE_FETCH_TIMEOUT) as resp:
            ctype = resp.headers.get("content-type", "")
            if "html" not in ctype:
                return ""
            return resp.read(700_000).decode(resp.headers.get_content_charset() or "utf-8", errors="ignore")
    except (HTTPError, URLError, TimeoutError, OSError):
        return ""


def _resolve_biztoc_source_url(url: str) -> str:
    raw = _fetch_html(url)
    if not raw:
        return ""

    canonical = re.search(r'<!--\s*<link\s+rel=["\']canonical["\']\s+href=["\']([^"\']+)["\']', raw, re.I)
    if canonical:
        candidate = html.unescape(canonical.group(1))
        if candidate and _domain(candidate) not in BIZTOC_EXTERNAL_SKIP_DOMAINS:
            return candidate

    for candidate in re.findall(r'href=["\'](https?://[^"\']+)["\']', raw, re.I):
        candidate = html.unescape(candidate)
        domain = _domain(candidate)
        if domain and domain not in BIZTOC_EXTERNAL_SKIP_DOMAINS and not domain.endswith("biztoc.com"):
            return candidate
    return ""


def _fetch_article_text(url: str) -> str:
    domain = _domain(url)
    if domain == "biztoc.com":
        source_url = _resolve_biztoc_source_url(url)
        return _fetch_article_text(source_url) if source_url else ""
    if domain in ARTICLE_SKIP_DOMAINS:
        return ""
    raw = _fetch_html(url)
    if not raw:
        return ""
    parser = _ArticleTextParser()
    try:
        parser.feed(raw)
    except Exception:
        return ""
    text = parser.text()
    return text if len(text) >= ARTICLE_MIN_CHARS else ""


def fetch_feed(name: str, category: str, url: str, fetch_budget: list[int]) -> list[dict]:
    items: list[dict] = []
    try:
        parsed = feedparser.parse(url, agent=USER_AGENT)
    except Exception as e:
        print(f"  {name}: fetch failed — {e}", flush=True)
        return items

    if parsed.bozo and not parsed.entries:
        print(f"  {name}: parse error, no entries", flush=True)
        return items

    for entry in parsed.entries[:40]:
        title = _clean_title(entry.get("title", ""))
        link = entry.get("link", "")
        if not title or not link:
            continue
        if not _keep_headline(title):
            continue
        summary = _clean_summary(entry.get("summary", ""))
        article_text = _entry_article_text(entry)
        text_for_tickers = f"{title} {summary} {article_text[:1000]}"
        tickers = extract_tickers(text_for_tickers)
        should_fetch_article = tickers or _looks_truncated(summary)
        if should_fetch_article and not article_text and fetch_budget[0] > 0:
            fetch_budget[0] -= 1
            article_text = _fetch_article_text(link)
        published = _parse_published(entry) or datetime.now(timezone.utc).isoformat()
        items.append({
            "id":        _hash_url(link),
            "title":     title,
            "link":      link,
            "summary":   summary if summary != title else "",
            "article_text": article_text,
            "source":    name,
            "category":  category,
            "type":      classify(title, category),
            "sentiment": sentiment(title, classify(title, category)),
            "domain":    _domain(link),
            "published": published,
            "tickers":   tickers,
        })
    print(f"  {name}: {len(items)} headlines", flush=True)
    return items


def load_existing() -> list[dict]:
    if not HEADLINES_PATH.exists():
        return []
    try:
        with open(HEADLINES_PATH, "r", encoding="utf-8") as f:
            doc = json.load(f)
        return doc.get("headlines", [])
    except Exception as e:
        print(f"WARN: existing headlines load failed — {e}", flush=True)
        return []


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    print(f"Loaded {len(TICKERS)} ticker symbols, {len(NAME_MAP)} name patterns", flush=True)
    print(f"Fetching {len(FEEDS)} feeds...", flush=True)

    fresh: list[dict] = []
    fetch_budget = [ARTICLE_FETCH_LIMIT]
    for name, category, url in FEEDS:
        fresh.extend(fetch_feed(name, category, url, fetch_budget))
        time.sleep(0.25)

    existing = [
        h for h in load_existing()
        if h.get("source") != "Yahoo Finance" and _keep_headline(h.get("title", ""))
    ]
    seen_ids = {h["id"] for h in existing}
    new_items = [h for h in fresh if h["id"] not in seen_ids]
    print(f"\nFetched {len(fresh)} total ({len(new_items)} new since last run)", flush=True)

    # Backfill any field added after a headline was first written
    for h in existing:
        if not h.get("type"):
            h["type"] = classify(h.get("title", ""), h.get("category", ""))
        if not h.get("sentiment"):
            h["sentiment"] = sentiment(h.get("title", ""), h.get("type", "news"))
        if "article_text" not in h:
            h["article_text"] = ""
        elif h.get("article_text", "").strip() == _normalize_article_text(h.get("summary", "")).strip():
            h["article_text"] = ""

    by_id: dict[str, dict] = {h["id"]: h for h in existing}
    for h in fresh:
        by_id[h["id"]] = h
    combined = sorted(by_id.values(), key=lambda h: h.get("published", ""), reverse=True)
    combined = combined[:MAX_KEEP]

    tagged_pct = (sum(1 for h in combined if h["tickers"]) / max(1, len(combined))) * 100
    print(f"\nFinal: {len(combined)} headlines, {tagged_pct:.0f}% ticker-tagged", flush=True)

    # Build a ticker -> canonical name map for tickers actually present in
    # the feed (used by the UI tooltip). Pick the first name pair that matches.
    tickers_in_feed = {tk for h in combined for tk in h.get("tickers", [])}
    name_lookup = {}
    for name, ticker in NAME_MAP:
        if ticker in tickers_in_feed and ticker not in name_lookup:
            name_lookup[ticker] = name
    print(f"Built ticker_names map for {len(name_lookup)} symbols", flush=True)

    payload = {
        "generated":    datetime.now(timezone.utc).isoformat(),
        "feed_count":   len(FEEDS),
        "ticker_universe_size": len(TICKERS),
        "headlines":    combined,
        "ticker_names": name_lookup,
    }
    HEADLINES_PATH.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {HEADLINES_PATH.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
