"""
03_ingest_f1_historical.py
==========================
Ingest historical Formula 1 data from the Jolpica API into PostgreSQL.

FIX FROM V1: PAGINATION
------------------------
The Jolpica API caps responses at 100 rows regardless of what you pass
as ?limit=. To get all data, we must paginate using ?limit=100&offset=0,
then ?limit=100&offset=100, etc. The MRData.total field tells us how
many total rows exist.

This version adds a api_get_all_pages() helper that automatically
handles pagination for any endpoint, so every fetch function gets
complete data.

WHAT WE'RE PULLING:
-------------------
1. Drivers     — all ~860 drivers who have ever entered an F1 race
2. Constructors — all ~210 teams/constructorsS
3. Races       — every race ever held (~1,100+)
4. Results     — finishing position, grid, points, status (~26,000+ rows)
5. Qualifying  — Q1/Q2/Q3 times (~1994 onward)
6. Standings   — end-of-season driver championship standings

RATE LIMITING:
--------------
Jolpica allows 200 requests per hour. We pace at one request every
20 seconds (~180/hour). Automatic retry on HTTP 429.

USAGE:
------
  python 03_ingest_f1_historical.py
"""

import json
import os
import sys
import time
import requests
import pg8000

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ===========================================================================
# CONFIG
# ===========================================================================
API_BASE = "http://api.jolpi.ca/ergast/f1"
PAGE_SIZE = 100       # API max per response
REQUEST_DELAY = 20    # Seconds between calls
RETRY_DELAY = 60      # Seconds to wait on 429
MAX_RETRIES = 3


# ===========================================================================
# HELPERS
# ===========================================================================
def load_env(path: str) -> None:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing .env file at: {path}")
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def get_conn():
    return pg8000.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        database=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


