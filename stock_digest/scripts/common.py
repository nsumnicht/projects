"""
Shared helpers for the weekly stock digest.

Every numbered script in this folder imports from here so that the .env
loading, database connection, path resolution, and event date math live in
exactly one place. This file is deliberately not numbered because it is a
library, not a runnable stage of the pipeline.
"""

import json
import logging
import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

# Constants live at the top of the file so there are no magic strings buried
# in the logic below. If a path or a threshold needs to change, it changes here.
ENV_FILENAME = ".env"
WATCHLIST_FILENAME = "watchlist.json"
DATA_DIRNAME = "data"
RAW_FILENAME = "digest_raw.json"
SUMMARY_FILENAME = "digest_summaries.json"
LEGISLATION_FILENAME = "legislation.json"

# Event urgency buckets. These drive the Discord embed colors later.
URGENCY_PAST = "past"          # the stored date is behind us
URGENCY_IMMINENT = "imminent"  # within IMMINENT_WINDOW_DAYS from today
URGENCY_UPCOMING = "upcoming"  # further out than the imminent window
URGENCY_NONE = "none"          # no events tracked for this ticker

IMMINENT_WINDOW_DAYS = 14
# A date that passed more than this many days ago stops being news and is
# treated as stale, so an old seed date does not keep an embed red forever.
RECENTLY_PAST_WINDOW_DAYS = 45

# Database settings. The schema is new for this project: sports_raw and
# health_raw already exist for the other projects, and market data is neither.
DB_SCHEMA = "markets_raw"
DB_TABLE = "stock_digest_runs"
DB_SOURCE = "stock_digest"
BATCH_SIZE = 5000

# Every required database variable must be present before we try to connect.
# In GitHub Actions none of them are set, which is how the database step knows
# to skip itself instead of erroring out.
REQUIRED_DB_VARS = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")


def project_dir() -> str:
    """
    Return the stock_digest project folder as an absolute path.

    __file__ is the path to this file as Python loaded it, which can be
    relative depending on how the script was launched. os.path.abspath turns
    it into a full path, dirname strips off the filename to leave the folder
    (scripts/), and the second dirname climbs one level up to stock_digest/.
    """
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(scripts_dir)


def data_path(filename: str) -> str:
    """Return an absolute path to a file inside the project data folder."""
    return os.path.join(project_dir(), DATA_DIRNAME, filename)


def setup_logging(level: int = logging.INFO) -> None:
    """
    Configure the logging module once for the calling script.

    Using logging instead of print gives us timestamps and severity levels,
    which is what makes a GitHub Actions log readable weeks later when
    something has broken and you are trying to work out when it started.
    """
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_env(path: Optional[str] = None) -> None:
    """
    Read .env files by hand and copy their values into os.environ.

    We do this manually rather than with python-dotenv so the project has one
    fewer dependency. Values already present in the real environment win,
    because in GitHub Actions the secrets arrive as real environment variables
    and there is no .env file at all.

    With no path given, two files are read in order: stock_digest/.env first,
    then the workspace root .env one level up. First value seen wins, so a key
    set for this project overrides the shared workspace one. The root file is
    where the DB_ variables already live for the other projects.
    """
    if path is None:
        workspace_root = os.path.dirname(project_dir())
        for candidate in (
            os.path.join(project_dir(), ENV_FILENAME),
            os.path.join(workspace_root, ENV_FILENAME),
        ):
            _load_env_file(candidate)
        return

    _load_env_file(path)


