"""
Stage 2: gather everything the digest needs, using only free sources.

Reads watchlist.json, then for every ticker collects:
  1. price, 7 day percent change, and 52 week high and low from yfinance
  2. a few recent headlines from the Google News RSS feed
  3. an event status worked out purely from date arithmetic

The result is written to data/digest_raw.json, and optionally also inserted
into Postgres when the database environment variables are present. Nothing in
this file costs money to run.

Run it with:
    python scripts/02_gather_market_data.py
"""

import json
import logging
import sys
import time
import urllib.parse
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

# The scripts folder is not an installed package, so add it to the import path
# before importing common. sys.path is the list of folders Python searches for
# modules, and inserting at position 0 puts our folder first.
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402

# Constants, no magic strings in the logic below.
GOOGLE_NEWS_RSS = (
    "https://news.google.com/rss/search"
    "?q={query}&hl=en-US&gl=US&ceid=US:en"
)
# Google News rejects requests with no browser-like User-Agent, so send one.
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
HTTP_TIMEOUT_SECONDS = 20
HEADLINES_PER_TICKER = 3
# Be a polite client. Neither source charges us, so do not hammer either one.
SLEEP_BETWEEN_CALLS_SECONDS = 0.5
PRICE_HISTORY_PERIOD = "1y"
SEVEN_DAY_WINDOW = 7

CREATE_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS {schema};".format(schema=common.DB_SCHEMA)

