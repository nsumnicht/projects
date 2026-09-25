# Stock Digest Notes

Running notes and status. See `stock_digest_README.md` for setup.

## Status

✅ Watchlist config, 8 sectors, 21 tickers, 9 tracked events
✅ Stage 2 free gathering, verified end to end, 21 of 21 tickers succeeded
⬜ Stage 3 single batched Claude call: written and fallback-verified, but
   switched OFF. Nick opted out of paying for an API key, so the workflow
   skips it and `anthropic` is commented out of requirements.txt. The README
   has a section on turning it back on.
✅ Stage 4 Discord embeds, verified by dry run, 8 embeds across 4 messages
✅ GitHub Actions workflow, weekly cron plus manual dispatch
✅ Postgres archive to markets_raw.stock_digest_runs, verified locally
✅ Live Discord post verified 2026-09-24, 4 messages to the real channel
🔨 Pending: add the DISCORD_WEBHOOK_URL GitHub secret, then a manual run

## Decisions

**One Claude call per run, never one per ticker.** The whole watchlist goes in
a single prompt. Per ticker calls would repeat the system prompt 21 times for
no gain in quality. Measured at roughly 2 to 4 cents a run.

**Structured outputs rather than parsing prose.** Stage 3 uses
`output_config.format` with a JSON schema, so the reply is guaranteed to parse.
The schema returns a list of `{symbol, one_liner}` rather than an object keyed
by symbol, because JSON Schema cannot describe keys that change per run.

**Stage 3 always exits 0.** A missing one-liner is a far better outcome than a
missing digest, so every failure in the paid step degrades to posting the free
data with a note in the header.

**The AI step is off, so the header stays clean.** Stage 4 only adds the
"summaries are missing" note when stage 3 actually ran and failed. With the
step switched off there is no summaries file at all and the note is skipped,
rather than apologizing in every single weekly message.

**@everyone on every message.** Requested 2026-09-24. Discord ignores an
@everyone in webhook text unless the request sets `allowed_mentions`, so that
is sent explicitly and scoped to `everyone` only, which also stops a stray
@name in a headline from pinging a real user. The digest spans four messages,
so this is four notifications per week. Toggle with `MENTION_EVERYONE` at the
top of `04_post_to_discord.py`.

**Stages hand off through JSON files, not function calls.** Lets stage 4 be
re-run and iterated on without re-fetching or re-spending. Also means the
GitHub Actions log shows which stage failed.

**New `markets_raw` schema.** The workspace has `sports_raw` and `health_raw`
and market data is neither. Flagged rather than forced into an existing one.

**Two headlines shown in Discord, three sent to Claude.** Google News links
are roughly 300 character redirect URLs and Discord counts every character
against the 6000 per message limit. Three links per ticker pushed the digest
to six messages, two keeps it to four.

## Gotchas found while building

- `yfinance` is at 1.7.0, not 0.2.x. The first pin written was `<1.0` and was
  wrong. Requirements now say `>=1.7,<2.0`.
- System `python` on this machine is 3.8.5, and the Anthropic SDK 1.x needs
  3.10 or newer. Use `venv/Scripts/python.exe`, which is 3.11.2.
- Truncating an embed field with a plain string slice cuts through the middle
  of a markdown link and dumps raw URL into the message. Fields are now
  assembled line by line against the budget, so a headline is either included
  whole or dropped.
- The 7 day change has to be computed against the last close at or before one
  week ago, not the close exactly 7 rows back, because markets are shut at
  weekends.
- Google News RSS needs a browser-like User-Agent or it refuses the request.
- Curly quotes in headlines are correct UTF-8 in the stored JSON. They only
  look mangled in a Windows terminal.

## TODO

- [x] Post a real digest to Discord, verified 2026-09-24
- [x] Add `DISCORD_WEBHOOK_URL` as a repository secret
- [x] Commit and push, done 2026-09-24 as commit 111947c on main
- [ ] Trigger one manual `workflow_dispatch` run from the Actions tab and
      confirm the digest arrives and the @everyone ping fires once
- [ ] Refine the seed event dates, most are estimates
- [ ] Decide whether the Postgres archive is worth running locally on a
      schedule, given the Actions run cannot reach the database