def api_get(url: str) -> dict:
    """
    Single API request with retry logic for rate limiting and errors.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, timeout=30)

            if r.status_code == 429:
                logger.warning(f"    Rate limited! Waiting {RETRY_DELAY}s (attempt {attempt}/{MAX_RETRIES})")
                time.sleep(RETRY_DELAY)
                continue

            r.raise_for_status()
            return r.json()

        except requests.exceptions.RequestException as e:
            if attempt < MAX_RETRIES:
                logger.warning(f"    Request failed: {e}. Retrying in 10s (attempt {attempt}/{MAX_RETRIES})")
                time.sleep(10)
            else:
                raise

    raise RuntimeError(f"Failed after {MAX_RETRIES} attempts: {url}")


def api_get_all_pages(base_url: str, table_key: str, array_key: str) -> list:
    """
    Paginate through a Jolpica endpoint and collect ALL rows.

    HOW PAGINATION WORKS:
    The Jolpica API returns a max of 100 rows per call. The response
    includes a 'total' field telling you how many rows exist. To get
    them all, we:
      1. First call: ?limit=100&offset=0 → get rows 0-99
      2. Check: did we get all rows? (offset + len(results) >= total)
      3. If not: call again with offset=100, then 200, etc.
      4. Combine all pages into one list

    Parameters:
      base_url:  The endpoint WITHOUT query params, e.g.
                 "http://api.jolpi.ca/ergast/f1/drivers.json"
      table_key: The key in MRData that holds the table, e.g. "DriverTable"
      array_key: The key inside that table with the array, e.g. "Drivers"

    WHY IS THIS A GENERIC FUNCTION?
    Every Jolpica endpoint follows the same pagination pattern, just with
    different table/array keys. Making this generic means we write the
    pagination logic once and reuse it for drivers, results, qualifying, etc.
    """
    all_items = []
    offset = 0

    while True:
        sep = "&" if "?" in base_url else "?"
        url = f"{base_url}{sep}limit={PAGE_SIZE}&offset={offset}"
        data = api_get(url)

        total = int(data["MRData"]["total"])
        items = data["MRData"][table_key][array_key]
        all_items.extend(items)

        offset += PAGE_SIZE

        # Are we done?
        if offset >= total:
            break

        # Be polite — delay between pagination calls too
        time.sleep(REQUEST_DELAY)

    return all_items


# ===========================================================================
# DATABASE
# ===========================================================================
def ensure_table(conn):
    ddl = """
    CREATE TABLE IF NOT EXISTS sports_raw.f1_rows (
        source      TEXT        NOT NULL,
        dataset_id  TEXT        NOT NULL,
        ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        payload     JSONB       NOT NULL
    );
    """
    cur = conn.cursor()
    cur.execute(ddl)
    conn.commit()
    cur.close()


def delete_existing_dataset(conn, source: str, dataset_id: str):
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM sports_raw.f1_rows WHERE source = %s AND dataset_id = %s;",
        (source, dataset_id),
    )
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    if deleted:
        logger.info(f"  Cleared {deleted} existing rows for {source}/{dataset_id}")


def ingest_rows(conn, source: str, dataset_id: str, rows: list,
                batch_size: int = 5000) -> int:
    insert_sql = """
        INSERT INTO sports_raw.f1_rows (source, dataset_id, payload)
        VALUES (%s, %s, %s::jsonb)
    """
    cur = conn.cursor()
    batch = []
    n = 0

    for row in rows:
        batch.append((source, dataset_id, json.dumps(row)))

        if len(batch) >= batch_size:
            cur.executemany(insert_sql, batch)
            conn.commit()
            n += len(batch)
            batch = []

    if batch:
        cur.executemany(insert_sql, batch)
        conn.commit()
        n += len(batch)

    cur.close()
    return n


# ===========================================================================
# DATA FETCHERS
# ===========================================================================

def fetch_all_drivers() -> list:
    """Fetch all F1 drivers ever, with automatic pagination."""
    logger.info("  Fetching all drivers...")
    drivers = api_get_all_pages(
        f"{API_BASE}/drivers.json",
        table_key="DriverTable",
        array_key="Drivers"
    )
    logger.info(f"    Got {len(drivers)} drivers")
    return drivers


def fetch_all_constructors() -> list:
    """Fetch all F1 constructors ever, with automatic pagination."""
    logger.info("  Fetching all constructors...")
    constructors = api_get_all_pages(
        f"{API_BASE}/constructors.json",
        table_key="ConstructorTable",
        array_key="Constructors"
    )
    logger.info(f"    Got {len(constructors)} constructors")
    return constructors


def fetch_season_races(season: int) -> list:
    """Fetch the race calendar for a given season (paginated)."""
    races = api_get_all_pages(
        f"{API_BASE}/{season}.json",
        table_key="RaceTable",
        array_key="Races"
    )
    return races


def fetch_season_results(season: int) -> list:
    """
    Fetch ALL race results for a season, with pagination and flattening.

    The API returns results nested inside Race objects. We flatten so
    each row = one driver's result in one race, with race metadata
    attached. This makes SQL analysis much easier.

    PAGINATION NOTE: For results, pagination is across the RACE level,
    not the result level. A season with 24 races might need multiple
    pages since each race has ~20 results nested inside it, but the
    API counts 100 top-level Race objects, not individual results.
    However, since most seasons have <30 races, one page usually
    suffices for the Race array. The results are nested INSIDE each
    race, so they all come along. But to be safe we still paginate.
    """
    races = api_get_all_pages(
        f"{API_BASE}/{season}/results.json",
        table_key="RaceTable",
        array_key="Races"
    )

    flat_results = []
    for race in races:
        race_meta = {
            "season": race.get("season"),
            "round": race.get("round"),
            "raceName": race.get("raceName"),
            "circuitId": race.get("Circuit", {}).get("circuitId"),
            "circuitName": race.get("Circuit", {}).get("circuitName"),
            "date": race.get("date"),
        }
        for result in race.get("Results", []):
            row = {**race_meta, **result}
            flat_results.append(row)

    return flat_results


def fetch_season_qualifying(season: int) -> list:
    """
    Fetch ALL qualifying results for a season (paginated + flattened).
    Empty for pre-1994 seasons — that's expected.
    """
    races = api_get_all_pages(
        f"{API_BASE}/{season}/qualifying.json",
        table_key="RaceTable",
        array_key="Races"
    )

    flat_qualifying = []
    for race in races:
        race_meta = {
            "season": race.get("season"),
            "round": race.get("round"),
            "raceName": race.get("raceName"),
            "circuitId": race.get("Circuit", {}).get("circuitId"),
            "date": race.get("date"),
        }
        for qual in race.get("QualifyingResults", []):
            row = {**race_meta, **qual}
            flat_qualifying.append(row)

    return flat_qualifying


def fetch_season_standings(season: int) -> list:
    """Fetch end-of-season driver championship standings."""
    url = f"{API_BASE}/{season}/driverStandings.json?limit={PAGE_SIZE}&offset=0"
    data = api_get(url)

    standings_lists = data["MRData"]["StandingsTable"]["StandingsLists"]
    if not standings_lists:
        return []

    standings = standings_lists[0].get("DriverStandings", [])
    for s in standings:
        s["season"] = str(season)

    return standings


# ===========================================================================
# MAIN
# ===========================================================================
def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.join(script_dir, os.pardir)
    env_path = os.path.join(project_dir, ".env")
    load_env(env_path)

    conn = get_conn()
    ensure_table(conn)

    source = "jolpica"
    current_year = 2025

    logger.info("=" * 60)
    logger.info("  F1 HISTORICAL DATA INGESTION (v2 — with pagination)")
    logger.info("=" * 60)

    # ------------------------------------------------------------------
    # Step 1: Drivers
    # ------------------------------------------------------------------
    dataset_id = "drivers"
    delete_existing_dataset(conn, source, dataset_id)
    drivers = fetch_all_drivers()
    n = ingest_rows(conn, source, dataset_id, drivers)
    logger.info(f"  -> {n} drivers ingested\n")
    time.sleep(REQUEST_DELAY)

    # ------------------------------------------------------------------
    # Step 2: Constructors
    # ------------------------------------------------------------------
    dataset_id = "constructors"
    delete_existing_dataset(conn, source, dataset_id)
    constructors = fetch_all_constructors()
    n = ingest_rows(conn, source, dataset_id, constructors)
    logger.info(f"  -> {n} constructors ingested\n")
    time.sleep(REQUEST_DELAY)

    # ------------------------------------------------------------------
    # Step 3: Per-season data (races, results, qualifying, standings)
    # ------------------------------------------------------------------
    for ds in ["races", "results", "qualifying", "standings"]:
        delete_existing_dataset(conn, source, ds)

    total_races = 0
    total_results = 0
    total_qualifying = 0
    total_standings = 0
    errors = 0

    seasons = list(range(1950, current_year + 1))
    logger.info(f"\n  Processing {len(seasons)} seasons (1950-{current_year})...")
    logger.info(f"  NOTE: This will take longer than v1 due to pagination.")
    logger.info(f"  Each season makes 4+ API calls with 20s delays.\n")

    for i, season in enumerate(seasons, start=1):
        try:
            # Races
            races = fetch_season_races(season)
            n = ingest_rows(conn, source, "races", races)
            total_races += n
            time.sleep(REQUEST_DELAY)

            # Results (may need multiple pages for modern seasons)
            results = fetch_season_results(season)
            n = ingest_rows(conn, source, "results", results)
            total_results += n
            time.sleep(REQUEST_DELAY)

            # Qualifying (empty for pre-1994, may need pages for modern)
            qualifying = fetch_season_qualifying(season)
            n = ingest_rows(conn, source, "qualifying", qualifying)
            total_qualifying += n
            time.sleep(REQUEST_DELAY)

            # Standings (always < 100 drivers, one page is enough)
            standings = fetch_season_standings(season)
            n = ingest_rows(conn, source, "standings", standings)
            total_standings += n
            time.sleep(REQUEST_DELAY)

        except Exception as e:
            errors += 1
            logger.info(f"  ERROR on season {season}: {e}")

        # Progress every 5 seasons (more frequent since this takes longer)
        if i % 1 == 0:
            logger.info(f"  Progress: {i}/{len(seasons)} seasons | races={total_races} results={total_results} qualifying={total_qualifying} standings={total_standings} errors={errors}")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    logger.info(f"\n{'='*60}")
    logger.info(f"  INGESTION COMPLETE")
    logger.info(f"{'='*60}")
    logger.info(f"  Drivers:      {len(drivers):,}")
    logger.info(f"  Constructors: {len(constructors):,}")
    logger.info(f"  Races:        {total_races:,}")
    logger.info(f"  Results:      {total_results:,}")
    logger.info(f"  Qualifying:   {total_qualifying:,}")
    logger.info(f"  Standings:    {total_standings:,}")
    logger.info(f"  Errors:       {errors}")
    logger.info(f"{'='*60}")

    conn.close()


if __name__ == "__main__":
    main()