"""
Stage 3: the one and only paid step.

Reads data/digest_raw.json and makes a single batched call to the Claude API
that covers every ticker at once. Claude returns one plain-English sentence per
ticker, which stage 4 drops into the Discord message.

Two rules shape this file:

  1. Exactly one API call per weekly run. Looping per ticker would be roughly
     twenty times the cost for no extra quality, since the prompt overhead
     would be repeated every time.
  2. This script always exits 0. If the API call fails for any reason, it
     writes a summaries file marked as unavailable and lets the digest go out
     with just the free data. A missing one-liner is a far better outcome than
     a missing digest.

Run it with:
    python scripts/03_summarize_with_claude.py
"""

import json
import logging
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402

# Constants.
MODEL_ID = "claude-sonnet-5"
MAX_TOKENS = 8000
EFFORT = "low"  # this is a summarization task, not a reasoning problem
API_KEY_VAR = "ANTHROPIC_API_KEY"
HEADLINES_IN_PROMPT = 3

SYSTEM_PROMPT = (
    "You write a weekly stock watchlist digest for one casual investor. "
    "He is smart but not a finance professional, so never use unexplained "
    "jargon. Write as if you were explaining it to a friend over coffee.\n\n"
    "For each ticker you are given the current price, the 7 day percent "
    "change, any recent news headlines, and the status of any catalyst event "
    "being tracked. Return exactly one sentence per ticker that answers two "
    "questions: what actually changed this week, and does it affect the "
    "reason for holding or watching the stock.\n\n"
    "Rules:\n"
    "- One sentence per ticker, at most about 30 words.\n"
    "- If nothing meaningful happened, say so plainly. Quiet weeks are normal "
    "and a boring honest sentence beats an invented story.\n"
    "- Never invent facts. Use only the price moves, headlines, and event "
    "statuses provided. If the data is thin, say the data is thin.\n"
    "- Do not give buy or sell advice and do not predict prices.\n"
    "- A price move under about 3 percent is noise, not news.\n"
    "- Plain words only. No tickers-speak, no emoji, no markdown formatting.\n"
    "- Return one entry for every ticker symbol in the input, even the quiet ones."
)

# Structured outputs constrain Claude's reply to this exact shape, which means
# the parsing below cannot fail on a stray sentence of preamble. The result is
# a list rather than an object keyed by symbol because JSON Schema cannot
# describe object keys that change from run to run.
RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "summaries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "one_liner": {"type": "string"},
                },
                "required": ["symbol", "one_liner"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["summaries"],
    "additionalProperties": False,
}


def build_prompt_payload(digest: Dict[str, Any]) -> Dict[str, Any]:
    """
    Trim the raw digest down to just what Claude needs to see.

    The raw file carries fields the model has no use for, for example the
    52 week range and the internal error strings. Leaving them out keeps the
    prompt smaller, which directly keeps the cost down.
    """
    compact: List[Dict[str, Any]] = []

    for sector_name, ticker in common.iter_tickers(digest):
        compact.append(
            {
                "symbol": ticker["symbol"],
                "company": ticker["company"],
                "sector": sector_name,
                "price": ticker.get("price"),
                "change_7d_pct": ticker.get("change_7d_pct"),
                "events": [
                    {
                        "label": event.get("label"),
                        "date": event.get("date"),
                        "status": event.get("status"),
                        "notes": event.get("notes", ""),
                    }
                    for event in ticker.get("events", [])
                ],
                "headlines": [
                    headline.get("title", "")
                    for headline in ticker.get("headlines", [])[:HEADLINES_IN_PROMPT]
                ],
            }
        )

    return {"week_of": digest.get("run_date"), "tickers": compact}


