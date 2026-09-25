"""
Stage 2b: find legislation that is actually moving and affects the watchlist.

Reads the policy_areas and keywords on each sector in watchlist.json, asks the
free Congress.gov API what has changed recently, and keeps only bills that have
genuinely advanced. Writes data/legislation.json for stage 4 to display.

The filtering is the whole point of this script. Roughly 4,400 bills get some
kind of update in any given 10 day window, and the large majority of those
updates are "Referred to the Committee on ...", which is where most bills go to
die. A digest that reported every newly introduced bill would be pure noise, so
a bill only appears here once it has cleared a committee, been scheduled for a
floor vote, passed a chamber, or become law.

Needs a free API key from https://api.congress.gov/sign-up/ in the environment
as CONGRESS_API_KEY. If the key is absent this script exits 0 having written
nothing, and the digest simply omits the legislation section.

Run it with:
    python scripts/02b_gather_legislation.py
    python scripts/02b_gather_legislation.py --days 21
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402

# Constants.
API_KEY_VAR = "CONGRESS_API_KEY"
BILL_LIST_URL = "https://api.congress.gov/v3/bill"
BILL_DETAIL_URL = "https://api.congress.gov/v3/bill/{congress}/{bill_type}/{number}"
CONGRESS_GOV_WEB_URL = "https://www.congress.gov/bill/{congress}th-congress/{slug}/{number}"

PAGE_SIZE = 250          # the API maximum
# Coverage ceiling. This has to be large enough to fetch every bill in the
# lookback window, because the API's sort parameter cannot be relied on (see
# fetch_recent_bills). A 14 day window is around 4,500 bills, so 30 pages of
# 250 leaves comfortable headroom.
MAX_PAGES = 30
HTTP_TIMEOUT_SECONDS = 30
SLEEP_BETWEEN_CALLS = 0.15
MAX_RETRIES = 3
DEFAULT_LOOKBACK_DAYS = 14
MAX_BILLS_PER_SECTOR = 3
# Detail calls cost one request each, so cap how many we are willing to make
# even if an unusually busy week produces a long candidate list.
MAX_DETAIL_CALLS = 60

# A bill only counts as moving if its latest action starts with one of these.
# Matching on the start of the string avoids false positives from actions that
# merely mention a chamber in passing.
MOVED_ACTION_PREFIXES = (
    "reported by",
    "reported to",
    "ordered to be reported",
    "committee agreed to seek consideration",
    "placed on the union calendar",
    "placed on senate legislative calendar",
    "placed on the calendar",
    "passed house",
    "passed senate",
    "passed/agreed to in house",
    "passed/agreed to in senate",
    "resolving differences",
    "presented to president",
    "signed by president",
    "became public law",
    "public law",
    "veto",
)

# Actions that look like movement but are not. Checked first.
DEAD_END_ACTION_PREFIXES = (
    "referred to",
    "read twice and referred",
    "received in the senate and read twice",
    "introduced in",
    "sponsor introductory remarks",
    "motion to reconsider laid on the table",
)

# Maps the API's bill type codes to the slug used in a congress.gov web URL.
BILL_TYPE_SLUGS = {
    "hr": "house-bill",
    "s": "senate-bill",
    "hjres": "house-joint-resolution",
    "sjres": "senate-joint-resolution",
    "hconres": "house-concurrent-resolution",
    "sconres": "senate-concurrent-resolution",
    "hres": "house-resolution",
    "sres": "senate-resolution",
}


def get_api_key() -> str:
    """Return the Congress.gov API key, or an empty string when absent."""
    return os.environ.get(API_KEY_VAR, "").strip()


def request_json(url: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    GET a URL and return parsed JSON, retrying briefly on transient failures.

    Returns None rather than raising, because a single failed page should
    degrade the legislation section rather than end the digest run.
    """
    import requests

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, timeout=HTTP_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            logging.warning("Request failed (attempt %s): %s", attempt, exc)
            time.sleep(attempt)
            continue

        if response.status_code == 200:
            try:
                return response.json()
            except ValueError as exc:
                logging.warning("Response was not valid JSON: %s", exc)
                return None

        if response.status_code == 429:
            # The documented limit is generous, but back off properly if hit.
            logging.warning("Rate limited by Congress.gov, waiting.")
            time.sleep(5 * attempt)
            continue

        if response.status_code in (401, 403):
            logging.error(
                "Congress.gov rejected the API key (%s). Check %s.",
                response.status_code,
                API_KEY_VAR,
            )
            return None

        logging.warning("Unexpected status %s from %s", response.status_code, url)
        time.sleep(attempt)

    return None


def latest_action_text(bill: Dict[str, Any]) -> str:
    """Pull the latest action text out of a bill record, or an empty string."""
    action = bill.get("latestAction") or {}
    return (action.get("text") or "").strip()


