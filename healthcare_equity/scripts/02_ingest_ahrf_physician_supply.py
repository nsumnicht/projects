"""
02_ingest_ahrf_physician_supply.py
===================================
Downloads the HRSA Area Health Resources Files (AHRF) and ingests
primary care physician supply data for Colorado counties into
health_raw.hdi_rows.

DATA SOURCE
-----------
HRSA Area Health Resources Files (AHRF)
URL: https://data.hrsa.gov/DataDownload/AHRF/AHRF_2024-2025_CSV.zip
Updated: Annually (most recent: March 2025)
Geographic level: County
License: Public domain, no usage limitations

WHAT IS THE AHRF?
-----------------
The AHRF is the gold standard dataset for healthcare workforce analysis
in the US. It compiles data from over 50 sources into a single county-
level file with 6,000+ variables covering physician supply, hospital
capacity, population, economics, and more.

WHY COUNTY-LEVEL?
-----------------
AHRF only provides data at the county level, but our Desert Index is
tract-level. This creates a geographic mismatch: every census tract
within a county gets the same physician-to-population ratio. This is a
known and accepted limitation in healthcare access research — even HRSA
uses county-level data for their Health Professional Shortage Area (HPSA)
designations.

The alternative would be the CMS NPPES NPI registry, which has individual
provider addresses we could geocode to tracts. That's 7+ million records
nationally and significantly more complex. We document this as a future
enhancement.

WHAT IS A PRIMARY CARE PHYSICIAN?
----------------------------------
HRSA defines primary care as four specialties:
  - General Family Medicine / Family Practice
  - General Practice
  - General Internal Medicine
  - General Pediatrics

This matches the HPSA designation criteria. The standard threshold HRSA
uses for shortage designation is 1 primary care physician per 3,500
population (or 1:3,000 for high-need areas). We'll use this as context
when interpreting Desert Index scores.

AHRF VARIABLE NAMES
--------------------
AHRF uses cryptic variable codes (e.g., "f1170323" for primary care
MDs in 2023). The technical documentation Excel file maps these codes
to human-readable descriptions. Starting with the 2022-2023 release,
AHRF switched to CSV format with more readable column names in subset
files. We handle both patterns.
"""

import csv
import io
import json
import os
import sys
import zipfile
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

# AHRF download URLs follow a predictable pattern.
# The 2024-2025 release is the most current as of March 2025.
AHRF_CSV_URL = "https://data.hrsa.gov/DataDownload/AHRF/AHRF_2024-2025_CSV.zip"
AHRF_RELEASE = "2024-2025"

# Source and dataset_id for the hdi_rows table — these let us identify
# and replace this specific dataset without affecting other HDI components.
SOURCE = "hrsa_ahrf"
DATASET_ID = f"ahrf_{AHRF_RELEASE}_county_physician_supply"


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
# AHRF download and extraction
# ---------------------------------------------------------------------------

def download_ahrf_zip(url: str, out_path: str) -> str:
    """
    Downloads the AHRF ZIP file to disk using streaming.

    WHY STREAM TO DISK?
    The AHRF ZIP is ~22 MB. While that would fit in memory, streaming is
    a good habit for data pipeline scripts because:
      1. Memory usage stays constant regardless of file size
      2. If the download fails partway, you see partial progress
      3. The pattern works unchanged for larger files later

    requests.get(stream=True) tells the requests library to NOT download
    the entire response body immediately. Instead, it downloads just the
    headers and gives you a response object you can read from in chunks.
    This is the difference between "give me the whole file" (stream=False,
    the default) and "let me pull bytes as I need them" (stream=True).
    """
    logger.info(f"Downloading AHRF ZIP: {url}")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    r = requests.get(url, stream=True, timeout=300)
    r.raise_for_status()

    # Write chunks to disk as they arrive instead of buffering in memory
    size = 0
    with open(out_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):  # 1 MB chunks
            if chunk:
                f.write(chunk)
                size += len(chunk)

    logger.info(f"Saved {size / 1024 / 1024:.1f} MB -> {out_path}")
    return out_path


