# Nick's Projects Workspace

## Owner
Nick Sumnicht — Senior Research Analyst at AIR
GitHub: nsumnicht

## Database
- PostgreSQL database: projects_db
- Connection: pg8000 ONLY — SQLAlchemy incompatible with pandas 1.3.4
- NEVER use SQLAlchemy for any database writes under any circumstances

## Schemas
- sports_raw / sports_clean — F1, NFL, and other sports projects
- health_raw / health_clean — Healthcare ED pipeline, Desert Index, 
  Price Transparency, Maternal Mortality, and all health projects

## Required Script Pattern (NON-NEGOTIABLE for all projects)
Every ingestion script MUST follow this exact pattern in this order:

1. load_env(path) — manually reads .env, NO python-dotenv dependency
2. get_conn() — returns pg8000 connection using os.environ values
3. ensure_table(conn) — CREATE TABLE IF NOT EXISTS
4. delete_existing_dataset(conn, source, dataset_id) — idempotent writes
5. batch insert via cursor.executemany() — batch_size=5000 standard
6. os.path.abspath(__file__) for ALL path resolution

## .env Format (used by all projects)
DB_HOST=localhost
DB_PORT=5432
DB_NAME=projects_db
DB_USER=projects_user
DB_PASSWORD=password

## JSONB Raw Table Pattern
All raw ingestion tables use this structure:
  source      TEXT NOT NULL
  dataset_id  TEXT NOT NULL
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
  payload     JSONB NOT NULL

## Key Dependencies
- pandas 1.3.4 (PINNED — never upgrade, breaks pg8000 compatibility)
- pg8000 for all DB connections and writes
- requests for HTTP calls
- json for JSONB serialization

## Project-Specific Dependencies

### NFL Project
- nflreadpy (installed from GitHub) — ALWAYS call .to_pandas() 
  immediately after any nflreadpy function call
  Example: df = nfl.load_injuries().to_pandas()
- nflreadpy returns Polars DataFrames by default

### F1 Project  
- Jolpica API (https://api.jolpi.ca/ergast/f1/)
- Rate limit: respect with time.sleep(0.5) between calls
- All endpoints paginate — use $limit/$offset pattern

### Healthcare Projects
- CDC PLACES via Socrata API (data.cdc.gov)
- Census ACS via Census API (api.census.gov)
- CMS Provider Data Catalog (data.cms.gov)
- EPA Walkability flat file

## Active Projects and Status

### ✅ Complete
- F1 Driver Performance Index (DPI) — sports_clean.f1_results

### 🔨 In Progress
- Healthcare ED Utilization Pipeline — health_raw/health_clean
- NFL Injury Rate Analysis — sports_raw schema set up, 
  ingestion scripts pending

### 📝 Prompts Ready (not yet started)
- Pantheon's Last Stand (Godot tower defense game)
- Portfolio Website (nsumnicht.github.io)
- Disney World Crowd Predictor
- Becky's Proposal Escape Room Box
- Colorado Healthcare Equity Tool (Desert Index + Price Transparency)

## Code Style Rules

### All Projects
- No bare except clauses — catch specific exceptions
- Type hints on all function signatures
- All TODO items marked # TODO: for easy Ctrl+F
- No magic strings — define constants at top of file
- logging module for all output (not print statements in production)
- os.path.join() for all file paths (Windows compatible)

### Personal Projects (teaching priority)
- Inline comments explain WHY not just WHAT
- Comments explain specific syntax in plain language
- Teaching is non-negotiable — Nick is actively learning

### Professional/Work Projects
- No teaching comments
- Professional tone throughout
- unittest only (no pytest) for Databricks compatibility

## File Naming Convention
01_ — schema setup or smoke test
02_ — data download or ingestion scripts
03_ — cleaning and transformation
04_ — analysis notebooks
05_ — output or reporting

## Notebooks
- Jupyter .ipynb format for all analysis
- Alternate markdown cells (narrative) with code cells
- Structure as blog-post quality portfolio pieces
- Every chart needs title, labeled axes, written interpretation

## What NOT To Do
- Never use SQLAlchemy
- Never use python-dotenv
- Never hardcode database credentials
- Never use pytest (use unittest)
- Never scrape Disney-owned domains directly
- Never scrape hospital websites directly for price data