def call_claude(payload: Dict[str, Any]) -> Dict[str, str]:
    """
    Make the single batched API call and return a symbol to sentence mapping.

    Raises on failure. The caller is responsible for turning that into a
    graceful fallback.
    """
    import anthropic

    # The Anthropic client reads ANTHROPIC_API_KEY from the environment on its
    # own, so the key is never written into the code or passed around.
    client = anthropic.Anthropic()

    user_content = (
        "Here is this week's watchlist data as JSON. Write one sentence for "
        "each of the {count} tickers.\n\n{data}".format(
            count=len(payload["tickers"]),
            data=json.dumps(payload, indent=2, ensure_ascii=False),
        )
    )
    messages = [{"role": "user", "content": user_content}]

    # Log the input size before spending anything. count_tokens is free and
    # tells you exactly what the call is about to cost on the input side.
    try:
        counted = client.messages.count_tokens(
            model=MODEL_ID, system=SYSTEM_PROMPT, messages=messages
        )
        logging.info("Prompt measures %s input tokens.", counted.input_tokens)
    except Exception as exc:  # noqa: BLE001
        # Token counting is a convenience, never a reason to abort the run.
        logging.debug("Token counting skipped: %s", exc)

    response = client.messages.create(
        model=MODEL_ID,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=messages,
        output_config={
            "effort": EFFORT,
            "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
        },
    )

    # Claude can decline a request outright. Check before reading the content.
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined the request, no summaries produced.")

    usage = response.usage
    logging.info(
        "Claude usage: %s input tokens, %s output tokens.",
        usage.input_tokens,
        usage.output_tokens,
    )
    logging.info("Estimated cost this run: $%.4f", estimate_cost(usage))

    # output_config.format guarantees the first text block is valid JSON
    # matching RESPONSE_SCHEMA, so this parse is safe.
    text = next(block.text for block in response.content if block.type == "text")
    parsed = json.loads(text)

    return {
        item["symbol"].strip().upper(): item["one_liner"].strip()
        for item in parsed.get("summaries", [])
    }


def estimate_cost(usage: Any) -> float:
    """
    Work out roughly what this call cost, in dollars.

    Sonnet 5 is priced at $2.00 per million input tokens and $10.00 per million
    output tokens. Thinking tokens are billed as output tokens and are already
    included in usage.output_tokens.
    """
    input_rate_per_token = 2.00 / 1_000_000
    output_rate_per_token = 10.00 / 1_000_000
    return (
        usage.input_tokens * input_rate_per_token
        + usage.output_tokens * output_rate_per_token
    )


def main() -> int:
    common.setup_logging()
    common.load_env()

    raw_path = common.data_path(common.RAW_FILENAME)
    digest = common.read_json(raw_path)
    out_path = common.data_path(common.SUMMARY_FILENAME)

    if digest is None:
        logging.error("No raw digest at %s, run stage 2 first.", raw_path)
        common.write_json(
            out_path,
            {"ok": False, "reason": "no raw digest found", "summaries": {}},
        )
        return 0

    if not os.environ.get(API_KEY_VAR):
        logging.warning(
            "%s is not set, the digest will go out without AI one-liners.", API_KEY_VAR
        )
        common.write_json(
            out_path,
            {"ok": False, "reason": "no API key set", "summaries": {}},
        )
        return 0

    payload = build_prompt_payload(digest)
    logging.info("Summarizing %s tickers in a single call.", len(payload["tickers"]))

    try:
        summaries = call_claude(payload)
    except Exception as exc:  # noqa: BLE001
        # Deliberately broad. Rate limits, network drops, schema surprises, and
        # SDK version differences all land here, and every one of them should
        # degrade to a digest without one-liners rather than no digest at all.
        logging.warning("Claude call failed, falling back to raw data only: %s", exc)
        common.write_json(
            out_path, {"ok": False, "reason": str(exc), "summaries": {}}
        )
        return 0

    # A missing ticker is not an error, stage 4 simply omits that one-liner.
    expected = {ticker["symbol"] for _sector, ticker in common.iter_tickers(digest)}
    missing = sorted(expected - set(summaries))
    if missing:
        logging.warning("Claude returned no line for: %s", ", ".join(missing))

    common.write_json(
        out_path, {"ok": True, "model": MODEL_ID, "summaries": summaries}
    )
    logging.info("Wrote %s one-liners to %s", len(summaries), out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