# The JSONB raw table pattern used by every other project in this workspace:
# source, dataset_id, ingested_at, payload.
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {schema}.{table} (
    source      TEXT NOT NULL,
    dataset_id  TEXT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload     JSONB NOT NULL
);
""".format(schema=common.DB_SCHEMA, table=common.DB_TABLE)

DELETE_SQL = """
DELETE FROM {schema}.{table}
WHERE source = %s AND dataset_id = %s;
""".format(schema=common.DB_SCHEMA, table=common.DB_TABLE)

INSERT_SQL = """
INSERT INTO {schema}.{table} (source, dataset_id, payload)
VALUES (%s, %s, %s);
""".format(schema=common.DB_SCHEMA, table=common.DB_TABLE)


def fetch_price_data(symbol: str) -> Dict[str, Any]:
    """
    Pull price history for one symbol and derive the numbers we display.

    yfinance scrapes Yahoo Finance, so it is free and needs no API key, but it
    is also the flakiest part of this pipeline. Every caller of this function
    must be ready for it to raise.
    """
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    # One download covers both jobs: the last row is the current price and the
    # full year gives us the 52 week range without a second request.
    history = ticker.history(period=PRICE_HISTORY_PERIOD, auto_adjust=False)

    if history is None or history.empty:
        raise ValueError("yfinance returned no price history for {}".format(symbol))

    closes = history["Close"].dropna()
    if closes.empty:
        raise ValueError("yfinance returned no closing prices for {}".format(symbol))

    latest_price = float(closes.iloc[-1])
    latest_date = closes.index[-1]

    # For the 7 day change, find the last close at or before one week ago.
    # Comparing against the index directly handles the fact that markets are
    # shut at weekends, so "7 days ago" is often not a trading day.
    week_ago_cutoff = latest_date - _timedelta_days(SEVEN_DAY_WINDOW)
    earlier = closes[closes.index <= week_ago_cutoff]
    if earlier.empty:
        change_7d_pct: Optional[float] = None
    else:
        baseline = float(earlier.iloc[-1])
        # Guard against a zero baseline, which would be a division by zero.
        change_7d_pct = None if baseline == 0 else round(
            ((latest_price - baseline) / baseline) * 100.0, 2
        )

    return {
        "price": round(latest_price, 2),
        "price_as_of": str(latest_date.date()),
        "change_7d_pct": change_7d_pct,
        "week_52_high": round(float(closes.max()), 2),
        "week_52_low": round(float(closes.min()), 2),
    }


def _timedelta_days(days: int):
    """Small wrapper so the import stays local to where it is used."""
    from datetime import timedelta

    return timedelta(days=days)


def fetch_headlines(symbol: str, company: str) -> List[Dict[str, str]]:
    """
    Fetch a handful of recent headlines from the free Google News RSS feed.

    We search on the company name plus the ticker plus the word stock, which
    cuts down on false positives for short symbols like MU or QS that are also
    ordinary words.
    """
    import feedparser
    import requests

    query = "{} {} stock".format(company, symbol)
    # quote_plus percent-encodes the query so spaces and punctuation are safe
    # to drop into a URL.
    url = GOOGLE_NEWS_RSS.format(query=urllib.parse.quote_plus(query))

    response = requests.get(url, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()

    # feedparser can fetch a URL itself, but handing it the bytes we already
    # downloaded lets us control the headers and the timeout.
    parsed = feedparser.parse(response.content)

    headlines: List[Dict[str, str]] = []
    for entry in parsed.entries[:HEADLINES_PER_TICKER]:
        headlines.append(
            {
                "title": getattr(entry, "title", "").strip(),
                "link": getattr(entry, "link", "").strip(),
                "published": getattr(entry, "published", ""),
                "source": getattr(getattr(entry, "source", None), "title", ""),
            }
        )
    return headlines


def build_ticker_record(
    symbol: str,
    company: str,
    events: List[Dict[str, Any]],
    today: date,
) -> Dict[str, Any]:
    """
    Assemble one ticker's slice of the digest.

    Failures in either free data source are recorded in an errors list rather
    than raised, so a single delisted symbol or a news hiccup never takes down
    the whole weekly run.
    """
    record: Dict[str, Any] = {
        "symbol": symbol,
        "company": company,
        "price": None,
        "price_as_of": None,
        "change_7d_pct": None,
        "week_52_high": None,
        "week_52_low": None,
        "headlines": [],
        "events": [common.classify_event(event, today) for event in events],
        "errors": [],
    }

    try:
        record.update(fetch_price_data(symbol))
    except Exception as exc:  # noqa: BLE001
        # yfinance raises a wide and undocumented range of exceptions from deep
        # inside its HTTP and parsing layers, so a broad catch here is the only
        # way to keep one bad symbol from ending the run. The error is logged
        # and carried in the record rather than swallowed.
        message = "price lookup failed: {}".format(exc)
        logging.warning("%s %s", symbol, message)
        record["errors"].append(message)

    time.sleep(SLEEP_BETWEEN_CALLS_SECONDS)

    try:
        record["headlines"] = fetch_headlines(symbol, company)
        if not record["headlines"]:
            logging.info("%s returned no headlines this week.", symbol)
    except Exception as exc:  # noqa: BLE001
        # Same reasoning as above: requests and feedparser between them can
        # raise network, SSL, and parser errors that do not share a base class.
        message = "headline lookup failed: {}".format(exc)
        logging.warning("%s %s", symbol, message)
        record["errors"].append(message)

    record["urgency"] = common.worst_urgency(record["events"])

    time.sleep(SLEEP_BETWEEN_CALLS_SECONDS)
    return record


def gather(watchlist: Dict[str, Any], today: date) -> Dict[str, Any]:
    """Walk the whole watchlist and build the complete digest object."""
    sectors_out: List[Dict[str, Any]] = []

    for sector in watchlist.get("sectors", []):
        sector_name = sector.get("name", "Unknown")
        logging.info("Gathering sector: %s", sector_name)

        sector_events = [
            common.classify_event(event, today)
            for event in sector.get("sector_events", [])
        ]

        ticker_records: List[Dict[str, Any]] = []
        for entry in sector.get("tickers", []):
            symbol = entry.get("symbol", "").strip().upper()
            company = entry.get("company", symbol)
            if not symbol:
                logging.warning("Skipping an entry in %s with no symbol.", sector_name)
                continue

            logging.info("  %s (%s)", symbol, company)
            ticker_records.append(
                build_ticker_record(symbol, company, entry.get("events", []), today)
            )

        # The sector color later depends on both its own events and the worst
        # event across its tickers, so combine them here.
        combined = list(sector_events)
        for record in ticker_records:
            combined.extend(record["events"])

        sectors_out.append(
            {
                "name": sector_name,
                "sector_events": sector_events,
                "tickers": ticker_records,
                "urgency": common.worst_urgency(combined),
            }
        )

    return {
        "run_date": today.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sectors": sectors_out,
    }


def ensure_table(conn) -> None:
    """Create the schema and the raw table if they do not exist yet."""
    cursor = conn.cursor()
    cursor.execute(CREATE_SCHEMA_SQL)
    cursor.execute(CREATE_TABLE_SQL)
    conn.commit()
    cursor.close()


def delete_existing_dataset(conn, source: str, dataset_id: str) -> None:
    """
    Remove any rows already stored for this source and dataset.

    This is what makes the write idempotent: rerunning the same Sunday twice
    replaces that Sunday's rows instead of doubling them up.
    """
    cursor = conn.cursor()
    cursor.execute(DELETE_SQL, (source, dataset_id))
    logging.info("Deleted %s existing rows for dataset %s.", cursor.rowcount, dataset_id)
    conn.commit()
    cursor.close()


def write_to_postgres(digest: Dict[str, Any]) -> None:
    """
    Store one row per ticker in the raw JSONB table.

    Skipped automatically when the database variables are absent, which is the
    case on the GitHub Actions runner since it cannot reach a local Postgres.
    """
    if not common.db_is_configured():
        logging.info("Database variables not set, skipping the Postgres write.")
        return

    dataset_id = digest["run_date"]

    # Build the full list of rows first so executemany can send them in one go.
    rows = []
    for sector_name, ticker in common.iter_tickers(digest):
        payload = dict(ticker)
        payload["sector"] = sector_name
        payload["run_date"] = dataset_id
        # pg8000 wants the JSONB value as a JSON string, not a Python dict.
        rows.append((common.DB_SOURCE, dataset_id, json.dumps(payload)))

    conn = None
    try:
        conn = common.get_conn()
        ensure_table(conn)
        delete_existing_dataset(conn, common.DB_SOURCE, dataset_id)

        cursor = conn.cursor()
        for start in range(0, len(rows), common.BATCH_SIZE):
            batch = rows[start : start + common.BATCH_SIZE]
            cursor.executemany(INSERT_SQL, batch)
        conn.commit()
        cursor.close()
        logging.info(
            "Inserted %s rows into %s.%s.", len(rows), common.DB_SCHEMA, common.DB_TABLE
        )
    except ImportError:
        logging.warning("pg8000 is not installed, skipping the Postgres write.")
    except Exception as exc:  # noqa: BLE001
        # pg8000 surfaces server errors as several unrelated classes. The
        # digest matters more than the archive, so log and carry on.
        logging.warning("Postgres write failed, continuing anyway: %s", exc)
        if conn is not None:
            conn.rollback()
    finally:
        if conn is not None:
            conn.close()


def main() -> int:
    common.setup_logging()
    common.load_env()

    today = date.today()
    watchlist = common.load_watchlist()

    digest = gather(watchlist, today)

    out_path = common.data_path(common.RAW_FILENAME)
    common.write_json(out_path, digest)
    logging.info("Wrote %s", out_path)

    write_to_postgres(digest)

    # A short console summary so a manual run tells you at a glance whether
    # the free sources cooperated.
    total = 0
    failed = 0
    for _sector_name, ticker in common.iter_tickers(digest):
        total += 1
        if ticker["errors"]:
            failed += 1
    logging.info("Gathered %s tickers, %s had at least one problem.", total, failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
