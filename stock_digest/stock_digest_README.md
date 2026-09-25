# Weekly Stock and Sector News Digest

A weekly digest of a personal stock watchlist, posted automatically to a
Discord channel. Every data source is free, and as currently configured the
whole thing costs nothing at all to run.

There is an optional Claude summarization step that adds a plain-English
one-liner per ticker. It is switched off by default because it is the only
part that would cost money. See the optional section near the bottom.

Runs itself every Sunday evening through GitHub Actions. Nothing to click.

## What it does, in order

1. Reads `watchlist.json`, the single source of truth for what gets tracked.
2. For each ticker, pulls the current price, the 7 day percent change, and the
   52 week high and low from `yfinance`, which scrapes Yahoo Finance and needs
   no API key.
3. For each ticker, pulls 3 recent headlines from the Google News RSS feed,
   parsed with `feedparser`. Also free, also no key.
4. Compares today's date to each event date in the watchlist and labels it
   upcoming or passed. This is plain date arithmetic, no AI involved.
5. Builds one Discord embed per sector, colored by how urgent that sector's
   nearest event is, and posts it through a webhook.

Every one of those steps is free. There is no API key, no account, and no
billing anywhere in the default pipeline.

## Layout

```
stock_digest/
  watchlist.json            what to track. Edit this by hand.
  requirements.txt
  .env.example              copy to .env for local runs
  data/                     per-run output, gitignored
  scripts/
    common.py               shared helpers, not a pipeline stage
    01_smoke_test.py        checks the setup, costs nothing
    02_gather_market_data.py    free data gathering
    03_summarize_with_claude.py optional, off by default, the only paid part
    04_post_to_discord.py       builds and posts the message
```

The workflow file lives at the repository root, in
`.github/workflows/stock_digest.yml`, because that is the only place GitHub
Actions looks for it.

Each stage is a standalone script that hands off through JSON files in
`data/`. That means you can run stage 2 once and then iterate on stage 4 as
many times as you like without re-fetching anything. Stage 3 is skipped
entirely in the default setup, and stage 4 does not care whether it ran.

## Setup

### 1. Create a Discord webhook

A webhook is a URL that lets a script post into one specific channel. It
carries its own permission, so nothing needs a bot account or a login.

1. In Discord, right-click the channel you want the digest in and choose
   **Edit Channel**.
2. Go to **Integrations**, then **Webhooks**, then **New Webhook**.
3. Give it a name, for example `Market Digest`. The name and avatar here are
   defaults, the script overrides the name when it posts.
4. Click **Copy Webhook URL**. It looks like
   `https://discord.com/api/webhooks/123456789/AbCdEf...`.

Treat that URL like a password. Anyone who has it can post into that channel.
It never goes in a file that gets committed.

### 2. Add the secret to GitHub

GitHub Actions secrets are encrypted values that get handed to the workflow as
environment variables at run time. They are never printed in the logs and
cannot be read back out of the web interface once saved.

1. Open the repository on github.com.
2. Click **Settings**, the tab along the top of the repository, not your
   account settings.
3. In the left sidebar, open **Secrets and variables**, then **Actions**.
4. Click **New repository secret**.
5. Name: `DISCORD_WEBHOOK_URL`. Secret: paste the webhook URL from step 1.
   Click **Add secret**.

The name has to match exactly, in capitals, because the workflow file looks it
up by name. That is the only secret this project needs.

To confirm it works, go to the **Actions** tab, pick **Weekly Stock Digest**
in the left sidebar, and click **Run workflow**. That is the
`workflow_dispatch` trigger, and it runs the real thing immediately rather
than waiting for Sunday.

### 3. Test locally first

Running it on your own machine before trusting the schedule is worth the five
minutes.

```
cd stock_digest
pip install -r requirements.txt
cp .env.example .env
```

Then open `.env` and put the webhook URL in. `.env` is gitignored, so it will
not be committed. The scripts also read the workspace root `.env` one level
up, which is where the `DB_` variables for the other projects already live, so
the database settings do not need repeating here.

