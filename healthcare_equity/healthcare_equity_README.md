# Healthcare Equity Tool — Colorado

A combined public-facing tool analyzing:
1. **Healthcare Desert Index** — which Colorado communities have the worst healthcare access
2. **Hospital Price Transparency** — how honestly Colorado hospitals publish their prices

## Directory Structure

```
healthcare_equity/
├── .env
├── README.md
├── scripts/
│   ├── 01_health_equity_schema_setup.py
│   ├── 02_*                              (ingestion scripts)
│   └── 03_*                              (cleaning notebooks)
├── notebooks/
│   ├── 02_compute_desert_index.ipynb
│   └── 04_equity_analysis.ipynb
├── reference/
│   └── procedure_name_map.csv
└── app/
    ├── app.py
    ├── callbacks.py
    ├── data_loader.py
    ├── map_utils.py
    ├── style.py
    └── assets/
        └── custom.css
```

## Pipeline Architecture (Medallion Pattern)

```
health_raw (Bronze)          health_clean (Silver/Gold)
├── hospital_registry        ├── desert_index
├── hdi_rows                 ├── hospital_equity_profile
├── transparency_rows        └── procedure_prices
├── price_line_items
├── cdc_rows (existing)
├── census_rows (existing)
└── epa_rows (existing)
```

## Script Numbering

| Script | Purpose |
|--------|---------|
| `scripts/01_health_equity_schema_setup.py` | Create all tables (run once) |
| `scripts/02_*` | Data ingestion scripts (download + ingest to health_raw) |
| `notebooks/02_*` | Cleaning + computation notebooks (health_raw → health_clean) |
| `notebooks/04_equity_analysis.ipynb` | Combined Desert + Transparency analysis |
| `app/` | Plotly Dash web application |

## State Expansion

All tables include `state_code`. To add a new state:
1. Add an entry to `STATE_CONFIG` in `scripts/01_health_equity_schema_setup.py`
2. Set `ACTIVE_STATE` to the new state key
3. Run the ingestion scripts — same code, different config

## Setup

Uses the project root `.env` file (one directory up):
```
DB_HOST=localhost
DB_PORT=5432
DB_NAME=projects_db
DB_USER=projects_user
DB_PASSWORD=password
```
