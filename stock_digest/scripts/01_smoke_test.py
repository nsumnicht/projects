"""
Stage 1: check the environment before spending anything.

Verifies that the dependencies import, the watchlist parses, the secrets are
visible, and that the two free data sources answer for a single test ticker.
It makes no Claude API call and posts nothing to Discord, so running it costs
nothing and is safe to repeat.

Run it with:
    python scripts/01_smoke_test.py
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402

TEST_SYMBOL = "MU"
TEST_COMPANY = "Micron Technology"

# Names are checked for presence only. Nothing here ever prints a secret.
REQUIRED_SECRET_VARS = ("DISCORD_WEBHOOK_URL",)
# Only needed if the Claude summarization step is switched back on, which it
# is not by default. Its absence is normal and is not a failure.
OPTIONAL_SECRET_VARS = ("ANTHROPIC_API_KEY",)


def check_imports() -> bool:
    """Confirm every third-party package the pipeline needs is installed."""
    ok = True
    for module_name in ("yfinance", "feedparser", "requests", "anthropic"):
        try:
            __import__(module_name)
            logging.info("import %-12s ok", module_name)
        except ImportError as exc:
            logging.error("import %-12s FAILED: %s", module_name, exc)
            ok = False

    # pg8000 is optional: without it the run simply skips the Postgres archive.
    try:
        __import__("pg8000")
        logging.info("import %-12s ok", "pg8000")
    except ImportError:
        logging.warning("import %-12s missing, Postgres archiving will be skipped", "pg8000")

    return ok


def check_watchlist() -> bool:
    """Confirm watchlist.json parses and report how much it covers."""
    try:
        watchlist = common.load_watchlist()
    except (OSError, ValueError) as exc:
        logging.error("watchlist.json could not be read: %s", exc)
        return False

    sectors = watchlist.get("sectors", [])
    tickers = sum(len(sector.get("tickers", [])) for sector in sectors)
    events = sum(
        len(entry.get("events", []))
        for sector in sectors
        for entry in sector.get("tickers", [])
    )
    logging.info(
        "watchlist ok: %s sectors, %s tickers, %s tracked events",
        len(sectors),
        tickers,
        events,
    )
    return tickers > 0


def check_secrets() -> bool:
    """Report which secrets are visible, without ever printing their values."""
    all_present = True
    for name in REQUIRED_SECRET_VARS:
        value = os.environ.get(name, "")
        if value:
            logging.info("%s is set (%s characters)", name, len(value))
        else:
            logging.warning("%s is NOT set, the digest cannot be posted", name)
            all_present = False

    for name in OPTIONAL_SECRET_VARS:
        value = os.environ.get(name, "")
        if value:
            logging.info("%s is set (%s characters)", name, len(value))
        else:
            logging.info(
                "%s is not set, the optional Claude step stays off", name
            )

    if common.db_is_configured():
        logging.info("Database variables are set, the Postgres archive will run.")
    else:
        logging.info("Database variables are not set, the Postgres archive will skip.")

    return all_present


def check_free_sources() -> bool:
    """Hit both free data sources once for a single well known ticker."""
    # Imported here rather than at the top so a missing package is reported by
    # check_imports as a clear message instead of crashing on the import line.
    gather = _load_gather_module()
    if gather is None:
        return False

    ok = True

    try:
        prices = gather.fetch_price_data(TEST_SYMBOL)
        logging.info("yfinance ok: %s at $%s", TEST_SYMBOL, prices["price"])
    except Exception as exc:  # noqa: BLE001
        logging.error("yfinance failed for %s: %s", TEST_SYMBOL, exc)
        ok = False

    try:
        headlines = gather.fetch_headlines(TEST_SYMBOL, TEST_COMPANY)
        logging.info("Google News ok: %s headlines for %s", len(headlines), TEST_SYMBOL)
        for headline in headlines:
            logging.info("  %s", headline["title"][:100])
    except Exception as exc:  # noqa: BLE001
        logging.error("Google News failed for %s: %s", TEST_SYMBOL, exc)
        ok = False

    return ok


def _load_gather_module():
    """
    Import 02_gather_market_data.py by file path.

    A module name cannot start with a digit, so a plain import statement will
    not work on the numbered scripts. importlib loads it from its path instead,
    which lets the smoke test reuse the real fetch functions rather than
    duplicating them.
    """
    import importlib.util

    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "02_gather_market_data.py"
    )
    spec = importlib.util.spec_from_file_location("gather_market_data", path)
    if spec is None or spec.loader is None:
        logging.error("Could not load %s", path)
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    common.setup_logging()
    common.load_env()

    logging.info("Project folder: %s", common.project_dir())

    results = {
        "imports": check_imports(),
        "watchlist": check_watchlist(),
        "secrets": check_secrets(),
    }
    # Only worth hitting the network if the packages are actually installed.
    results["free sources"] = check_free_sources() if results["imports"] else False

    logging.info("---")
    for name, passed in results.items():
        logging.info("%-14s %s", name, "PASS" if passed else "FAIL")

    # Missing secrets are a warning during local setup, not a hard failure,
    # so they do not decide the exit code.
    blocking = ("imports", "watchlist", "free sources")
    if all(results[name] for name in blocking):
        logging.info("Smoke test passed. Safe to run stage 2.")
        return 0

    logging.error("Smoke test failed. Fix the items marked FAIL above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
