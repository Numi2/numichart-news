"""
fetch_stocks.py — for every ticker that appears in the current headlines.json,
pull recent price data via yfinance and write docs/data/stocks.json with:

  { TICKER: {
      "price":      current close,
      "prev_close": previous close,
      "change":     price - prev_close (absolute),
      "change_pct": (price/prev_close - 1) * 100,
      "week_closes": [last 7 closes for sparkline],
      "wk_low":     min of last 7 closes,
      "wk_high":    max of last 7 closes,
    } }

Runs after fetch_news.py in the workflow.
"""
import json
import socket
import warnings
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

socket.setdefaulttimeout(15)
warnings.filterwarnings("ignore")

ROOT = Path(__file__).parent.parent
DATA = ROOT / "docs" / "data"
HEADLINES = DATA / "headlines.json"
OUT = DATA / "stocks.json"

BATCH_SIZE = 80     # tickers per yfinance.download call


def collect_tickers() -> list[str]:
    if not HEADLINES.exists():
        return []
    try:
        doc = json.loads(HEADLINES.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"WARN: could not read headlines.json — {e}", flush=True)
        return []
    seen = set()
    for h in doc.get("headlines", []):
        for t in h.get("tickers") or []:
            if t and t.isascii() and 1 <= len(t) <= 6:
                seen.add(t.upper())
    return sorted(seen)


def fetch_batch(tickers: list[str]) -> dict:
    """Return {ticker: row} dict for one batch using yfinance bulk download."""
    out = {}
    if not tickers:
        return out
    try:
        df = yf.download(
            " ".join(tickers),
            period="10d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
            group_by="ticker",
        )
    except Exception as e:
        print(f"  batch {tickers[0]}..{tickers[-1]}: download failed — {e}", flush=True)
        return out

    if df is None or df.empty:
        return out

    # When only one ticker is passed, df doesn't have a MultiIndex
    multi = hasattr(df.columns, "levels")

    for tk in tickers:
        try:
            if multi:
                if tk not in df.columns.get_level_values(0):
                    continue
                sub = df[tk]
            else:
                sub = df
            closes = sub["Close"].dropna().tolist() if "Close" in sub else []
            if len(closes) < 2:
                continue
            closes = [round(float(x), 2) for x in closes][-7:]
            curr = closes[-1]
            prev = closes[-2]
            out[tk] = {
                "price":       curr,
                "prev_close":  prev,
                "change":      round(curr - prev, 2),
                "change_pct":  round((curr / prev - 1) * 100, 2) if prev else 0.0,
                "week_closes": closes,
                "wk_low":      round(min(closes), 2),
                "wk_high":     round(max(closes), 2),
            }
        except Exception:
            continue
    return out


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    tickers = collect_tickers()
    print(f"fetch_stocks: {len(tickers)} unique tickers in current feed", flush=True)
    if not tickers:
        OUT.write_text(json.dumps({"generated": datetime.now(timezone.utc).isoformat(), "stocks": {}}), encoding="utf-8")
        return

    stocks = {}
    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i : i + BATCH_SIZE]
        chunk = fetch_batch(batch)
        stocks.update(chunk)
        print(f"  batch {i+1}-{i+len(batch)}: {len(chunk)} resolved", flush=True)

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "stocks":    stocks,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)} ({len(stocks)} tickers with prices)", flush=True)


if __name__ == "__main__":
    main()
