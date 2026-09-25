"""
Stage 4: build the Discord message and post it through a webhook.

Reads data/digest_raw.json (always) and data/digest_summaries.json (which may
say the AI step failed) and turns them into one embed per sector, colored by
how urgent that sector's nearest event is.

Discord caps a single webhook message at 10 embeds and 6000 characters across
all of them, so the embeds are packed into as many sequential messages as it
takes.

Run it with:
    python scripts/04_post_to_discord.py
    python scripts/04_post_to_discord.py --dry-run    (prints, posts nothing)
"""

import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402

# Constants.
WEBHOOK_URL_VAR = "DISCORD_WEBHOOK_URL"
WEBHOOK_USERNAME = "Weekly Market Digest"
HTTP_TIMEOUT_SECONDS = 20

# Ping the channel once per weekly digest. The mention goes on the first
# message only: the digest spans several messages, and pinging on each one
# would fire four notifications for a single weekly post.
# Set this to False for a silent post.
MENTION_EVERYONE = True
MENTION_TEXT = "@everyone"

# Discord's documented limits. We stay under the character cap deliberately so
# a rounding error in our own counting never produces a rejected message.
MAX_EMBEDS_PER_MESSAGE = 10
MAX_CHARS_PER_MESSAGE = 5800
MAX_FIELD_VALUE_CHARS = 1024
MAX_FIELDS_PER_EMBED = 25
SLEEP_BETWEEN_MESSAGES_SECONDS = 1.0
MAX_RATE_LIMIT_RETRIES = 3

# Google News links are redirect URLs of roughly 300 characters each, and
# Discord counts every one of those characters against the message limit. Two
# links per ticker keeps the digest to a few messages instead of six or seven.
# Stage 3 still sends Claude all three headlines, this cap is display only.
HEADLINES_IN_MESSAGE = 2
MAX_HEADLINE_TITLE_CHARS = 90

# Embed colors are integers, not CSS strings. 0x prefixed hex is the readable
# way to write them: red for something that just happened, orange for
# something about to happen, blue for business as usual.
COLOR_BY_URGENCY = {
    common.URGENCY_PAST: 0xE74C3C,      # red
    common.URGENCY_IMMINENT: 0xE67E22,  # orange
    common.URGENCY_UPCOMING: 0x3498DB,  # blue
    common.URGENCY_NONE: 0x95A5A6,      # grey
}
DEFAULT_COLOR = 0x95A5A6


def clean_for_markdown(text: str) -> str:
    """
    Make a headline safe to put inside a Discord markdown link.

    Square brackets and parentheses in the link text would close the markdown
    early and leave a mangled line, so swap them for their curly equivalents.
    """
    return (
        text.replace("[", "(").replace("]", ")").replace("\n", " ").strip()
    )


def format_price_line(ticker: Dict[str, Any]) -> str:
    """Build the price and 7 day change line, tolerating missing values."""
    price = ticker.get("price")
    change = ticker.get("change_7d_pct")

    price_text = "price unavailable" if price is None else "${:,.2f}".format(price)

    if change is None:
        change_text = "7d change unavailable"
    else:
        # An explicit plus sign makes a gain unmistakable at a glance.
        sign = "+" if change > 0 else ""
        change_text = "7d {}{:.1f}%".format(sign, change)

    return "{}  |  {}".format(price_text, change_text)


def format_events(events: List[Dict[str, Any]]) -> List[str]:
    """Render each tracked event as a single readable line."""
    lines = []
    for event in events:
        label = event.get("label", "event")
        status = event.get("status", "")
        notes = event.get("notes", "")
        line = "{}: {}".format(label, status)
        if notes:
            line += " ({})".format(notes)
        lines.append(line)
    return lines


def format_headline_lines(headlines: List[Dict[str, str]]) -> List[str]:
    """Render headlines as individual markdown link lines."""
    lines = []
    for headline in headlines[:HEADLINES_IN_MESSAGE]:
        title = clean_for_markdown(headline.get("title", ""))
        if not title:
            continue
        if len(title) > MAX_HEADLINE_TITLE_CHARS:
            title = title[: MAX_HEADLINE_TITLE_CHARS - 3] + "..."
        link = headline.get("link", "")
        lines.append("- [{}]({})".format(title, link) if link else "- {}".format(title))
    return lines