def has_moved(bill: Dict[str, Any]) -> bool:
    """
    Decide whether a bill has actually advanced.

    Dead-end prefixes are checked first, because some real action texts contain
    a moving phrase later in the sentence while still describing a referral.
    """
    text = latest_action_text(bill).lower()
    if not text:
        return False
    if text.startswith(DEAD_END_ACTION_PREFIXES):
        return False
    return text.startswith(MOVED_ACTION_PREFIXES)


def moved_recently(bill: Dict[str, Any], days: int) -> bool:
    """
    Check that the advancing action itself happened inside the window.

    This matters more than it looks. The API's updateDate field changes on any
    metadata edit, not just a legislative action, so a bill whose last real
    action was months ago still turns up in a query for recently updated bills.
    Without this check the digest would report the same bill becoming law every
    week forever. A bill with no action date is dropped, because an undated
    action cannot be shown to be news.
    """
    action = bill.get("latestAction") or {}
    raw_date = (action.get("actionDate") or "").strip()
    if not raw_date:
        return False
    try:
        action_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
    except ValueError:
        return False
    age_days = (datetime.now(timezone.utc).date() - action_date).days
    # A future-dated action is odd but harmless, so allow it through.
    return age_days <= days


def match_sectors(
    title: str, sector_configs: List[Tuple[str, List[str], List[str]]]
) -> List[Tuple[str, str]]:
    """
    Return (sector_name, matched_keyword) for every sector this title hits.

    Matching is done on the lowercased title, which the list endpoint already
    gives us, so this costs no extra API calls. A bill can legitimately match
    more than one sector, for example a bill about both nuclear power and
    critical minerals.
    """
    lowered = title.lower()
    matches = []
    for sector_name, _policy_areas, keywords in sector_configs:
        for keyword in keywords:
            if keyword.lower() in lowered:
                matches.append((sector_name, keyword))
                break  # one match per sector is enough
    return matches