def _load_env_file(path: str) -> None:
    """Load a single .env file, doing nothing if it is not there."""
    if not os.path.exists(path):
        logging.debug("No .env file at %s, relying on the real environment.", path)
        return

    with open(path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            # Skip blank lines and comments.
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            # split with maxsplit=1 so a value containing an equals sign
            # survives intact, which matters for API keys and webhook URLs.
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def db_is_configured() -> bool:
    """Return True only when every database variable is present."""
    return all(os.environ.get(name) for name in REQUIRED_DB_VARS)


def get_conn():
    """
    Return a pg8000 connection built from environment variables.

    pg8000 is imported inside the function rather than at the top of the file
    so that the Claude and Discord stages still run on a machine where pg8000
    is not installed, for example a bare GitHub Actions runner.
    """
    import pg8000.dbapi

    return pg8000.dbapi.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        database=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


def load_watchlist(path: Optional[str] = None) -> Dict[str, Any]:
    """Read watchlist.json and return it as a plain Python dictionary."""
    if path is None:
        path = os.path.join(project_dir(), WATCHLIST_FILENAME)
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def read_json(path: str) -> Optional[Dict[str, Any]]:
    """Read a JSON file, returning None if it is missing or unreadable."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        logging.warning("Could not read %s: %s", path, exc)
        return None


def write_json(path: str, payload: Dict[str, Any]) -> None:
    """Write a dictionary to disk as indented JSON, creating the folder if needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def classify_event(event: Dict[str, Any], today: Optional[date] = None) -> Dict[str, Any]:
    """
    Add status, days_until, and urgency fields to one event.

    This is pure date arithmetic, no AI involved. Subtracting two date objects
    gives a timedelta, and .days pulls the whole number of days out of it.
    A positive number means the event is ahead of us, negative means behind.
    """
    if today is None:
        today = date.today()

    classified = dict(event)  # copy so we never mutate the watchlist in memory
    raw_date = event.get("date", "")

    try:
        event_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
    except ValueError:
        # A malformed date in watchlist.json should not take down the run.
        classified["status"] = "date could not be read, check watchlist.json"
        classified["days_until"] = None
        classified["urgency"] = URGENCY_NONE
        return classified

    days_until = (event_date - today).days
    classified["days_until"] = days_until

    if days_until > IMMINENT_WINDOW_DAYS:
        classified["status"] = "upcoming ({} days)".format(days_until)
        classified["urgency"] = URGENCY_UPCOMING
    elif days_until > 0:
        classified["status"] = "upcoming ({} days)".format(days_until)
        classified["urgency"] = URGENCY_IMMINENT
    elif days_until == 0:
        classified["status"] = "today"
        classified["urgency"] = URGENCY_IMMINENT
    elif abs(days_until) <= RECENTLY_PAST_WINDOW_DAYS:
        classified["status"] = "date has passed {} days ago, see headlines".format(
            abs(days_until)
        )
        classified["urgency"] = URGENCY_PAST
    else:
        # Long past. Flag it so you know to edit watchlist.json by hand,
        # but stop treating it as urgent news.
        classified["status"] = "date passed {} days ago, needs updating in watchlist.json".format(
            abs(days_until)
        )
        classified["urgency"] = URGENCY_UPCOMING

    return classified


def worst_urgency(events: List[Dict[str, Any]]) -> str:
    """
    Return the most attention-grabbing urgency across a list of events.

    Ordering is past, then imminent, then upcoming, then none. A sector embed
    takes its color from this, so one imminent event colors the whole block.
    """
    ranked = [URGENCY_PAST, URGENCY_IMMINENT, URGENCY_UPCOMING, URGENCY_NONE]
    present = set(event.get("urgency", URGENCY_NONE) for event in events)
    for urgency in ranked:
        if urgency in present:
            return urgency
    return URGENCY_NONE


def iter_tickers(digest: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """
    Flatten the digest into (sector_name, ticker_record) pairs.

    Several stages want to walk every ticker regardless of sector, and this
    saves writing the same nested for loop three times.
    """
    pairs: List[Tuple[str, Dict[str, Any]]] = []
    for sector in digest.get("sectors", []):
        for ticker in sector.get("tickers", []):
            pairs.append((sector.get("name", "Unknown"), ticker))
    return pairs
