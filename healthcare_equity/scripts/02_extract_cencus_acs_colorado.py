"""
02_extract_census_acs_colorado.py
==================================
Extracts Colorado-specific Census ACS variables from the existing
health_raw.census_rows table and writes them to health_raw.hdi_rows
for use in the Desert Index computation.

WHY A SEPARATE EXTRACTION SCRIPT?
-----------------------------------
You already have Census ACS data ingested nationally in health_raw.census_rows
from 03_ingest_census_acs_raw.py. That script pulled total population by
tract. But the Desert Index needs two additional variables:

  1. Uninsured rate — from ACS table B27010 (Health Insurance Coverage)
  2. Poverty rate — from ACS table B17001 (Poverty Status)

This script fetches those specific variables from the Census API for
Colorado tracts only, then writes them to health_raw.hdi_rows using the
standard JSONB payload pattern. This keeps the existing census_rows table
unchanged while adding the specific variables we need.

CENSUS ACS TABLE NUMBERS EXPLAINED
------------------------------------
The Census Bureau organizes data into "tables" with codes like B27010.
The letter prefix tells you what kind of table it is:
  B = Base table (most detailed)
  C = Collapsed table (less detail, fewer cells)
  S = Subject table (pre-computed percentages)

The number identifies the topic:
  B27010 = Health Insurance Coverage Status
  B17001 = Poverty Status in the Past 12 Months

Within each table, individual cells have suffixes:
  B27010_001E = Total population for insurance status
  B27010_017E = Uninsured population (under 19)
  B27010_033E = Uninsured population (19-64)
  B27010_050E = Uninsured population (65+)
  (The 'E' suffix means 'Estimate' — there's also 'M' for margin of error)

For poverty:
  B17001_001E = Total population for poverty determination
  B17001_002E = Population below poverty level
"""

import json
import os
import sys
import requests
import pg8000

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

STATE_CONFIG = {
    "colorado": {
        "fips": "08",
        "abbreviation": "CO",
        "name": "Colorado",
    },
}

ACTIVE_STATE = "colorado"

# Census API base URL — no API key required for small requests, but
# you may hit rate limits. Add a Census API key to .env as CENSUS_API_KEY
# if you run into "too many requests" errors.
CENSUS_BASE = "https://api.census.gov/data"
ACS_YEAR = "2022"  # Most recent 5-year ACS with complete data

SOURCE = "census_acs_extract"
DATASET_ID = f"acs5_{ACS_YEAR}_tract_insurance_poverty"

# The specific ACS variables we need for the Desert Index.
# Each variable code maps to a human-readable description.
ACS_VARIABLES = {
    # --- Health Insurance (Table B27010) ---
    # Total population for insurance universe
    "B27010_001E": "insurance_universe_total",

    # Uninsured counts by age group — we sum these for total uninsured
    "B27010_017E": "uninsured_under_19",
    "B27010_033E": "uninsured_19_to_64",
    "B27010_050E": "uninsured_65_plus",

    # --- Poverty Status (Table B17001) ---
    # Total population for whom poverty status is determined
    # (slightly different from total population because some people
    # like those in group quarters are excluded from the poverty universe)
    "B17001_001E": "poverty_universe_total",

    # Population below poverty level
    "B17001_002E": "population_below_poverty",

    # --- Total Population (Table B01003) ---
    "B01003_001E": "total_population",
}


# ---------------------------------------------------------------------------
# Standard pipeline utilities
# ---------------------------------------------------------------------------

def load_env(path: str) -> None:
    """Reads a .env file and sets environment variables."""
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
    """Returns a pg8000 connection to the projects database."""
    return pg8000.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        database=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


# ---------------------------------------------------------------------------
# Census API fetching
# ---------------------------------------------------------------------------

def fetch_acs_variables(year: str, state_fips: str, variables: list) -> list:
    """
    Fetches ACS 5-year estimates for specified variables at the tract level
    for all tracts in a given state.

    The Census API returns data as a list of lists:
      Row 0 = headers: ["B27010_001E", "B17001_001E", ..., "state", "county", "tract"]
      Row 1+ = data:   ["5432", "4100", ..., "08", "001", "000100"]

    We call the API once per county to stay within Census API limits.
    Alternatively, we can request all tracts in the state at once if the
    variable list is short enough.

    WHY ACS 5-YEAR INSTEAD OF 1-YEAR?
    The 5-year estimates combine 60 months of data, which gives reliable
    estimates even for small tracts with low populations. The 1-year
    estimates are only available for areas with 65,000+ population, which
    excludes most census tracts. For tract-level analysis, 5-year is the
    only option.
    """
    var_string = ",".join(variables)

    # Try fetching all tracts in the state at once
    url = (
        f"{CENSUS_BASE}/{year}/acs/acs5"
        f"?get={var_string}"
        f"&for=tract:*"
        f"&in=state:{state_fips}"
    )

    # Add API key if available (helps avoid rate limiting)
    api_key = os.environ.get("CENSUS_API_KEY")
    if api_key:
        url += f"&key={api_key}"

    logger.info(f"Fetching ACS data: {url[:120]}...")

    r = requests.get(url, timeout=120)
    r.raise_for_status()
    data = r.json()

    logger.info(f"Received {len(data) - 1} tract rows")
    return data


