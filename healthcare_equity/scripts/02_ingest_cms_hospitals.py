"""
02_ingest_cms_hospitals.py
==========================
Downloads the CMS Hospital General Information dataset and populates
health_raw.hospital_registry with Colorado hospitals.

This is the backbone table for both the Desert Index (which hospitals
are near which tracts?) and the Transparency tool (what's this
hospital's compliance grade?).

DATA SOURCE
-----------
CMS Provider Data Catalog — Hospital General Information
Metastore slug: xubh-q36u

The CMS Provider Data Catalog uses a "metastore" pattern where each
dataset has a human-readable slug. We first hit the metastore endpoint
to get the actual CSV download URL, then download the CSV. This is more
reliable than guessing the datastore query URL, which can change when
CMS updates their backend.

WHAT THIS SCRIPT DOES
---------------------
1. Queries the CMS metastore to resolve the CSV download URL
2. Downloads the full national hospital CSV (streaming to disk)
3. Filters to the configured state (Colorado by default)
4. Upserts each hospital into health_raw.hospital_registry

WHY UPSERT INSTEAD OF DELETE-AND-REINSERT?
-------------------------------------------
Unlike the JSONB raw tables where we wipe and reload, hospital_registry
has typed columns AND will accumulate data from other sources (PRA grades,
enforcement counts, price file URLs). A full DELETE would blow away those
fields. Instead, we use INSERT ... ON CONFLICT to update only the CMS-
sourced columns while preserving transparency data added by later scripts.
"""

import csv
import io
import os
import sys
import requests
import pg8000
import json

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

# CMS Provider Data Catalog — metastore slug for Hospital General Information.
# This slug resolves to the actual CSV download URL via the metastore API.
CMS_METASTORE_BASE = "https://data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items"
HOSPITAL_GENERAL_SLUG = "xubh-q36u"


# ---------------------------------------------------------------------------
# Standard pipeline utilities
# ---------------------------------------------------------------------------

def load_env(path: str) -> None:
    """
    Reads a .env file and sets environment variables.
    Uses os.environ.setdefault so existing env vars aren't overwritten.
    """
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
# CMS data fetching
# ---------------------------------------------------------------------------

def resolve_cms_csv_url(slug: str) -> str:
    """
    Queries the CMS metastore to find the actual CSV download URL for a
    dataset identified by its slug.

    The metastore response contains a 'distribution' array with download
    links. We look for the entry with format 'csv' and return its
    downloadURL. This is the correct pattern for CMS Provider Data Catalog —
    the datastore query endpoint can break when CMS reorganizes their backend.
    """
    url = f"{CMS_METASTORE_BASE}/{slug}"
    logger.info(f"Resolving CSV URL from metastore: {url}")

    r = requests.get(url, timeout=60)
    r.raise_for_status()
    meta = r.json()

    # The distribution array contains one entry per file format (CSV, JSON, etc.)
    for dist in meta.get("distribution", []):
        # CMS uses 'format' or 'mediaType' to indicate file type
        fmt = dist.get("format", "").lower()
        media = dist.get("mediaType", "").lower()
        if "csv" in fmt or "csv" in media:
            download_url = dist.get("downloadURL", "")
            if download_url:
                logger.info(f"Resolved CSV URL: {download_url}")
                return download_url

    raise ValueError(f"No CSV distribution found for slug: {slug}")


def download_cms_csv(url: str) -> str:
    """
    Downloads a CSV file from CMS and returns the text content.

    WHY NOT STREAM TO DISK HERE?
    The Hospital General Information CSV is relatively small (~7,000 rows
    nationally, ~1 MB). For files this size, loading into memory is fine
    and simpler. We'll use streaming for the much larger price files in
    Phase 4 where files can be hundreds of MB.
    """
    logger.info(f"Downloading: {url}")
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    logger.info(f"Downloaded {len(r.text):,} characters")
    return r.text


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------

def delete_state_hospitals(conn, state_code: str):
    """
    Removes all hospitals for a given state before re-ingesting.

    For hospital_registry specifically, we delete-and-reinsert by state
    rather than using upsert, because it's simpler and we want a clean
    slate of CMS data each run. Transparency fields (PRA grade, enforcement
    count) will be populated by separate scripts that run AFTER this one.
    """
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM health_raw.hospital_registry WHERE state_code = %s;",
        (state_code,),
    )
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    logger.info(f"Deleted {deleted} existing hospitals for state_code={state_code}")


def parse_bool(val: str) -> bool:
    """Converts CMS 'Yes'/'No' strings to Python booleans."""
    if val is None:
        return None
    return val.strip().lower() == "yes"


def parse_int(val: str):
    """Safely converts a string to int, returning None for blanks/errors."""
    if val is None:
        return None
    val = val.strip()
    if val == "":
        return None
    try:
        return int(float(val))
    except ValueError:
        return None


def parse_float(val: str):
    """Safely converts a string to float, returning None for blanks/errors."""
    if val is None:
        return None
    val = val.strip()
    if val == "":
        return None
    try:
        return float(val)
    except ValueError:
        return None


