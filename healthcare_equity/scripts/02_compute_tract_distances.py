"""
02_compute_tract_distances.py
==============================
Downloads Census 2020 population-weighted tract centroids for Colorado,
then computes the distance from each tract centroid to the nearest
Emergency Department hospital.

Currently uses Haversine (straight-line) distance. Architected so
OpenRouteService drive time can be swapped in as a drop-in replacement.

DATA SOURCES
------------
1. Census 2020 Centers of Population — Tract level
   URL: https://www2.census.gov/geo/docs/reference/cenpop2020/tract/CenPop2020_Mean_TR{state_fips}.txt
   Format: Comma-delimited, one row per tract
   Contains: STATEFP, COUNTYFP, TRACTCE, POPULATION, LATITUDE, LONGITUDE

2. health_raw.hospital_registry — Hospital locations (from 02_ingest_cms_hospitals.py)
   We filter to hospitals with emergency_services = TRUE to find ED locations.

EUCLIDEAN vs DRIVE TIME: WHY IT MATTERS
----------------------------------------
Haversine distance measures the straight-line ("as the crow flies") distance
between two points on Earth's surface. It's a decent approximation for flat
terrain with a good road network, but systematically underestimates access
barriers in two key situations:

  1. MOUNTAINOUS TERRAIN — A tract in Silverton, CO might be 15 miles
     straight-line from Durango's hospital, but it's a 50-minute drive
     over mountain passes. Haversine says "close"; reality says "far."

  2. RIVER/HIGHWAY GAPS — Two points on opposite sides of a river with
     no nearby bridge might be 1 mile apart by Haversine but 20 minutes
     by car.

For Colorado specifically, this is a significant concern because the
Western Slope and mountain communities are exactly the areas most likely
to be healthcare deserts, and Haversine most dramatically underestimates
their access barriers.

UPGRADE PATH: OpenRouteService (ORS)
--------------------------------------
ORS provides a free API with 500 matrix requests/day. Colorado has ~1,250
tracts and ~80 ED hospitals. Using the matrix endpoint (up to 3,500
origin × destination pairs per request), we could compute all drive times
in roughly 30 batched requests — well within the free tier.

The compute_distances() function returns a standard list of dicts regardless
of whether it uses Haversine or ORS. To swap:
  1. Sign up at openrouteservice.org for a free API key
  2. Add ORS_API_KEY to your .env file
  3. Uncomment the ORS implementation in compute_distances_ors()
  4. Change the call in main() from compute_distances_haversine() to
     compute_distances_ors()

WHAT IS HAVERSINE?
-------------------
The Haversine formula calculates the great-circle distance between two
points on a sphere given their latitudes and longitudes. "Great circle"
means the shortest path along the surface of the Earth (like the path
an airplane would fly). The formula accounts for Earth's curvature,
which matters at distances beyond a few miles.

The math:
  a = sin²(Δlat/2) + cos(lat1) × cos(lat2) × sin²(Δlon/2)
  c = 2 × atan2(√a, √(1−a))
  distance = R × c   (where R = Earth's radius ≈ 3,959 miles)
"""

import csv
import io
import json
import math
import os
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

# Census 2020 Centers of Population — tract-level centroids.
# These are population-weighted centroids, meaning they represent where
# people actually live within a tract, not the geographic center.
# This matters because a large rural tract's geographic center might be
# in an empty field, but its population center is in the one small town.
CENSUS_CENTROID_URL = (
    "https://www2.census.gov/geo/docs/reference/cenpop2020/tract/"
    "CenPop2020_Mean_TR{state_fips}.txt"
)

SOURCE = "haversine_distance"
DATASET_ID = "tract_to_nearest_ed_2020"

# Earth's radius in miles (for Haversine calculation)
EARTH_RADIUS_MILES = 3958.8


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
# Data fetching
# ---------------------------------------------------------------------------