def extract_csvs_from_zip(zip_path: str) -> dict:
    """
    Opens the AHRF ZIP and extracts the subset CSV files we need.

    The 2024-2025 AHRF ships as multiple subset CSVs inside the ZIP:
      AHRF2025.csv    — main file with all columns (huge, 4000+ columns)
      AHRF2025hp.csv  — health professions subset (physician counts)
      AHRF2025pop.csv — population subset (population estimates)
      AHRF2025geo.csv — geography subset (county names, FIPS codes)
      ... and others (env, exp, hf, utl)

    Rather than loading the massive main file, we extract only the subset
    files we need and join them by fips_st_cnty. This is faster and uses
    less memory.

    Returns a dict mapping suffix to CSV text:
      {"hp": "...", "pop": "...", "geo": "..."}
    """
    # The subset file suffixes we need for physician supply analysis
    needed_suffixes = ["hp", "pop", "geo"]

    with zipfile.ZipFile(zip_path, "r") as zf:
        csv_files = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        logger.info(f"ZIP contains {len(csv_files)} CSV files:")
        for name in csv_files:
            size_mb = zf.getinfo(name).file_size / 1024 / 1024
            logger.info(f"  {name} ({size_mb:.1f} MB)")

        extracted = {}
        for name in csv_files:
            # Extract the suffix from filenames like "AHRF2025hp.csv"
            # by stripping the path and extension, then taking the last 2-3 chars
            basename = name.split("/")[-1].replace(".csv", "").lower()

            for suffix in needed_suffixes:
                if basename.endswith(suffix):
                    logger.info(f"Extracting {suffix}: {name}")
                    raw_bytes = zf.read(name)
                    try:
                        extracted[suffix] = raw_bytes.decode("utf-8")
                    except UnicodeDecodeError:
                        extracted[suffix] = raw_bytes.decode("latin-1")
                    break

        # If we didn't find subset files, fall back to the main file
        if not extracted:
            # Use the main AHRF file (the one without a subset suffix)
            main_files = [n for n in csv_files
                          if not any(n.lower().replace(".csv", "").endswith(s)
                                     for s in ["hp", "pop", "geo", "env",
                                               "exp", "hf", "utl"])]
            if main_files:
                target = main_files[0]
                logger.info(f"No subset files found; using main file: {target}")
                raw_bytes = zf.read(target)
                try:
                    extracted["main"] = raw_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    extracted["main"] = raw_bytes.decode("latin-1")

        if not extracted:
            raise FileNotFoundError(
                f"No usable CSV files found in {zip_path}. "
                f"Contents: {zf.namelist()}"
            )

        return extracted


# ---------------------------------------------------------------------------
# AHRF parsing
# ---------------------------------------------------------------------------

def parse_csv_to_dict(csv_text: str, key_col: str) -> dict:
    """
    Parses a CSV string into a dict keyed by a specific column.

    Returns {key_value: {col1: val1, col2: val2, ...}, ...}

    This lets us join multiple AHRF subset files by their shared
    fips_st_cnty column without loading them into pandas.
    """
    f = io.StringIO(csv_text)
    reader = csv.DictReader(f)
    result = {}
    for row in reader:
        key = row.get(key_col, "").strip()
        if key:
            result[key] = row
    return result