def build_field(ticker: Dict[str, Any], one_liner: Optional[str]) -> Dict[str, str]:
    """
    Build the embed field for a single ticker.

    The field value is assembled line by line against the 1024 character
    budget rather than built in full and then sliced. Slicing would happily
    cut through the middle of a markdown link and leave a wall of raw URL in
    the message, so instead a headline is either included whole or dropped.
    """
    # These lines carry the actual information, so they go in first.
    required_lines = [format_price_line(ticker)]
    required_lines.extend(format_events(ticker.get("events", [])))
    if one_liner:
        # Italics set the AI sentence apart from the raw numbers above it.
        required_lines.append("_{}_".format(one_liner))
    if ticker.get("errors"):
        required_lines.append("_data problem: {}_".format(ticker["errors"][0]))

    lines = list(required_lines)
    # The join adds one newline per line after the first, so count that here.
    used = sum(len(line) for line in lines) + max(0, len(lines) - 1)

    headline_lines = format_headline_lines(ticker.get("headlines", []))
    if not headline_lines:
        candidate = "_no headlines found this week_"
        if used + 1 + len(candidate) <= MAX_FIELD_VALUE_CHARS:
            lines.append(candidate)
    else:
        dropped = 0
        for line in headline_lines:
            if used + 1 + len(line) <= MAX_FIELD_VALUE_CHARS:
                lines.append(line)
                used += 1 + len(line)
            else:
                dropped += 1
        if dropped:
            note = "_{} more headline(s) omitted for length_".format(dropped)
            if used + 1 + len(note) <= MAX_FIELD_VALUE_CHARS:
                lines.append(note)

    value = "\n".join(lines)
    if len(value) > MAX_FIELD_VALUE_CHARS:
        # Only reachable if the required lines alone overflow, for example a
        # very long event label. Safe to slice here: no links are involved.
        value = value[: MAX_FIELD_VALUE_CHARS - 4] + " ..."

    return {
        "name": "{} ({})".format(ticker["symbol"], ticker["company"]),
        "value": value,
        "inline": False,
    }


def build_sector_embed(
    sector: Dict[str, Any], summaries: Dict[str, str]
) -> Optional[Dict[str, Any]]:
    """Build one embed for one sector, or None if the sector has no tickers."""
    fields = []
    for ticker in sector.get("tickers", [])[:MAX_FIELDS_PER_EMBED]:
        one_liner = summaries.get(ticker["symbol"].upper())
        fields.append(build_field(ticker, one_liner))

    if not fields:
        return None

    embed: Dict[str, Any] = {
        "title": sector.get("name", "Unknown sector"),
        "color": COLOR_BY_URGENCY.get(sector.get("urgency"), DEFAULT_COLOR),
        "fields": fields,
    }

    sector_event_lines = format_events(sector.get("sector_events", []))
    if sector_event_lines:
        embed["description"] = "\n".join(sector_event_lines)

    return embed


def embed_size(embed: Dict[str, Any]) -> int:
    """
    Count the characters Discord counts toward the per-message limit.

    Discord totals the title, description, field names, and field values
    across every embed in the message. Colors and flags do not count.
    """
    size = len(embed.get("title", "")) + len(embed.get("description", ""))
    for field in embed.get("fields", []):
        size += len(field.get("name", "")) + len(field.get("value", ""))
    return size