def download_tract_centroids(state_fips: str) -> list:
    """
    Downloads Census 2020 population-weighted tract centroids for a state.

    Returns a list of dicts:
      [{"tract_geoid": "08001000100", "lat": 39.95, "lon": -104.99,
        "population": 5432, "county_fips": "08001"}, ...]

    GEOID construction: state_fips (2) + county_fips (3) + tract_code (6) = 11 digits.
    This is the standard Census tract identifier used everywhere.
    """
    url = CENSUS_CENTROID_URL.format(state_fips=state_fips)
    logger.info(f"Downloading tract centroids: {url}")

    r = requests.get(url, timeout=120)
    r.raise_for_status()

    # Census files can use different encodings and may include a BOM character.
    # utf-8-sig strips the BOM automatically if present.
    try:
        text = r.content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = r.content.decode("latin-1")

    # Parse the comma-delimited file
    f = io.StringIO(text)
    reader = csv.DictReader(f)

    tracts = []
    for row in reader:
        # Census centroid files have columns:
        #   STATEFP, COUNTYFP, TRACTCE, POPULATION, LATITUDE, LONGITUDE
        state = row.get("STATEFP", "").strip()
        county = row.get("COUNTYFP", "").strip()
        tract = row.get("TRACTCE", "").strip()
        pop_str = row.get("POPULATION", "0").strip()
        lat_str = row.get("LATITUDE", "").strip()
        lon_str = row.get("LONGITUDE", "").strip()

        if not (state and county and tract and lat_str and lon_str):
            continue

        # Build the 11-digit GEOID: state (2) + county (3) + tract (6)
        geoid = f"{state.zfill(2)}{county.zfill(3)}{tract.zfill(6)}"
        county_fips = f"{state.zfill(2)}{county.zfill(3)}"

        try:
            lat = float(lat_str)
            lon = float(lon_str)
            pop = int(pop_str) if pop_str else 0
        except ValueError:
            continue

        tracts.append({
            "tract_geoid": geoid,
            "county_fips": county_fips,
            "lat": lat,
            "lon": lon,
            "population": pop,
        })

    logger.info(f"Parsed {len(tracts)} tract centroids")
    return tracts


def fetch_ed_hospitals(conn, state_code: str) -> list:
    """
    Queries hospital_registry for hospitals in the target state that
    have emergency services. Returns a list of (ccn, lat, lon, name) tuples.

    We only use hospitals with emergency_services = TRUE because the
    Desert Index measures access to emergency care specifically.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT hospital_ccn, latitude, longitude, hospital_name
        FROM health_raw.hospital_registry
        WHERE state_code = %s
          AND emergency_services = TRUE
          AND latitude IS NOT NULL
          AND longitude IS NOT NULL;
        """,
        (state_code,),
    )
    rows = cur.fetchall()
    cur.close()

    hospitals = []
    for ccn, lat, lon, name in rows:
        hospitals.append({
            "ccn": ccn,
            "lat": float(lat),
            "lon": float(lon),
            "name": name,
        })

    logger.info(f"Found {len(hospitals)} ED hospitals in {state_code}")
    return hospitals


# ---------------------------------------------------------------------------
# Distance computation — Haversine (current implementation)
# ---------------------------------------------------------------------------