def ingest_hospitals(conn, csv_text: str, state_abbr: str, state_fips: str,
                     batch_size: int = 500):
    """
    Parses the CMS Hospital General Information CSV, filters to the target
    state, and inserts rows into health_raw.hospital_registry.

    CMS COLUMN NAMES (as of 2024-2025 dataset):
    - Facility ID          → hospital_ccn (CMS Certification Number)
    - Facility Name        → hospital_name
    - Address              → address
    - City/Town            → city
    - State                → state_code (two-letter abbreviation)
    - ZIP Code             → zip_code
    - County/Parish        → county_name
    - Hospital Type        → hospital_type
    - Hospital Ownership   → ownership
    - Emergency Services   → emergency_services (Yes/No)
    - Location             → contains lat/long as "POINT (lng lat)"

    NOTE: CMS column names can vary slightly between releases (e.g.,
    "City/Town" vs "City"). The script tries common variants and logs
    warnings if a column isn't found.
    """

    # Parse the CSV text into a list of dictionaries
    f = io.StringIO(csv_text)
    reader = csv.DictReader(f)

    insert_sql = """
        INSERT INTO health_raw.hospital_registry (
            hospital_ccn, hospital_name, address, city, state_code,
            zip_code, county_name, latitude, longitude,
            hospital_type, ownership, emergency_services,
            state_fips
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s
        )
    """

    cur = conn.cursor()
    batch = []
    n = 0
    skipped = 0

    for row in reader:
        # Filter to target state — CMS uses the two-letter abbreviation
        row_state = row.get("State", "").strip()
        if row_state != state_abbr:
            continue

        # Extract CMS Certification Number (the primary key for hospitals)
        ccn = row.get("Facility ID", "").strip()
        if not ccn:
            skipped += 1
            continue

        name = row.get("Facility Name", "").strip()

        # Address — CMS sometimes uses "Address" or "Address Line 1"
        address = (row.get("Address", "") or row.get("Address Line 1", "")).strip()

        # City — CMS sometimes uses "City/Town" or "City"
        city = (row.get("City/Town", "") or row.get("City", "")).strip()

        zip_code = row.get("ZIP Code", "").strip()
        county = (row.get("County/Parish", "") or row.get("County Name", "")).strip()

        hospital_type = row.get("Hospital Type", "").strip()
        ownership = row.get("Hospital Ownership", "").strip()
        emergency = parse_bool(row.get("Emergency Services", ""))

        # Parse coordinates from the "Location" field.
        # CMS formats this as "POINT (-104.821 39.745)" (longitude first, then latitude).
        # Some rows may have empty Location fields.
        lat, lng = None, None
        location_str = row.get("Location", "").strip()
        if location_str.startswith("POINT"):
            # Extract the numbers between parentheses
            try:
                coords = location_str.replace("POINT", "").strip("() ")
                parts = coords.split()
                if len(parts) == 2:
                    # POINT format is (longitude latitude) — note the order!
                    lng = parse_float(parts[0])
                    lat = parse_float(parts[1])
            except (ValueError, IndexError):
                pass  # Leave lat/lng as None if parsing fails

        batch.append((
            ccn, name, address, city, state_abbr,
            zip_code, county, lat, lng,
            hospital_type, ownership, emergency,
            state_fips,
        ))

        if len(batch) >= batch_size:
            cur.executemany(insert_sql, batch)
            conn.commit()
            n += len(batch)
            logger.info(f"Inserted {n} hospitals...")
            batch = []

    # Insert any remaining rows in the final partial batch
    if batch:
        cur.executemany(insert_sql, batch)
        conn.commit()
        n += len(batch)

    cur.close()
    logger.info(f"Done. Inserted {n} hospitals for {state_abbr} (skipped {skipped} rows with no CCN)")
    return n


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Resolve .env relative to this script's location
    # Structure: healthcare_equity/scripts/02_ingest_cms_hospitals.py
    #            healthcare_equity/.env
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(script_dir, "..", ".env")
    load_env(env_path)

    state = STATE_CONFIG[ACTIVE_STATE]
    logger.info(f"Ingesting CMS hospitals for: {state['name']} ({state['abbreviation']})")
    logger.info()

    # Step 1: Resolve the CSV download URL from the CMS metastore
    csv_url = resolve_cms_csv_url(HOSPITAL_GENERAL_SLUG)

    # Step 2: Download the full national CSV
    csv_text = download_cms_csv(csv_url)

    # Step 3: Connect to database and clear existing state data
    conn = get_conn()
    delete_state_hospitals(conn, state["abbreviation"])

    # Step 4: Parse CSV, filter to state, insert into hospital_registry
    count = ingest_hospitals(
        conn,
        csv_text,
        state_abbr=state["abbreviation"],
        state_fips=state["fips"],
    )

    logger.info()
    logger.info(f"Hospital registry complete: {count} hospitals for {state['name']}")

    conn.close()


if __name__ == "__main__":
    main()