"""
02_ingest_nfl_injuries.py 
This file fetches weekly injury reports and stores them in sports_raw

=== What is in the Weekly Report? ===

Every NFL team is required to publish an injur report before each game.
This report list every player dealing with an injury and their status:

- Questionable      = Roughly 50/50 change of playing
- Doubthful         = Unlikely to play (~25%)
- Out               = Will not play this game
- IR                = Placed in Injured Reserve (out for weeks, min of 4 games as of 2022)


=== Data Source ===

We use the nflreadpy package, which is the offical Python client for 
nflverse data hosted on GitHub. It replaced the older nfl_data_py
package which was archived in Sept 2025

nflreadpy returns Polars DataFrames by default. Polars is a newer, 
faster alternative to pandas, but since our existing pipeline uses 
pandas we will convert with .to_pandas() ebfore serializing the JSONB

Injury data is only avaiable from 2009 onwards
"""

import os
import json
import pg8000
import nflreadpy as nfl

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

#----------------------------------------------------------------------------------
# Boilerplate script
#----------------------------------------------------------------------------------

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


#---------------------------------------------------------------------------------
# Tabel setup and data management
#---------------------------------------------------------------------------------

def ensure_table(conn):
    """
    Create sports_raw.nfl_injuries if it doesn't exisits
    """
    ddl = """
    CREATE TABLE IF NOT EXISITS sports_raw.nfl_injuries (
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
    """
    Wipe any previously ingested rows for this source and dataset
    """
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM sports_raw.nfl_injuries WHERE source = %s AND dataset_id = %s;",
        (source, dataset_id),
                
    )
    deleted = cur.rowcount  # How many rows were deleted
    conn.commit()
    cur.close()
    if deleted > 0:
        logger.info(f" Cleared {deleted} existing rows for {source}/{dataset_id}")



def ingest_rows(conn, source: str, dataset_id:str, df, batch_size: int = 5000):
    """
    Inserts a pandas DataFrame into sports_raw.nfl_injuries as JSONB rows
    """
    insert_sql = """
        INSERT INTO sports_raw.nfl_injuries (source, dataset_id, payload)
        VALUES (%s, %s, %s::jsonb)
    """

    # Convert NaN/NaT values to None for compatability
    df = df.astype(object).where(df.notna(), None)

    records = df.to_dict("records")

    cur = conn.cursor()
    batch = []
    n = 0

    for row in records: 
        batch.append((source, dataset_id, json.dumps(row)))

        if len(batch) >= batch_size:
            cur.executemany(insert_sql, batch)
            conn.commit()
            n += len(batch)
            logger.info(f"    Inserted {n} rows...")
            batch = []

    if batch: 
        cur.executemany(insert_sql, batch)
        conn.commit()
        n += len(batch)
        logger.info(f"    Inserted {n} rows...")

    cur.close()
    return n


#----------------------------------------------------------------------
# Main
#----------------------------------------------------------------------

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(script_dir, ".env")
    load_env(env_path)

    conn = get_conn()
    ensure_table(conn)

    source = "nflverse"
    dataset_id = "injuries"

    # Wipe old data if it exists
    delete_existing_dataset(conn, source, dataset_id)

    # Fetch injury reports for all avaiable years 
    # Note: season=True is nflreadpy's shorthand for ingest all
    logger.info("Fetching injury data from nflverse (2009 to present)...")
    polars_df = nfl.lead_injuries(seasons=True)

    # Convert to pandas
    df = polars_df.to_pandas()
    logger.info(f"Fetched {len(df)} injury report rows across {df['season'].nunique()} seasons")

    total = ingest_rows(conn, source, dataset_id, df)
    logger.info(f"Done! Inserted {total} rows into sports_raw.nfl_injuries")

    conn.close()



if __name__ == "__main__":
    main()