def parse_ahrf_for_physician_supply(csv_dict: dict, state_fips: str) -> list:
    """
    Parses AHRF subset CSV files and extracts primary care physician
    counts and population for the target state.

    The 2024-2025 AHRF ships as multiple subset CSVs. The relevant ones:

      hp (health professions) — contains physician counts:
        fips_st_cnty                         → 5-digit county FIPS
        phys_nf_prim_care_pc_exc_rsdt_23     → non-federal primary care
                                               physicians (MD+DO combined)
                                               excluding residents, 2023
        md_nf_prim_care_pc_excl_rsdnt_23     → MDs only
        do_nf_prim_care_pc_excl_rsdnt_23     → DOs only

      pop (population) — contains population estimates:
        fips_st_cnty                         → same join key
        popn_est_24                          → population estimate 2024
        popn_est_23                          → population estimate 2023
        cens_popn_20                         → Census 2020 population

      geo (geography) — contains county names:
        fips_st_cnty                         → same join key
        Not in geo's columns from output but main file has cnty_name

    COLUMN NAME DECODING:
    The abbreviated names follow a pattern:
      phys  = physicians (MD + DO combined)
      md    = Doctor of Medicine only
      do    = Doctor of Osteopathic Medicine only
      nf    = non-federal (excludes military/VA physicians)
      prim_care = primary care specialties
      pc    = patient care (actively seeing patients)
      exc/excl_rsdt/rsdnt = excluding residents (trainees)
      _23   = year 2023

    WHY EXCLUDE RESIDENTS?
    Medical residents are physicians-in-training who work under
    supervision. While they do see patients, they're temporary —
    they'll move to wherever they get hired after training. Including
    them would overcount physician supply in counties with teaching
    hospitals and undercount everywhere else.

    WHY NON-FEDERAL?
    Federal physicians (VA, military, Indian Health Service) serve
    specific populations, not the general public. A VA hospital's
    doctors don't reduce wait times at the local clinic. HRSA excludes
    them from shortage designations for this reason.
    """
    # The join key used across all AHRF subset files
    JOIN_KEY = "fips_st_cnty"

    # --- Parse the subset files ---

    # Health professions file — physician counts
    hp_data = {}
    if "hp" in csv_dict:
        hp_data = parse_csv_to_dict(csv_dict["hp"], JOIN_KEY)
        logger.info(f"Parsed {len(hp_data)} counties from hp (health professions) file")
    elif "main" in csv_dict:
        hp_data = parse_csv_to_dict(csv_dict["main"], JOIN_KEY)
        logger.info(f"Parsed {len(hp_data)} counties from main file")

    # Population file — population estimates
    pop_data = {}
    if "pop" in csv_dict:
        pop_data = parse_csv_to_dict(csv_dict["pop"], JOIN_KEY)
        logger.info(f"Parsed {len(pop_data)} counties from pop (population) file")

    # Geography file — county names and state codes
    geo_data = {}
    if "geo" in csv_dict:
        geo_data = parse_csv_to_dict(csv_dict["geo"], JOIN_KEY)
        logger.info(f"Parsed {len(geo_data)} counties from geo (geography) file")

    # Use whatever file has the most counties as the base for iteration
    all_fips = set(hp_data.keys()) | set(pop_data.keys()) | set(geo_data.keys())
    logger.info(f"Total unique FIPS codes across files: {len(all_fips)}")

    # --- Column names for physician counts (from hp file) ---
    # We prefer the combined MD+DO count, falling back to summing separately
    PHYS_COMBINED_COLS = [
        "phys_nf_prim_care_pc_exc_rsdt_23",
        "phys_nf_prim_care_pc_exc_rsdt_22",
    ]
    MD_COLS = [
        "md_nf_prim_care_pc_excl_rsdnt_23",
        "md_nf_prim_care_pc_excl_rsdnt_22",
    ]
    DO_COLS = [
        "do_nf_prim_care_pc_excl_rsdnt_23",
        "do_nf_prim_care_pc_excl_rsdnt_22",
    ]

    # --- Column names for population (from pop file) ---
    POP_COLS = [
        "popn_est_24",
        "popn_est_23",
        "cens_popn_20",
    ]

    # --- Column names for county name (from geo or main file) ---
    NAME_COLS = [
        "cnty_name_st_abbrev",
        "cnty_name",
        "st_name",
    ]

    # --- Filter and extract for the target state ---
    results = []

    for fips in sorted(all_fips):
        # The first 2 digits of fips_st_cnty are the state FIPS
        fips_clean = fips.strip().zfill(5)
        if fips_clean[:2] != state_fips:
            continue

        hp_row = hp_data.get(fips, {})
        pop_row = pop_data.get(fips, {})
        geo_row = geo_data.get(fips, {})

        # --- Get physician count ---
        # Try the combined MD+DO column first
        phys_combined = None
        for col in PHYS_COMBINED_COLS:
            phys_combined = safe_int(hp_row.get(col, ""))
            if phys_combined is not None:
                break

        # Also get MD and DO separately for the payload
        md_count = None
        for col in MD_COLS:
            md_count = safe_int(hp_row.get(col, ""))
            if md_count is not None:
                break

        do_count = None
        for col in DO_COLS:
            do_count = safe_int(hp_row.get(col, ""))
            if do_count is not None:
                break

        # Use combined if available, otherwise sum MD + DO
        if phys_combined is not None:
            total_pcp = phys_combined
        else:
            total_pcp = (md_count or 0) + (do_count or 0)

        # --- Get population ---
        population = None
        for col in POP_COLS:
            population = safe_int(pop_row.get(col, ""))
            if population is not None:
                break

        # --- Get county name ---
        county_name = None
        for col in NAME_COLS:
            # Check geo file first, then hp file, then pop file
            for source_row in [geo_row, hp_row, pop_row]:
                val = source_row.get(col, "").strip()
                if val:
                    county_name = val
                    break
            if county_name:
                break

        # --- Compute ratio ---
        # HRSA's threshold: < 1:3,500 (≈ 28.6 per 100k) = shortage area
        pcp_per_100k = None
        if population and population > 0:
            pcp_per_100k = round(total_pcp / population * 100_000, 2)

        results.append({
            "county_fips": fips_clean,
            "county_name": county_name,
            "state_fips": state_fips,
            "pcp_md_count": md_count or 0,
            "pcp_do_count": do_count or 0,
            "total_pcp_count": total_pcp,
            "total_population": population,
            "pcp_per_100k": pcp_per_100k,
            "ahrf_release": AHRF_RELEASE,
        })

    logger.info(f"\nFound {len(results)} counties for state FIPS {state_fips}")
    return results