Note on Python versions: run everything through the workspace `venv`, which is
on 3.11. A bare `python` on this machine is 3.8, and while the free pipeline
works there, the optional Anthropic SDK would not install.

```
# Costs nothing. Checks imports, the watchlist, the secrets, and both
# free data sources against one test ticker.
../venv/Scripts/python.exe scripts/01_smoke_test.py

# Costs nothing. Takes about 45 seconds for 21 tickers because it pauses
# half a second between calls to be polite to the free sources.
# Writes data/digest_raw.json.
../venv/Scripts/python.exe scripts/02_gather_market_data.py

# Prints the exact Discord message as JSON and posts nothing. Run this
# before you ever post for real.
../venv/Scripts/python.exe scripts/04_post_to_discord.py --dry-run

# Posts for real.
../venv/Scripts/python.exe scripts/04_post_to_discord.py
```

The smoke test will warn that `ANTHROPIC_API_KEY` is not set. That is expected
and does not fail the test, since the paid step is switched off.

## What it costs

Nothing, as configured. `yfinance` and the Google News RSS feed are both free
and need no account, GitHub Actions is free for public repositories and has a
generous free allowance for private ones, and a Discord webhook costs nothing.
A weekly run takes a couple of minutes of Actions time.

## Optional: turning the Claude one-liners back on

`scripts/03_summarize_with_claude.py` is written, tested, and left in place,
but nothing runs it. It sends the whole watchlist to Claude in a **single**
batched call per week and gets back one plain-English sentence per ticker,
saying what changed and whether it affects the reason for watching the stock.

To switch it on:

1. Get an API key at https://console.anthropic.com, under **Settings**, then
   **API keys**, then **Create Key**. Add a little credit under **Billing**.
2. Add it as a second repository secret named `ANTHROPIC_API_KEY`, following
   the same steps as section 2 above.
3. Uncomment `anthropic>=1.0,<2.0` in `requirements.txt`.
4. In `.github/workflows/stock_digest.yml`, put `ANTHROPIC_API_KEY` back in the
   `env:` block and uncomment the `Summarize with Claude` step. Both are marked
   with comments saying exactly what to restore.

What it would cost, measured against the real 21 ticker watchlist:

| Piece | Size |
|---|---|
| System prompt | about 1,100 characters |
| Watchlist data in the prompt | about 12,200 characters |
| Total input | roughly 3,300 to 3,800 tokens |
| Output, 21 sentences plus thinking | roughly 1,500 to 2,500 tokens |

At Claude Sonnet 5 rates of $2.00 per million input tokens and $10.00 per
million output tokens, that is roughly **2 to 4 cents per run, about $1.50 to
$2.00 a year**. Stage 3 logs the real token counts and an estimated dollar
cost after every call, so the figure stays honest rather than theoretical.

The single batched call is the design decision that keeps it that cheap.
Calling once per ticker would repeat the system prompt 21 times for no gain in
quality. The model is set by `MODEL_ID` at the top of the script.

Nothing else changes if you turn it on. Stage 4 picks the one-liners up
automatically when the summaries file is there, and if the call ever fails the
digest still posts without them and says so in the header.

## Editing the watchlist

`watchlist.json` is the only file to touch for routine changes. The shape is:

```json
{
  "sectors": [
    {
      "name": "Biotech",
      "sector_events": [
        { "label": "Something affecting the whole sector", "date": "2026-12-05", "notes": "" }
      ],
      "tickers": [
        {
          "symbol": "KOD",
          "company": "Kodiak Sciences",
          "events": [
            { "label": "DAYBREAK trial topline data", "date": "2026-09-30", "notes": "estimate" }
          ]
        }
      ]
    }
  ]
}
```

**To add a ticker**, add an object to the `tickers` list of the right sector.
`symbol` and `company` are both required. `company` is used for the news
search as well as the display name, so the real company name works better than
a nickname. `events` can be an empty list.