def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Computes the great-circle distance between two points on Earth
    using the Haversine formula. Returns distance in miles.

    The formula works by:
    1. Converting lat/lon from degrees to radians (math functions need radians)
    2. Computing the differences in lat and lon
    3. Applying the Haversine formula which accounts for Earth's curvature
    4. Multiplying by Earth's radius to get miles
    """
    # Convert degrees to radians — math.sin() and math.cos() expect radians
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    # The Haversine formula itself
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return EARTH_RADIUS_MILES * c


def compute_distances_haversine(tracts: list, hospitals: list) -> list:
    """
    For each tract, finds the nearest ED hospital by straight-line distance.

    Returns a list of dicts with distance results:
      [{"tract_geoid": "08001000100", "nearest_ed_ccn": "060001",
        "nearest_ed_name": "Hospital X", "distance_miles": 4.2,
        "distance_method": "haversine", ...}, ...]

    This is an O(tracts × hospitals) computation. For Colorado's ~1,250
    tracts × ~80 ED hospitals = ~100,000 distance calculations. Each one
    is a simple math formula, so the whole thing runs in under a second.
    """
    results = []

    for i, tract in enumerate(tracts):
        best_dist = float("inf")
        best_hospital = None

        for hosp in hospitals:
            dist = haversine_miles(
                tract["lat"], tract["lon"],
                hosp["lat"], hosp["lon"],
            )
            if dist < best_dist:
                best_dist = dist
                best_hospital = hosp

        # Estimate drive time from Haversine distance.
        # Rule of thumb: multiply straight-line distance by 1.4 for road
        # distance (roads aren't straight), then assume 35 mph average speed.
        # This is very rough but gives a ballpark. Will be replaced by ORS.
        estimated_road_miles = best_dist * 1.4
        estimated_drive_minutes = (estimated_road_miles / 35.0) * 60.0

        results.append({
            "tract_geoid": tract["tract_geoid"],
            "county_fips": tract["county_fips"],
            "tract_lat": tract["lat"],
            "tract_lon": tract["lon"],
            "tract_population": tract["population"],
            "nearest_ed_ccn": best_hospital["ccn"] if best_hospital else None,
            "nearest_ed_name": best_hospital["name"] if best_hospital else None,
            "nearest_ed_lat": best_hospital["lat"] if best_hospital else None,
            "nearest_ed_lon": best_hospital["lon"] if best_hospital else None,
            "haversine_miles": round(best_dist, 2),
            "estimated_road_miles": round(estimated_road_miles, 2),
            "estimated_drive_minutes": round(estimated_drive_minutes, 1),
            "distance_method": "haversine",
        })

        if (i + 1) % 250 == 0:
            logger.info(f"Computed distances for {i + 1}/{len(tracts)} tracts...")

    return results


# ---------------------------------------------------------------------------
# Distance computation — OpenRouteService (future implementation)
# ---------------------------------------------------------------------------

# TODO: Implement ORS drive time computation.
#
# To activate:
# 1. Sign up at https://openrouteservice.org/ for a free API key
# 2. Add ORS_API_KEY=your_key_here to your .env file
# 3. Uncomment compute_distances_ors() below
# 4. Change main() to call compute_distances_ors() instead of _haversine()
#
# ORS free tier allows 500 matrix requests/day with up to 3,500
# origin × destination pairs per request (e.g., 50 origins × 70 destinations).
# For Colorado: ~1,250 tracts × ~80 hospitals = ~100,000 pairs.
# Batching 50 tracts at a time = 25 requests. Well within free tier.
#
# def compute_distances_ors(tracts: list, hospitals: list) -> list:
#     """Uses OpenRouteService matrix API for actual drive times."""
#     import time
#
#     api_key = os.environ.get("ORS_API_KEY")
#     if not api_key:
#         raise ValueError("ORS_API_KEY not set in .env")
#
#     ors_url = "https://api.openrouteservice.org/v2/matrix/driving-car"
#     headers = {"Authorization": api_key, "Content-Type": "application/json"}
#
#     # Build hospital location list (destinations — same for every batch)
#     hosp_coords = [[h["lon"], h["lat"]] for h in hospitals]
#
#     results = []
#     batch_size = 50  # tracts per request (50 × 80 = 4,000 pairs ≈ 3,500 limit)
#
#     for start in range(0, len(tracts), batch_size):
#         batch_tracts = tracts[start:start + batch_size]
#         tract_coords = [[t["lon"], t["lat"]] for t in batch_tracts]
#
#         # Combine: tracts first, then hospitals
#         all_coords = tract_coords + hosp_coords
#         sources = list(range(len(tract_coords)))
#         destinations = list(range(len(tract_coords), len(all_coords)))
#
#         body = {
#             "locations": all_coords,
#             "sources": sources,
#             "destinations": destinations,
#             "metrics": ["duration", "distance"],
#             "units": "mi",
#         }
#
#         r = requests.post(ors_url, json=body, headers=headers, timeout=60)
#         r.raise_for_status()
#         data = r.json()
#
#         durations = data["durations"]  # seconds
#         distances = data["distances"]  # miles
#
#         for i, tract in enumerate(batch_tracts):
#             # Find the hospital with the shortest drive time
#             min_idx = min(range(len(hospitals)),
#                          key=lambda j: durations[i][j] if durations[i][j] else float("inf"))
#
#             drive_seconds = durations[i][min_idx]
#             drive_miles = distances[i][min_idx]
#             best_hosp = hospitals[min_idx]
#
#             results.append({
#                 "tract_geoid": tract["tract_geoid"],
#                 "county_fips": tract["county_fips"],
#                 "tract_lat": tract["lat"],
#                 "tract_lon": tract["lon"],
#                 "tract_population": tract["population"],
#                 "nearest_ed_ccn": best_hosp["ccn"],
#                 "nearest_ed_name": best_hosp["name"],
#                 "nearest_ed_lat": best_hosp["lat"],
#                 "nearest_ed_lon": best_hosp["lon"],
#                 "drive_time_minutes": round(drive_seconds / 60.0, 1) if drive_seconds else None,
#                 "drive_distance_miles": round(drive_miles, 2) if drive_miles else None,
#                 "distance_method": "openrouteservice",
#             })
#
#         print(f"ORS batch {start // batch_size + 1}: "
#               f"processed {len(batch_tracts)} tracts")
#
#         # Respect ORS rate limit: 40 requests per minute
#         time.sleep(1.5)
#
#     return results


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------

def delete_existing_dataset(conn, source: str, dataset_id: str, state_code: str):
    """Removes existing distance data for re-ingestion."""
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


def ingest_distance_rows(conn, source: str, dataset_id: str, state_code: str,
                         distance_data: list, batch_size: int = 5000):
    """
    Inserts tract-to-nearest-ED distance data into health_raw.hdi_rows
    using the standard JSONB payload pattern.
    """
    insert_sql = """
        INSERT INTO health_raw.hdi_rows (source, dataset_id, state_code, payload)
        VALUES (%s, %s, %s, %s::jsonb)
    """

    cur = conn.cursor()
    batch = []
    n = 0

    for record in distance_data:
        batch.append((source, dataset_id, state_code, json.dumps(record)))

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
    logger.info(f"Done. Inserted {n} tract distance rows")
    return n


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(script_dir, "..", ".env")
    load_env(env_path)

    state = STATE_CONFIG[ACTIVE_STATE]
    logger.info(f"Computing tract-to-ED distances for: {state['name']}")
    logger.info()

    # Step 1: Connect to DB and fetch ED hospital locations
    conn = get_conn()
    hospitals = fetch_ed_hospitals(conn, state["abbreviation"])

    if not hospitals:
        logger.error("No ED hospitals found in hospital_registry. Run 02_ingest_cms_hospitals.py first.")
        conn.close()
        return

    # Step 2: Download tract centroids from Census
    tracts = download_tract_centroids(state["fips"])

    if not tracts:
        logger.error("ERROR: No tract centroids downloaded.")
        conn.close()
        return

    # Step 3: Compute distances (Haversine for now, ORS later)
    logger.info(f"Computing Haversine distances: {len(tracts)} tracts x {len(hospitals)} ED hospitals = {len(tracts) * len(hospitals):,} distance calculations")
    logger.info("(This should take a few seconds...)\n")

    distance_data = compute_distances_haversine(tracts, hospitals)

    # Print summary statistics
    distances = [d["haversine_miles"] for d in distance_data]
    logger.info(f"\nDistance summary (Haversine miles to nearest ED):")
    logger.info(f"  Min:    {min(distances):.1f} miles")
    logger.info(f"  Max:    {max(distances):.1f} miles")
    logger.info(f"  Median: {sorted(distances)[len(distances) // 2]:.1f} miles")
    logger.info(f"  Mean:   {sum(distances) / len(distances):.1f} miles")

    # Count tracts beyond common access thresholds
    over_30 = sum(1 for d in distances if d > 30)
    over_60 = sum(1 for d in distances if d > 60)
    logger.info(f"  Tracts > 30 miles from ED: {over_30} ({over_30/len(distances)*100:.1f}%)")
    logger.info(f"  Tracts > 60 miles from ED: {over_60} ({over_60/len(distances)*100:.1f}%)")

    # Step 4: Write to database
    logger.info()
    delete_existing_dataset(conn, SOURCE, DATASET_ID, state["abbreviation"])
    count = ingest_distance_rows(conn, SOURCE, DATASET_ID,
                                 state["abbreviation"], distance_data)

    logger.info(f"\nComplete: {count} tract distances ingested into health_raw.hdi_rows")
    logger.info(f"Distance method: Haversine (straight-line)")
    logger.info(f"NOTE: These distances underestimate actual drive time, especially")
    logger.info(f"in mountainous Western Colorado. Upgrade to OpenRouteService for")
    logger.info(f"accurate drive times (see TODO in source code).")

    conn.close()


if __name__ == "__main__":
    main()