def chunk_embeds(embeds: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """
    Split embeds into groups that each fit inside one Discord message.

    A group is closed when adding the next embed would break either the count
    limit or the character limit.
    """
    chunks: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    current_size = 0

    for embed in embeds:
        size = embed_size(embed)
        too_many = len(current) >= MAX_EMBEDS_PER_MESSAGE
        too_big = current and (current_size + size) > MAX_CHARS_PER_MESSAGE

        if too_many or too_big:
            chunks.append(current)
            current = []
            current_size = 0

        current.append(embed)
        current_size += size

    if current:
        chunks.append(current)
    return chunks


def post_message(webhook_url: str, payload: Dict[str, Any]) -> bool:
    """
    Post one message to the webhook, retrying if Discord rate limits us.

    Discord answers a successful webhook post with 204 No Content. A 429 means
    slow down, and the body tells us for how long.
    """
    import requests

    for attempt in range(1, MAX_RATE_LIMIT_RETRIES + 1):
        try:
            response = requests.post(
                webhook_url, json=payload, timeout=HTTP_TIMEOUT_SECONDS
            )
        except requests.RequestException as exc:
            logging.error("Webhook post failed on attempt %s: %s", attempt, exc)
            return False

        if response.status_code in (200, 204):
            return True

        if response.status_code == 429:
            # retry_after comes back in seconds as a float.
            try:
                wait = float(response.json().get("retry_after", 5))
            except ValueError:
                wait = 5.0
            logging.warning("Rate limited by Discord, waiting %.1fs.", wait)
            time.sleep(wait + 0.5)
            continue

        logging.error(
            "Discord rejected the message (%s): %s",
            response.status_code,
            response.text[:500],
        )
        return False

    logging.error("Gave up after %s rate limit retries.", MAX_RATE_LIMIT_RETRIES)
    return False


def build_header(digest: Dict[str, Any], summary_file: Dict[str, Any]) -> str:
    """
    Build the short line of text that sits above the first batch of embeds.

    The note about missing summaries only appears when the AI step actually
    ran and failed. When that step is switched off entirely, which is the
    normal setup here, there is nothing to apologize for and the header stays
    clean rather than carrying the same complaint every single week.
    """
    header = "**Weekly watchlist digest, week of {}**".format(digest.get("run_date"))
    if summary_file.get("attempted") and not summary_file.get("ok"):
        header += "\n_Plain-English summaries are missing this week: {}_".format(
            summary_file.get("reason", "unknown reason")
        )
    return header


def build_content(
    digest: Dict[str, Any],
    summary_file: Dict[str, Any],
    index: int,
    total: int,
) -> str:
    """
    Build the plain text line that sits above the embeds in one message.

    The first message carries the real header. Later messages get a short
    continuation line instead, so that a reader who sees message 3 on its own
    still knows what it belongs to.
    """
    if index != 1:
        # Continuation messages never carry the mention, so the whole digest
        # produces exactly one notification.
        return "_Weekly watchlist digest, continued ({} of {})_".format(index, total)

    header = build_header(digest, summary_file)
    if MENTION_EVERYONE:
        # The mention goes first so it leads the notification preview.
        return "{} {}".format(MENTION_TEXT, header)
    return header


def main() -> int:
    common.setup_logging()
    common.load_env()

    dry_run = "--dry-run" in sys.argv

    digest = common.read_json(common.data_path(common.RAW_FILENAME))
    if digest is None:
        logging.error("No raw digest found, run stage 2 first.")
        return 1

    # The summaries file is optional by design. If stage 3 never ran or gave
    # up, we still post everything the free sources gathered. A file that is
    # not there at all means the step is switched off, which is the normal
    # setup, so it is not reported as a problem.
    summary_file = common.read_json(common.data_path(common.SUMMARY_FILENAME))
    if summary_file is None:
        summary_file = {"attempted": False, "ok": False, "summaries": {}}
    else:
        summary_file["attempted"] = True
    summaries = {
        symbol.upper(): text
        for symbol, text in (summary_file.get("summaries") or {}).items()
    }

    embeds = []
    for sector in digest.get("sectors", []):
        embed = build_sector_embed(sector, summaries)
        if embed is not None:
            embeds.append(embed)

    if not embeds:
        logging.error("Nothing to post, every sector came back empty.")
        return 1

    chunks = chunk_embeds(embeds)
    logging.info("Built %s embeds across %s message(s).", len(embeds), len(chunks))

    webhook_url = os.environ.get(WEBHOOK_URL_VAR, "")
    if dry_run:
        for index, chunk in enumerate(chunks, start=1):
            print("----- message {} of {} -----".format(index, len(chunks)))
            print(build_content(digest, summary_file, index, len(chunks)))
            print(json.dumps(chunk, indent=2, ensure_ascii=False))
        return 0

    if not webhook_url:
        logging.error("%s is not set, cannot post.", WEBHOOK_URL_VAR)
        return 1

    failures = 0
    for index, chunk in enumerate(chunks, start=1):
        payload: Dict[str, Any] = {
            "username": WEBHOOK_USERNAME,
            "embeds": chunk,
            "content": build_content(digest, summary_file, index, len(chunks)),
            # Discord ignores an @everyone in webhook text unless the request
            # explicitly allows that kind of mention, and only the first
            # message carries one. Everything else parses no mentions at all,
            # so a stray @name in a headline can never ping a real person.
            "allowed_mentions": {
                "parse": ["everyone"] if (MENTION_EVERYONE and index == 1) else []
            },
        }

        if post_message(webhook_url, payload):
            logging.info("Posted message %s of %s.", index, len(chunks))
        else:
            failures += 1
            logging.error("Message %s of %s failed to post.", index, len(chunks))

        # Space the posts out so Discord does not rate limit a long digest.
        if index < len(chunks):
            time.sleep(SLEEP_BETWEEN_MESSAGES_SECONDS)

    if failures:
        logging.error("%s of %s messages failed.", failures, len(chunks))
        return 1

    logging.info("Digest posted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
