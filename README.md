# numichart-news

**A live, self-updating financial news terminal in your browser.**  
Bloomberg-style dense UI. Headlines from 29+ free RSS feeds, auto-tagged with tickers, refreshed by GitHub Actions.

🌐 **Live Dashboard** — served by GitHub Pages:  
**https://Numi2.github.io/numichart-news/**

[![GitHub Pages](https://img.shields.io/badge/GitHub%20Pages-Live-brightgreen?logo=github)](https://Numi2.github.io/numichart-news/)
[![Workflow](https://img.shields.io/github/actions/workflow/status/Numi2/numichart-news/refresh.yml?label=refresh)](https://github.com/Numi2/numichart-news/actions/workflows/refresh.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## What it is

A single-page, high-density news dashboard that:

- Pulls from curated financial RSS feeds (MarketWatch, CNBC, WSJ, FT, Seeking Alpha, PR Newswire, GlobeNewswire, BizToc, SEC EDGAR 8-Ks, etc.)
- Extracts and tags relevant stock tickers using a 1,700+ ticker universe + name/alias matching
- Classifies stories (earnings, M&A, upgrades, FDA, legal, macro…)
- Shows live prices + 7-day sparklines for tickers currently in the feed
- Lets you load **your own book** (drop an .xlsx / paste tickers) and instantly filter the stream to only those names

Everything runs 100% client-side after the two tiny JSON files are fetched. No backend, no login, no tracking.

## Live Demo

Visit the dashboard:

→ **[https://Numi2.github.io/numichart-news/](https://Numi2.github.io/numichart-news/)**

The page auto-refreshes the feed every 60 seconds (client-side poll). New content is published by the backend every ~5 minutes during market hours. Keyboard-driven (press `/` to search, `1`/`2` to switch views, `ESC` to clear).

## Features

- Amber-on-black terminal aesthetic with virtualized scrolling (handles 600+ headlines instantly)
- Monitor ribbon showing the most-mentioned tickers + their price moves
- Source and type filters + full-text search
- **MY BOOK** tab — upload holdings or paste tickers; the feed narrows to your positions
- Direct links to the raw JSON (perfect for scripts, curl, other dashboards, or piping into tools)
- URL params support (`?watchlist=AAPL,TSLA,NVDA` or `?ticker=NVDA`)
- Shift-click any ticker to jump to an external companion desk (if you have one)

## How it works (self-updating)

```
RSS feeds → GitHub Action (target: every ~5 min market hours) → 
  scan/fetch_news.py + fetch_stocks.py → 
  commit docs/data/headlines.json + stocks.json → 
  GitHub Pages serves the static site instantly
```

The workflow is triggered on a 5-minute schedule defined in `.github/workflows/refresh.yml` (GitHub's scheduler is best-effort on the free tier). Data is committed back to the `main` branch under `docs/data/`.

## Direct JSON access (great for automation)

The data is **public, CORS-friendly, and always available**:

- `https://Numi2.github.io/numichart-news/data/headlines.json`
- `https://Numi2.github.io/numichart-news/data/stocks.json`

Example:

```bash
curl -s https://Numi2.github.io/numichart-news/data/headlines.json \
| jq '.headlines | length'
```

The live UI shows the exact current URLs with one-click COPY buttons.

## Local development

```bash
git clone https://github.com/Numi2/numichart-news.git
cd numichart-news

pip install -r requirements.txt
python scan/fetch_news.py
python scan/fetch_stocks.py   # optional, needs yfinance
```

Then open `docs/index.html` directly in a browser (file:// works for the static dashboard).

## Configuration

| What | Where | Notes |
|------|-------|-------|
| Add/remove RSS feeds | `scan/fetch_news.py` → `FEEDS` list | `(name, category, url)` tuples |
| Ticker universe | `scan/universe_tickers.csv` | One ticker per line |
| Company name / alias matching | `scan/universe_names.csv` + `universe_aliases.csv` | `TICKER,Name` |
| Blocklist (words that look like tickers) | `TICKER_BLOCKLIST` in `fetch_news.py` | Prevents false positives |
| Refresh schedule | `.github/workflows/refresh.yml` | GitHub Actions `schedule` every 5 min (`*/5`, best-effort) + `workflow_dispatch` (manual + optional automation) |

## Architecture

```
numichart-news/
├── docs/
│   ├── index.html           # fully self-contained single-file app
│   ├── data/
│   │   ├── headlines.json   # the live tagged feed (written by CI)
│   │   └── stocks.json      # price snapshots for current tickers
│   └── .nojekyll            # tells GitHub Pages: serve as static files
├── scan/
│   ├── fetch_news.py        # RSS + ticker tagging + classification
│   ├── fetch_stocks.py      # yfinance price enrichment
│   └── *.csv                # ticker universe + names/aliases
├── .github/workflows/refresh.yml
└── requirements.txt
```

## Publishing to GitHub Pages (for forks)

This repo is already configured to publish from the `docs/` folder on the `main` branch.

If you fork:

1. Go to your fork → **Settings → Pages**
2. Under "Build and deployment":
   - Source: **Deploy from a branch**
   - Branch: `main`
   - Folder: `/docs`
3. Save. The site will be available at `https://YOURNAME.github.io/numichart-news/`

The 5-minute cadence is defined by the `schedule` trigger in `.github/workflows/refresh.yml`. You can also run it on demand from the Actions tab ("Run workflow") or via the GitHub CLI (`gh workflow run refresh.yml`). `workflow_dispatch` is enabled if you ever want to add an external caller later.

## Not investment advice

This is a news viewer only. Headlines come from third parties. Ticker extraction and classification are heuristic and can be wrong. Always do your own research.

## Performance & privacy

- Virtual scroller: only the visible rows exist in the DOM.
- All watchlist / holdings / filtering logic is 100% local to your browser.
- The only network calls after page load are the two public JSON fetches (and the occasional price tooltip logo).
- The JSON files are the source of truth and safe to consume from any language or tool.

---

Made for terminal addicts who want a fast, dense, no-BS market news surface that just works.