def parse_acs_response(data: list, variable_map: dict) -> list:
    """
    Converts the Census API response into a list of tract-level dictionaries
    with computed rates.

    The Census API returns raw counts — we need to compute rates ourselves:
      uninsured_rate = total_uninsured / insurance_universe_total
      poverty_rate = population_below_poverty / poverty_universe_total
    """
    headers = data[0]
    rows = data[1:]

    results = []

    for row in rows:
        record = dict(zip(headers, row))

        # Build the 11-digit tract GEOID
        state = record.get("state", "").zfill(2)
        county = record.get("county", "").zfill(3)
        tract = record.get("tract", "").zfill(6)
        geoid = f"{state}{county}{tract}"
        county_fips = f"{state}{county}"

        # Parse the raw counts — Census returns these as strings
        def safe_int(key):
            val = record.get(key, "")
            if val is None or str(val).strip() in ("", "null", "-"):
                return None
            try:
                return int(val)
            except (ValueError, TypeError):
                return None

        # Insurance variables
        ins_total = safe_int("B27010_001E")
        unins_under19 = safe_int("B27010_017E") or 0
        unins_19_64 = safe_int("B27010_033E") or 0
        unins_65plus = safe_int("B27010_050E") or 0
        total_uninsured = unins_under19 + unins_19_64 + unins_65plus

        # Poverty variables
        pov_total = safe_int("B17001_001E")
        pov_below = safe_int("B17001_002E")

        # Total population
        total_pop = safe_int("B01003_001E")

        # Compute rates — avoid division by zero for tracts with no population
        uninsured_rate = None
        if ins_total and ins_total > 0:
            uninsured_rate = round(total_uninsured / ins_total, 4)

        poverty_rate = None
        if pov_total and pov_total > 0 and pov_below is not None:
            poverty_rate = round(pov_below / pov_total, 4)

        results.append({
            "tract_geoid": geoid,
            "county_fips": county_fips,
            "state_fips": state,
            "total_population": total_pop,
            "insurance_universe": ins_total,
            "total_uninsured": total_uninsured,
            "uninsured_rate": uninsured_rate,
            "poverty_universe": pov_total,
            "population_below_poverty": pov_below,
            "poverty_rate": poverty_rate,
            "acs_year": ACS_YEAR,
        })

    return results


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------

def delete_existing_dataset(conn, source: str, dataset_id: str, state_code: str):
    """Removes existing Census ACS extract for re-ingestion."""
    cur = conn.cursor()
    cur.execute(
        """DELETE FROM health_raw.hdi_rows
           WHERE source = %s AND dataset_id = %s AND state_code = %s;""",
        (source, dataset_id, state_code),
    )
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    logger.info(f"Deleted {deleted} existing rows for {source}/{dataset_id}/{state_code}")


def ingest_acs_rows(conn, source: str, dataset_id: str, state_code: str,
                    tract_data: list, batch_size: int = 5000):
    """
    Inserts tract-level ACS data into health_raw.hdi_rows using the
    standard JSONB payload pattern.
    """
    insert_sql = """
        INSERT INTO health_raw.hdi_rows (source, dataset_id, state_code, payload)
        VALUES (%s, %s, %s, %s::jsonb)
    """

    cur = conn.cursor()
    batch = []
    n = 0

    for tract in tract_data:
        batch.append((source, dataset_id, state_code, json.dumps(tract)))

        if len(batch) >= batch_size:
            cur.executemany(insert_sql, batch)
            conn.commit()
            n += len(batch)
            logger.info(f"Inserted {n} rows...")
            batch = []

    if batch:
        cur.executemany(insert_sql, batch)
        conn.commit()
        n += len(batch)

    cur.close()
    logger.info(f"Done. Inserted {n} tract ACS rows")
    return n


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(script_dir, "..", ".env")
    load_env(env_path)

    state = STATE_CONFIG[ACTIVE_STATE]
    logger.info(f"Extracting Census ACS variables for: {state['name']}")
    logger.info(f"ACS year: {ACS_YEAR} (5-year estimates)")
    logger.info(f"Variables: {', '.join(ACS_VARIABLES.keys())}")
    logger.info()

    # Step 1: Fetch ACS data from Census API
    variable_codes = list(ACS_VARIABLES.keys())
    raw_data = fetch_acs_variables(ACS_YEAR, state["fips"], variable_codes)

    # Step 2: Parse into tract-level records with computed rates
    tract_data = parse_acs_response(raw_data, ACS_VARIABLES)

    if not tract_data:
        logger.warning("WARNING: No tract data returned. Check state FIPS and ACS year.")
        return

    # Print summary statistics
    unins_rates = [t["uninsured_rate"] for t in tract_data if t["uninsured_rate"] is not None]
    pov_rates = [t["poverty_rate"] for t in tract_data if t["poverty_rate"] is not None]

    logger.info(f"\nSummary for {state['name']} ({len(tract_data)} tracts):")
    logger.info(f"  Uninsured rate:")
    logger.info(f"    Min:    {min(unins_rates):.1%}")
    logger.info(f"    Max:    {max(unins_rates):.1%}")
    logger.info(f"    Median: {sorted(unins_rates)[len(unins_rates)//2]:.1%}")
    logger.info(f"    Mean:   {sum(unins_rates)/len(unins_rates):.1%}")
    logger.info(f"  Poverty rate:")
    logger.info(f"    Min:    {min(pov_rates):.1%}")
    logger.info(f"    Max:    {max(pov_rates):.1%}")
    logger.info(f"    Median: {sorted(pov_rates)[len(pov_rates)//2]:.1%}")
    logger.info(f"    Mean:   {sum(pov_rates)/len(pov_rates):.1%}")

    # Step 3: Write to database
    conn = get_conn()
    delete_existing_dataset(conn, SOURCE, DATASET_ID, state["abbreviation"])
    count = ingest_acs_rows(conn, SOURCE, DATASET_ID,
                            state["abbreviation"], tract_data)

    logger.info(f"\nComplete: {count} Colorado tracts ingested into health_raw.hdi_rows")
    conn.close()


if __name__ == "__main__":
    main()