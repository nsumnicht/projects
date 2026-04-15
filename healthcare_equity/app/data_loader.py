import os
import pg8000
import pandas as pd



def load_env(path: str) -> None: 
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing .env file at: {path}")
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: 
                continue
            k,v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def get_conn():
    return pg8000.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        database=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


def load_desert_index() -> pd.DataFrame:
    conn = get_conn()
    cur = conn.cursor()
    sql = """
    SELECT
        tract_geoid,
        state_code,
        county_fips,
        county_name,
        total_population,
        drive_time_minutes,
        physician_ratio,
        uninsured_rate,
        poverty_rate,
        drive_time_score,
        physician_score,
        uninsured_score,
        poverty_score,
        desert_score,
        desert_tier
    FROM health_clean.desert_index
    WHERE state_code = 'CO'
    """

    cur.execute(sql)

    rows = cur.fetchall()
    columns = [desc[0] for desc in cur.description]

    conn.close()
    return pd.DataFrame(rows, columns=columns)


def load_hospitals() -> pd.DataFrame:
    conn = get_conn()
    cur = conn.cursor()
    sql = """ 
    SELECT
        hospital_ccn, 
        hospital_name, 
        city, 
        state_code, 
        latitude,
        longitude, 
        hospital_type,
        ownership,
        emergency_services, 
        bed_count, 
        pra_compliance_grade,
        cms_enforcement_count
    FROM health_raw.hospital_registry
    WHERE state_code = 'CO'
    """

    cur.execute(sql)

    rows = cur.fetchall()
    columns = [desc[0] for desc in cur.description]

    conn.close()
    return pd.DataFrame(rows, columns=columns)


def load_all(env_path: str) -> dict:
    load_env(env_path)
    return {
        "desert": load_desert_index(),
        "hospitals": load_hospitals(),
    }