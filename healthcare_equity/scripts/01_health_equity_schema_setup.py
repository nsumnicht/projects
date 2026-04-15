"""
01_health_equity_setup.py
=========================
Healthcare Equity Tool - Database Schema Setup

Creates all new tables in the EXISTING health_raw and health_clean schemas. 
No new schemas are created, we are just adding tables to the already existing ones

CONFIGURATION-DRIVEN DESIGN
----------------------------
Every table incldues a state_code column so we can expand to additional
states without restructuring. the STATE_CONFIG dictionary below defines
state specific parameters in one place. adding a new state means adding 
one dictionary entry, not rewriting queries or adding new tables.

"""

import os
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
 
# Central state configuration — add new states here as the project expands.
# Each entry contains everything state-specific so no per-state logic is
# scattered across the codebase.
STATE_CONFIG = {
    "colorado": {
        "fips": "08",                # Federal FIPS code for state filtering
        "abbreviation": "CO",        # Two-letter code for file paths / display
        "name": "Colorado",          # Human-readable name for the Dash UI
        # HCPF report URL for price transparency file discovery.
        # Other states will have their own equivalent agency URL.
        "hcpf_url": "https://hcpf.colorado.gov/hospital-price-transparency",
    },
}
 
# The active state for this pipeline run. 
# Change this one value to process a different state once their config is added to STATE_CONFIG.
ACTIVE_STATE = "colorado"
 
 
# ---------------------------------------------------------------------------
# Standard pipeline utilities (same pattern as existing ingest scripts)
# ---------------------------------------------------------------------------
 
