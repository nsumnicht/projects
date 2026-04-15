"""
01_nfl_schema_setup.py
This file creates raw tables for the NFL injury analysis project

=== TABLE DESIGN ===

Each table follows the same four column pattern used across the project: 

    source      TEXT    - Identifies the data provider (nflverse). Useful if we ever ingest data from another source

    dataset_id  TEXT    - Identifies the logical dataset within that source

    ingested_at TIMESTAMPTZ - Automatic time stamp

    payload     JSONB   - The actual records, stored as JSON object
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

#------------------------------------------------------------------
# Boilerplate: .env loading and database connection
# (Identical across all project scrpits)
#------------------------------------------------------------------

def load_env(path: str) -> None:
    """
    Tiny .env loader - avoids adding python-dotenv as a dependency.
    os.environ.setdefault() means an already-set env var won't be
    overwritten
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
    """Return a pg 8000 connection using credentials fomr the .env"""
    return pg8000.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        database=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )

#---------------------------------------------------------------
# Table creat helpers
#---------------------------------------------------------------

TABLE_DDLS = {
    "sports_raw.nfl_injuries": """
        CREATE TABLE IF NOT EXIST sports_raw.nfl_injuries (
            source      TEXT        NOT NULL,
            dataset_id  TEXT        NOT NULL,
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
            payload     JSONB       NOT NULL
        );
    """,

    "sports_raw.nfl_schedules": """
        CREATE TABLE IF NOT EXIST sports_raw.nfl_schedules (
            source      TEXT        NOT NULL,
            dataset_id  TEXT        NOT NULL,
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
            payload     JSONB       NOT NULL
        );
    """, 

    "sports_raw.nfl_rosters": """
        CREATE TABLE IF NOT EXIST sports_raw.nfl_rosters (
            source      TEXT        NOT NULL,
            dataset_id  TEXT        NOT NULL,
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
            payload     JSONB       NOT NULL
        );
    """, 

    "sports_raw.nfl_drafts": """
        CREATE TABLE IF NOT EXIST sports_raw.nfl_drafts (
            source      TEXT        NOT NULL,
            dataset_id  TEXT        NOT NULL,
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
            payload     JSONB       NOT NULL
        );
    """,    
}


def ensure_tables(conn):
    """
    Create all NFL raw tables if they don't already exist
    """
    cur = conn.cursor()
    for table_name, ddl in TABLE_DDLS.items():
        logger.info(f"Ensuring table exists: {table_name}")
        cur.execute(ddl)
    conn.commit()
    cur.close()
    logger.info("All tables ready.")


#-------------------------------------------------------------------
# Main
#-------------------------------------------------------------------

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(script_dir, ".env")
    load_env(env_path)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("CREATE SCHEMA IF NOT EXISTS sports_raw")
    conn.commit()
    cur.close()

    ensure_tables(conn)
    conn.close()
    logger.info("Schema setup complete")


if __name__ == "__main__":
    main()