**To add a sector**, add an object to `sectors` with a `name` and a `tickers`
list. It becomes its own Discord embed automatically. `sector_events` is
optional and applies to the whole sector rather than one ticker.

**To add or fix an event**, add an object to that ticker's `events` list with
a `label`, a `date` in `YYYY-MM-DD` form, and optionally a `notes` string.
Use `notes` to record that a date is an estimate, which is most of them.

Nothing edits this file automatically. When an event date passes, the digest
says so in the message and keeps saying so for about six weeks, after which
the wording changes to say it needs updating in `watchlist.json`. That is the
prompt to read the headlines, work out the new date, and edit it by hand.

After editing, check the file still parses before committing:

```
../venv/Scripts/python.exe scripts/01_smoke_test.py
```

## Schedule

The workflow runs on `cron: "0 1 * * 1"`, which is 01:00 UTC on Monday.

GitHub cron is always UTC and does not follow daylight saving, while Mountain
Time does, so a fixed UTC time drifts by an hour across the year:

- November to March, Mountain Standard Time (UTC-7): Sunday 6:00 pm
- March to November, Mountain Daylight Time (UTC-6): Sunday 7:00 pm

If 6:00 pm in summer matters more than 6:00 pm in winter, change the cron to
`"0 0 * * 1"` instead, which gives 6:00 pm in summer and 5:00 pm in winter.

Two caveats worth knowing about GitHub's scheduler. Scheduled runs are queued
on a best-effort basis and can be delayed by several minutes at busy times, so
do not expect it to the second. And GitHub disables scheduled workflows in
repositories with no activity for 60 days, so an occasional commit keeps it
alive.

## The database archive

Each run also stores one row per ticker in Postgres, following the same raw
JSONB pattern as the other projects in this workspace:

```
markets_raw.stock_digest_runs
  source      TEXT           always 'stock_digest'
  dataset_id  TEXT           the run date, for example '2026-09-24'
  ingested_at TIMESTAMPTZ
  payload     JSONB          one ticker's full record, plus its sector
```

Note that `markets_raw` is a **new schema**. The workspace convention has
`sports_raw` and `health_raw`, and market data is neither, so this adds a third
rather than forcing it into one of the existing two. The schema and table are
created on first run with `CREATE SCHEMA IF NOT EXISTS` and
`CREATE TABLE IF NOT EXISTS`.

The write is idempotent. Rerunning the same date deletes that date's rows
before inserting, so a repeated run replaces rather than duplicates.

This step **skips itself** whenever the `DB_` environment variables are absent,
which is always the case on the GitHub Actions runner since it has no route to
a local Postgres. So the weekly automated run posts to Discord and archives
nothing, while a local run does both. If the archive matters more than that,
the options are to host the database somewhere reachable or to run the gather
stage locally on a schedule.

A failed database write never stops the digest. It is logged and the run
continues.

## When something breaks

Every failure mode here is designed to degrade rather than stop.

| What went wrong | What happens |
|---|---|
| One ticker fails in yfinance | That ticker is logged, its price shows as unavailable, everything else posts |
| No headlines for a ticker | The field says so, the run continues |
| Claude step is off, the normal case | Nothing to report, the digest posts the free data with a clean header |
| Claude enabled and the API errors | The digest posts without one-liners and says so in the header |
| Postgres unreachable | Logged as a warning, the digest still posts |
| Discord rejects a message | Retried on a rate limit, otherwise logged, and the job fails so the Actions run shows red |

Every run uploads `data/*.json` as a GitHub Actions artifact, kept for 14
days. When a digest looks wrong, download that artifact from the run page to
see the exact data that produced it.

The two things that genuinely fail the whole run are a missing
`DISCORD_WEBHOOK_URL` and a Discord rejection, both of which mean there is
nowhere to post.

The most likely long-term breakage is `yfinance`. It scrapes Yahoo Finance
rather than using a supported API, so it breaks occasionally when Yahoo changes
something. The fix is almost always upgrading the package.