def load_env(path: str) -> None:
    """
    Reads a .env file and sets environment variables.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing .env file at: {path}")
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            # Skip blank lines, comments, and lines without '='
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
 
 
def get_conn():
    """
    Returns a pg8000 connection to the projects database.
    """
    return pg8000.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        database=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )
 
 
# ---------------------------------------------------------------------------
# Table definitions
# ---------------------------------------------------------------------------
# Each table is defined as a (table_name, ddl_string) tuple.
# ensure_tables() iterates through them all, creating any that don't exist.
# ---------------------------------------------------------------------------
 
RAW_TABLES = [
    # ------------------------------------------------------------------
    # hospital_registry: The shared backbone table for both projects.
    # Every Colorado hospital gets one row. Both the Desert Index
    # (which hospitals are near which tracts?) and the Transparency
    # tool (what's this hospital's compliance grade?) join through
    # this table via hospital CCN (CMS Certification Number).
    # ------------------------------------------------------------------
    (
        "health_raw.hospital_registry",
        """
        CREATE TABLE IF NOT EXISTS health_raw.hospital_registry (
            hospital_ccn        TEXT PRIMARY KEY,
            hospital_name       TEXT NOT NULL,
            address             TEXT,
            city                TEXT,
            state_code          TEXT NOT NULL,
            zip_code            TEXT,
            county_name         TEXT,
            county_fips         TEXT,
            latitude            DOUBLE PRECISION,
            longitude           DOUBLE PRECISION,
            hospital_type       TEXT,
            ownership           TEXT,
            emergency_services  BOOLEAN,
            bed_count           INTEGER,
 
            -- Price transparency fields
            price_file_url      TEXT,
            price_file_format   TEXT,
            pra_compliance_grade TEXT,
            cms_enforcement_count INTEGER DEFAULT 0,
            last_downloaded     TIMESTAMPTZ,
 
            -- Metadata
            state_fips          TEXT,
            ingested_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    ),
 
    # ------------------------------------------------------------------
    # hdi_rows: Raw Healthcare Desert Index component data.
    # Stores physician supply counts, drive time calculations, and
    # any other raw inputs needed to compute the Desert Index.
    #
    # The 'source' column distinguishes data origins:
    #   - 'hrsa_ahrf' for physician supply from Area Health Resources Files
    #   - 'osrm_drive_time' for computed drive times to nearest ED
    #   - 'census_acs_extract' for Colorado-filtered insurance/poverty data
    #
    # The 'dataset_id' column tracks the specific dataset version
    # (e.g., 'ahrf_2023_county_physician_supply') so we can re-ingest
    # updated releases without confusion.
    # ------------------------------------------------------------------
    (
        "health_raw.hdi_rows",
        """
        CREATE TABLE IF NOT EXISTS health_raw.hdi_rows (
            source       TEXT NOT NULL,
            dataset_id   TEXT NOT NULL,
            state_code   TEXT NOT NULL,
            ingested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            payload      JSONB NOT NULL
        );
        """
    ),
 
    # ------------------------------------------------------------------
    # transparency_rows: Raw transparency data from external sources.
    # Stores PRA compliance scores and CMS enforcement actions.
    #
    # Separate from hospital_registry because one hospital can have
    # MULTIPLE enforcement actions over time — this is a log of events,
    # not a single status. The registry summarizes; this table stores
    # the full history.
    #
    # Sources:
    #   - 'pra' for Patient Rights Advocate compliance grades
    #   - 'cms_enforcement' for HHS enforcement actions
    #   - 'hcpf' for Colorado HCPF evaluation report data
    # ------------------------------------------------------------------
    (
        "health_raw.transparency_rows",
        """
        CREATE TABLE IF NOT EXISTS health_raw.transparency_rows (
            source       TEXT NOT NULL,
            dataset_id   TEXT NOT NULL,
            state_code   TEXT NOT NULL,
            ingested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            payload      JSONB NOT NULL
        );
        """
    ),
 
    # ------------------------------------------------------------------
    # price_line_items: Parsed hospital price file records.
    # One row per billing code per hospital per payer.
    #
    # This is the largest table in the project. A single hospital's
    # price file can contain 300,000+ line items (every procedure ×
    # every insurance plan × every price type). For Colorado's ~100
    # hospitals, that's potentially tens of millions of rows.
    #
    # WHY JSONB HERE INSTEAD OF TYPED COLUMNS?
    # Hospital price files are notoriously inconsistent. Even with
    # the CMS standardized template (required since July 2024),
    # hospitals interpret the fields differently. Storing as JSONB
    # lets us ingest everything, then normalize in the clean layer
    # where we can handle the variations explicitly.
    # ------------------------------------------------------------------
    (
        "health_raw.price_line_items",
        """
        CREATE TABLE IF NOT EXISTS health_raw.price_line_items (
            hospital_ccn  TEXT NOT NULL,
            state_code    TEXT NOT NULL,
            ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            payload       JSONB NOT NULL
        );
        """
    ),
]
 
 
CLEAN_TABLES = [
    # ------------------------------------------------------------------
    # desert_index: The finished Desert Index scores per census tract.
    #
    # Each row is one census tract with its four normalized component
    # scores, the weighted composite score, and the tier classification.
    #
    # WHY TYPED COLUMNS IN CLEAN BUT JSONB IN RAW?
    # By the time data reaches the clean layer, we've made explicit
    # decisions about which fields matter and what types they should be.
    # Typed columns give us:
    #   - Postgres type checking (can't accidentally store "N/A" in a
    #     DOUBLE PRECISION column)
    #   - Better query performance (no JSONB extraction overhead)
    #   - Self-documenting schema (column names ARE the documentation)
    # ------------------------------------------------------------------
    (
        "health_clean.desert_index",
        """
        CREATE TABLE IF NOT EXISTS health_clean.desert_index (
            tract_geoid         TEXT NOT NULL,
            state_code          TEXT NOT NULL,
            county_fips         TEXT,
            county_name         TEXT,
            total_population    INTEGER,
 
            -- Raw component values (before normalization)
            drive_time_minutes       DOUBLE PRECISION,
            physician_ratio          DOUBLE PRECISION,
            uninsured_rate           DOUBLE PRECISION,
            poverty_rate             DOUBLE PRECISION,
 
            -- Normalized component scores (0-1 scale, higher = worse)
            drive_time_score         DOUBLE PRECISION,
            physician_score          DOUBLE PRECISION,
            uninsured_score          DOUBLE PRECISION,
            poverty_score            DOUBLE PRECISION,
 
            -- Composite index
            desert_score             DOUBLE PRECISION,
            desert_tier              TEXT,
 
            -- Weights used (stored per row so historical runs are
            -- self-documenting — if you change weights and re-run,
            -- old rows in a backup still show what weights produced them)
            weight_drive_time        DOUBLE PRECISION,
            weight_physician         DOUBLE PRECISION,
            weight_uninsured         DOUBLE PRECISION,
            weight_poverty           DOUBLE PRECISION,
 
            computed_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
 
            PRIMARY KEY (tract_geoid, state_code)
        );
        """
    ),
 
    # ------------------------------------------------------------------
    # hospital_equity_profile: The combined view per hospital.
    # Joins Desert Index context with transparency grades and
    # enforcement history. One row per hospital — this is what
    # the Dash app queries for hospital cards and map dots.
    # ------------------------------------------------------------------
    (
        "health_clean.hospital_equity_profile",
        """
        CREATE TABLE IF NOT EXISTS health_clean.hospital_equity_profile (
            hospital_ccn             TEXT PRIMARY KEY,
            hospital_name            TEXT NOT NULL,
            state_code               TEXT NOT NULL,
            address                  TEXT,
            city                     TEXT,
            zip_code                 TEXT,
            county_name              TEXT,
            latitude                 DOUBLE PRECISION,
            longitude                DOUBLE PRECISION,
            hospital_type            TEXT,
            ownership                TEXT,
            bed_count                INTEGER,
 
            -- Desert context (from the tract the hospital sits in)
            tract_geoid              TEXT,
            desert_score             DOUBLE PRECISION,
            desert_tier              TEXT,
 
            -- Transparency metrics
            pra_compliance_grade     TEXT,
            has_parseable_price_file BOOLEAN DEFAULT FALSE,
            cms_enforcement_count    INTEGER DEFAULT 0,
            most_recent_enforcement  TIMESTAMPTZ,
 
            -- Combined equity classification
            -- High desert + low transparency = worst equity outcome
            equity_concern_level     TEXT,
 
            computed_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    ),
 
    # ------------------------------------------------------------------
    # procedure_prices: Normalized pricing data ready for Dash queries.
    # One row per procedure per hospital per price type.
    #
    # This table bridges the gap between raw price file chaos and
    # a user typing "knee replacement" into a search bar. The
    # plain_english_name and procedure_category columns are populated
    # by joining with the reference/procedure_name_map.csv lookup.
    # ------------------------------------------------------------------
    (
        "health_clean.procedure_prices",
        """
        CREATE TABLE IF NOT EXISTS health_clean.procedure_prices (
            hospital_ccn              TEXT NOT NULL,
            state_code                TEXT NOT NULL,
            billing_code              TEXT NOT NULL,
            billing_code_type         TEXT,
            description_raw           TEXT,
            plain_english_name        TEXT,
            procedure_category        TEXT,
            gross_charge              DOUBLE PRECISION,
            discounted_cash_price     DOUBLE PRECISION,
            payer_name                TEXT,
            negotiated_rate           DOUBLE PRECISION,
            min_negotiated_rate       DOUBLE PRECISION,
            max_negotiated_rate       DOUBLE PRECISION,
 
            ingested_at               TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    ),
]
 
 
# ---------------------------------------------------------------------------
# Schema setup logic
# ---------------------------------------------------------------------------
 
def ensure_tables(conn, table_definitions: list):
    """
    Iterates through a list of (table_name, ddl) tuples and creates
    each table if it doesn't already exist.
 
    WHY CREATE TABLE IF NOT EXISTS?
    This makes the script idempotent — you can run it multiple times
    safely. The first run creates the tables; subsequent runs do nothing.
    This is important because you might run it after adding a new table
    definition without wanting to drop existing tables that already
    contain data.
    """
    cur = conn.cursor()
    for table_name, ddl in table_definitions:
        logger.info(f"Ensuring table exists: {table_name}")
        cur.execute(ddl)
    conn.commit()
    cur.close()
    logger.info(f"All {len(table_definitions)} tables verified.")
 
 
def verify_tables(conn, table_definitions: list):
    """
    Quick sanity check — queries information_schema to confirm each
    table actually exists after creation. This catches typos in schema
    names or permission issues that CREATE TABLE IF NOT EXISTS might
    silently swallow.
    """
    cur = conn.cursor()
    all_ok = True
    for table_name, _ in table_definitions:
        # table_name format: "schema.table" — split into parts
        schema, table = table_name.split(".")
        cur.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = %s AND table_name = %s;
            """,
            (schema, table),
        )
        count = cur.fetchone()[0]
        status = "OK" if count == 1 else "MISSING"
        logger.info(f"  {table_name}: {status}")
        if count != 1:
            all_ok = False
    cur.close()
    return all_ok
 
 
# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
 
def main():
    # Resolve the .env path relative to THIS script's location.
    # os.path.abspath(__file__) gives the full path to this .py file,
    # os.path.dirname() strips the filename to get the directory.
    # Structure: healthcare_equity/scripts/01_health_equity_schema_setup.py
    #            healthcare_equity/.env
    # So we go up one level (..) from scripts/ to healthcare_equity/
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(script_dir, "..", ".env")
    load_env(env_path)
 
    state = STATE_CONFIG[ACTIVE_STATE]
    logger.info(f"Setting up schema for: {state['name']} ({state['abbreviation']})")
    logger.info(f"State FIPS: {state['fips']}")
    logger.info()
 
    conn = get_conn()
 
    # Create raw tables first, then clean tables.
    # Order matters conceptually (raw before clean mirrors data flow)
    # even though there are no foreign key dependencies between them.
    logger.info("=== Creating health_raw tables ===")
    ensure_tables(conn, RAW_TABLES)
    logger.info()
 
    logger.info("=== Creating health_clean tables ===")
    ensure_tables(conn, CLEAN_TABLES)
    logger.info()
 
    # Verify everything landed correctly
    logger.info("=== Verification ===")
    all_tables = RAW_TABLES + CLEAN_TABLES
    ok = verify_tables(conn, all_tables)
    logger.info()
 
    if ok:
        logger.info("Schema setup complete. All tables verified.")
    else:
        logger.warning("WARNING: Some tables are missing. Check permissions and schema existence.")
 
    conn.close()
 
 
if __name__ == "__main__":
    main()