def fetch_recent_bills(api_key: str, days: int) -> List[Dict[str, Any]]:
    """
    Page through every bill updated in the lookback window.

    Deliberately no sort parameter. Congress.gov accepts sort=updateDate+desc
    but does not reliably honour it alongside fromDateTime: a 45 day query was
    observed returning the same mid-window date on both page 1 and page 25.
    That makes partial coverage actively dangerous, because an unsorted partial
    slice looks identical to a quiet week in Congress. So the only safe
    approach is to fetch the entire window and filter locally, and to complain
    loudly when the window is too big to fetch completely.

    Keep the lookback short for this reason. 14 days is about 4,500 bills,
    while 45 days is over 35,000 and cannot be covered.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT00:00:00Z"
    )
    bills: List[Dict[str, Any]] = []
    total_available: Optional[int] = None

    for page in range(MAX_PAGES):
        params = {
            "api_key": api_key,
            "limit": PAGE_SIZE,
            "offset": page * PAGE_SIZE,
            "fromDateTime": since,
        }
        payload = request_json(BILL_LIST_URL, params)
        if payload is None:
            break

        pagination = payload.get("pagination") or {}
        if total_available is None:
            total_available = pagination.get("count")
            capacity = MAX_PAGES * PAGE_SIZE
            if total_available and total_available > capacity:
                logging.warning(
                    "Congress.gov reports %s bills updated in the last %s days, "
                    "which is more than the %s this script can fetch. Results "
                    "will be incomplete. Use a shorter --days window.",
                    total_available,
                    days,
                    capacity,
                )

        batch = payload.get("bills", [])
        if not batch:
            break
        bills.extend(batch)

        # Stop as soon as the API says there is no next page.
        if not pagination.get("next"):
            break
        time.sleep(SLEEP_BETWEEN_CALLS)

    logging.info(
        "Fetched %s of %s bills updated since %s.",
        len(bills),
        total_available if total_available is not None else "unknown",
        since[:10],
    )
    return bills


def fetch_policy_area(api_key: str, bill: Dict[str, Any]) -> Optional[str]:
    """
    Fetch one bill's policy area, which the list endpoint does not include.

    This is the only per-bill request in the script, which is why it runs on
    the short post-filter candidate list rather than on everything.
    """
    url = BILL_DETAIL_URL.format(
        congress=bill.get("congress"),
        bill_type=(bill.get("type") or "").lower(),
        number=bill.get("number"),
    )
    payload = request_json(url, {"api_key": api_key})
    if payload is None:
        return None
    detail = payload.get("bill") or {}
    return ((detail.get("policyArea") or {}).get("name") or "").strip() or None


def build_web_url(bill: Dict[str, Any]) -> str:
    """Build a human-readable congress.gov link for the bill."""
    bill_type = (bill.get("type") or "").lower()
    slug = BILL_TYPE_SLUGS.get(bill_type)
    if not slug:
        # Fall back to the API URL, which is at least a working link.
        return bill.get("url", "")
    return CONGRESS_GOV_WEB_URL.format(
        congress=bill.get("congress"), slug=slug, number=bill.get("number")
    )


def gather(api_key: str, watchlist: Dict[str, Any], days: int) -> Dict[str, Any]:
    """Fetch, filter, and group legislation by watchlist sector."""
    sector_configs = [
        (
            sector.get("name", "Unknown"),
            sector.get("policy_areas", []),
            sector.get("keywords", []),
        )
        for sector in watchlist.get("sectors", [])
    ]

    all_bills = fetch_recent_bills(api_key, days)

    # Filter 1: has the bill actually advanced. This is by far the most
    # aggressive cut, and it costs nothing because the list response already
    # carries the latest action.
    moved = [bill for bill in all_bills if has_moved(bill)]
    logging.info("%s of %s bills have actually moved.", len(moved), len(all_bills))

    # Filter 1b: did it move inside the window, rather than months ago.
    recent = [bill for bill in moved if moved_recently(bill, days)]
    logging.info("%s of those moved within the last %s days.", len(recent), days)
    moved = recent

    # Filter 2: does the title touch a sector we care about. Also free.
    candidates: List[Tuple[Dict[str, Any], List[Tuple[str, str]]]] = []
    for bill in moved:
        matches = match_sectors(bill.get("title", ""), sector_configs)
        if matches:
            candidates.append((bill, matches))
    logging.info("%s of those match a watchlist sector.", len(candidates))

    # Filter 3: confirm with the policy area, which needs one request per bill.
    # A sector with an empty policy_areas list accepts anything.
    policy_areas_by_sector = {name: areas for name, areas, _kw in sector_configs}
    by_sector: Dict[str, List[Dict[str, Any]]] = {
        name: [] for name, _a, _k in sector_configs
    }
    detail_calls = 0

    for bill, matches in candidates:
        policy_area = None
        if detail_calls < MAX_DETAIL_CALLS:
            policy_area = fetch_policy_area(api_key, bill)
            detail_calls += 1
            time.sleep(SLEEP_BETWEEN_CALLS)

        for sector_name, keyword in matches:
            allowed = policy_areas_by_sector.get(sector_name, [])
            # Keep the bill when the sector has no policy filter, when we could
            # not determine the policy area, or when it is on the allow list.
            if allowed and policy_area and policy_area not in allowed:
                logging.debug(
                    "Dropped %s%s from %s: policy area %s not allowed.",
                    bill.get("type"),
                    bill.get("number"),
                    sector_name,
                    policy_area,
                )
                continue

            if len(by_sector[sector_name]) >= MAX_BILLS_PER_SECTOR:
                continue

            action = bill.get("latestAction") or {}
            by_sector[sector_name].append(
                {
                    "bill": "{}{}".format(bill.get("type", ""), bill.get("number", "")),
                    "congress": bill.get("congress"),
                    "title": (bill.get("title") or "").strip(),
                    "matched_keyword": keyword,
                    "policy_area": policy_area,
                    "latest_action": (action.get("text") or "").strip(),
                    "latest_action_date": action.get("actionDate"),
                    "introduced_date": bill.get("introducedDate"),
                    "url": build_web_url(bill),
                }
            )

    logging.info("Made %s detail requests.", detail_calls)
    total = sum(len(v) for v in by_sector.values())
    logging.info("Kept %s bills across %s sectors.", total, len(by_sector))

    return {
        "run_date": datetime.now(timezone.utc).date().isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lookback_days": days,
        "bills_scanned": len(all_bills),
        "bills_moved": len(moved),
        "by_sector": by_sector,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help="how many days back to look for bill activity",
    )
    args = parser.parse_args()

    common.setup_logging()
    common.load_env()

    out_path = common.data_path(common.LEGISLATION_FILENAME)

    api_key = get_api_key()
    if not api_key:
        logging.warning(
            "%s is not set, skipping the legislation section. Get a free key at "
            "https://api.congress.gov/sign-up/",
            API_KEY_VAR,
        )
        common.write_json(
            out_path, {"ok": False, "reason": "no API key set", "by_sector": {}}
        )
        return 0

    watchlist = common.load_watchlist()

    try:
        result = gather(api_key, watchlist, args.days)
    except Exception as exc:  # noqa: BLE001
        # Same reasoning as the other gathering stages: the legislation section
        # is an enhancement, so any failure here degrades it rather than
        # stopping the digest.
        logging.warning("Legislation gathering failed: %s", exc)
        common.write_json(out_path, {"ok": False, "reason": str(exc), "by_sector": {}})
        return 0

    result["ok"] = True
    common.write_json(out_path, result)
    logging.info("Wrote %s", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
