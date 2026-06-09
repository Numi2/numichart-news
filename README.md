# numichart-news

Live financial headline aggregator. Pulls from a curated set of free RSS feeds (MarketWatch, Yahoo Finance, CNBC, Investing.com, Seeking Alpha, SEC EDGAR 8-Ks, PR Newswire, BusinessWire, Reuters, BizToc), tags each story with relevant tickers, and renders a chronological stream.

A GitHub Action refreshes the feed every 10 minutes during US market hours, every 30 minutes off-hours, and hourly on weekends. The result is committed to `docs/data/headlines.json` and served by GitHub Pages.

## Architecture

```
numichart-news/
├── docs/
│   ├── index.html              # static dashboard (dark, single page)
│   └── data/headlines.json     # the feed (refreshed by CI)
├── scan/
│   ├── fetch_news.py           # RSS scraper + ticker tagger
│   └── universe_tickers.csv    # ticker set for tagging
├── .github/workflows/refresh.yml
└── requirements.txt
```

## Local development

```bat
pip install -r requirements.txt
python scan/fetch_news.py
```

Open `docs/index.html` in a browser (the page reads `data/headlines.json` via a relative path; opening directly with `file://` should work in most browsers).

The UI is a dense, Bloomberg-terminal-style single-page dashboard:
- Amber-on-black, high information density, monospace throughout
- Live updating (polls the JSON every 60s), virtualized scrolling list for instant filtering even with hundreds of headlines
- Monitor ribbon with most-mentioned tickers + live prices/changes pulled from the companion stocks snapshot
- Command-line style search + source/type filters, keyboard-driven (`/` focuses search, `1`/`2` switch views, `ESC` clears)
- "MY BOOK" tab lets you drop a holdings .xlsx / paste tickers locally; the feed then narrows to only those names

## Direct JSON access (fetch from anywhere)

The underlying data is **always directly fetchable** as plain JSON — no scraping, no auth, no CORS issues on GitHub Pages:

- `docs/data/headlines.json` — the full tagged feed (used by the UI)
- `docs/data/stocks.json` — latest prices + 7d closes for every ticker currently appearing in headlines

From curl, Python, other dashboards, or another terminal:

```bash
curl -s https://YOURNAME.github.io/numichart-news/data/headlines.json | jq '.headlines | length'
```

In the live terminal UI the exact current URLs are shown in the top status bar with one-click COPY buttons so you can instantly grab them for scripts.

## Configuration

- **Add or remove feeds**: edit `FEEDS` in `scan/fetch_news.py`. Each entry is `(display_name, category, url)`.
- **Adjust ticker tagging**: `scan/universe_tickers.csv` is one ticker per line. `TICKER_BLOCKLIST` in the scraper filters out common English words that look like tickers (THE, FOR, ON, etc).
- **Cron frequency**: `.github/workflows/refresh.yml` defines three schedules — market hours, off-hours, weekends.

## Adding new sources

Most financial RSS feeds work out of the box with `feedparser`. To add one:

1. Find the feed URL (usually linked from a publication's footer or `/rss/` page).
2. Append a tuple to `FEEDS`.
3. Pick a category — used for color-coded source chips in the UI.

## Not investment advice

This is a news viewer. Headlines come from third parties; tickers are extracted heuristically and may be wrong. Do your own research.

## Performance & data notes

- The dashboard uses a virtual scroller — only the visible ~40–60 rows exist in the DOM at any time. Filtering and typing feel instant even at 600+ headlines.
- All filtering, watchlist logic, and holdings parsing happens 100% client-side. Nothing leaves your machine except the two public JSON fetches.
- The JSON files are the source of truth and are safe to consume from any tool or language.