def safe_int(val) -> int:
    """Converts a string to int, returning None for blanks/errors."""
    if val is None:
        return None
    val = str(val).strip()
    if val == "" or val == ".":
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------

def delete_existing_dataset(conn, source: str, dataset_id: str, state_code: str):
    """
    Removes all rows for a specific source + dataset + state before
    re-ingesting. This makes the script idempotent — run it as many
    times as you want and you always get a clean result.
    """
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


def ingest_physician_rows(conn, source: str, dataset_id: str, state_code: str,
                          county_data: list, batch_size: int = 5000):
    """
    Inserts county-level physician supply data into health_raw.hdi_rows
    using the standard JSONB payload pattern. Each county becomes one row
    with its physician data stored as a JSON object in the payload column.
    """
    insert_sql = """
        INSERT INTO health_raw.hdi_rows (source, dataset_id, state_code, payload)
        VALUES (%s, %s, %s, %s::jsonb)
    """

    cur = conn.cursor()
    batch = []
    n = 0

    for county in county_data:
        batch.append((source, dataset_id, state_code, json.dumps(county)))

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
    logger.info(f"Done. Inserted {n} county physician supply rows")
    return n


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(script_dir, "..", ".env")
    load_env(env_path)

    state = STATE_CONFIG[ACTIVE_STATE]
    logger.info(f"Ingesting AHRF physician supply for: {state['name']}")
    logger.info(f"AHRF release: {AHRF_RELEASE}")
    logger.info()

    # Step 1: Download the AHRF ZIP
    data_dir = os.path.join(script_dir, "..", "data", "raw")
    zip_path = os.path.join(data_dir, f"ahrf_{AHRF_RELEASE}.zip")

    # Only download if we don't already have the ZIP (saves time on re-runs)
    if not os.path.exists(zip_path):
        download_ahrf_zip(AHRF_CSV_URL, zip_path)
    else:
        logger.info(f"Using cached ZIP: {zip_path}")

    # Step 2: Extract the subset CSVs from the ZIP
    csv_dict = extract_csvs_from_zip(zip_path)

    # Step 3: Parse for physician supply data, filtered to our state
    county_data = parse_ahrf_for_physician_supply(csv_dict, state["fips"])

    if not county_data:
        logger.warning("WARNING: No counties found. Check FIPS code and column mapping.")
        return

    # Print a summary of what we found
    logger.info(f"\nSample data (first 3 counties):")
    for c in county_data[:3]:
        logger.info(f"  {c['county_name']}: {c['total_pcp_count']} PCPs, pop {c['total_population']}, {c['pcp_per_100k']} per 100k")

    # Step 4: Write to database
    conn = get_conn()
    delete_existing_dataset(conn, SOURCE, DATASET_ID, state["abbreviation"])
    count = ingest_physician_rows(conn, SOURCE, DATASET_ID,
                                  state["abbreviation"], county_data)

    logger.info(f"\nComplete: {count} Colorado counties ingested into health_raw.hdi_rows")
    conn.close()


if __name__ == "__main__":
    main()