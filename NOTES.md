# Project Notes
Last updated: 2026-04-27

---

## ✅ Complete
- F1 Driver Performance Index (DPI) — all 16 indicators wrapped into functions,
  debug prints removed, global name_lookup refs eliminated, avg_finish_pct bug
  fixed in peak_performance. Notebook ready for portfolio.

## 🔨 In Progress

### Colorado Healthcare Equity Tool (Desert Index + Price Transparency)
- Phases 1 & 2 complete. All 4 Desert Index components ready in DB:
  - hdi_rows: uninsured_rate + poverty_rate (1,447 CO tracts)
  - hdi_rows: physician supply (64 CO counties, AHRF)
  - hdi_rows: tract_to_nearest_ed_2020 (1,447 CO tracts, Haversine)
  - health_clean.cdc_places_clean: 229K rows (validation layer)
  - health_raw.hospital_registry: 97 CO hospitals (geocoded)
- All packages installed in project root venv (64-bit Python 3.11.2)
- Note: uses health_raw/health_clean schemas (not colorado_raw/colorado_clean as prompt specifies)
- 02_compute_desert.ipynb complete — 1,447 tracts scored, tiered, validated, written to health_clean.desert_index
- Dash app: style.py ✅, data_loader.py ✅, map_utils.py ✅, app.py ✅, callbacks.py ✅, assets/custom.css ✅
- Run: cd healthcare_equity/app && python app.py → http://127.0.0.1:8050
- Read DASHBOARD_WALKTHROUGH.md before touching code — covers every function and why
- Bugs fixed: TIGER API layer was 8 (Block Groups) → now 6 (Census Tracts), STATE field requires quoted string, colorbar titlefont → title=dict(text,font)
- Phase 4 complete (2026-04-21): HCPF compliance grades and CMS enforcement counts ingested
  - health_raw.transparency_rows: 101 HCPF hospital scorecard rows (source=hcpf, dataset=hcpf_aug_2025)
  - health_raw.transparency_rows: 204 CMS enforcement action rows (source=cms_enforcement, dataset=cms_enforcement_q3_q4_2025)
  - health_raw.hospital_registry: 85/97 hospitals have pra_compliance_grade, 67/97 have cms_enforcement_count > 0
  - 12 unmatched are specialty facilities (rehab, LTACH, behavioral) not in CMS general hospital registry: expected
  - pra_compliance_grade stores HCPF Good/Fair/Poor overall rating (not the PRA A-F grade assumed in original prompt)
  - HCPF PDF had Good/Fair/Poor ratings, not price file URLs. Prompt assumption was wrong; data model adapted.
  - Scripts: 02_ingest_hcpf_compliance.py, 02_ingest_cms_enforcement.py, 03_update_hospital_registry.py
- Next: run Dash app and confirm compliance data surfaces in hospital detail panel
- Prompt: prompts/health_desert_transparency_dashboard_prompt.txt

### ED Utilization Pipeline (Tableau Data Mart)
- Prompt: prompts/ED_Utilizatoin_Prompt.txt
- Schemas: health_raw (raw JSONB) / health_clean (cleaned views)
- Phase 1 — Ingestion: ALL COMPLETE
  - health_raw.cdc_rows: CDC PLACES swc5-untb (229K rows) + cwsq-ngmh (3M rows)
  - health_raw.census_rows: ACS5 2022 tract SDOH + population (85K rows each)
  - health_raw.cms_rows: hospital general info xubh-q36u (5,426) + timely care 4pq5-n9py (14,710) + yv7e-xc69 (138,129)
  - health_raw.epa_rows: EPA walkability index tract 2019 (74K rows)
  - Note: dataset 4pq5-n9py is Nursing Home Compare data, NOT ED data. ED measures are in yv7e-xc69.
- Phase 2 — Cleaning: COMPLETE (2026-04-16)
  - Script: scripts/04_clean_and_load.py
  - health_clean.ed_hospitals: 5,426 rows (all US hospitals, county FIPS resolved)
  - health_clean.ed_measures: 4,009 rows (hospitals with ED measures, pivoted wide)
  - health_clean.census_sdoh_tract: 85,396 rows (derived rates from ACS variables)
  - health_clean.epa_walkability: 74,001 rows (walkability score per tract)
  - health_clean.cdc_places_county: 3,143 rows (17 health measures pivoted wide)
  - health_clean.ed_dashboard: 4,009 rows (final flat Tableau table, all context joined)
  - Walkthrough: ed_utilization/CLEANING_WALKTHROUGH.md
- Phase 3 — SQL views: NOT STARTED (05_create_views.py is 0 bytes, placeholder only)
- Phase 4 — Analysis notebook: NOT STARTED
- Run env: use project root venv (64-bit Python 3.11.2)

### Disney World Crowd Predictor
- Prompt: prompts/Disney_Prompt.txt
- Schemas: disney_raw / disney_clean (both exist in DB)
- Phase 1 — Schema setup: COMPLETE (2026-04-16)
  - Script: disney/scripts/01_schema_setup.py
  - 8 raw tables created: wait_times, crowd_calendars, lightning_lane_prices,
    ticket_prices, hotel_rates, school_calendars, special_events, weather
- Phase 2 — Ingestion: NOT STARTED (pending data source decisions below)
- Data source research complete (2026-04-16). Verdicts:

  GREEN (safe to build immediately):
  - queue-times.com — free documented public API, no auth, live wait times only
    (must poll and accumulate history ourselves going forward)
  - open-meteo.com — free, no API key, Orlando historical weather back to 1940
  - Python holidays library — US federal holidays, local computation

  YELLOW (need action before building):
  - thrill-data.com — best source for Lightning Lane pricing history and deep wait
    time history. No public API. ToS requires emailing thrilldataofficial@gmail.com
    before building a database from their data. Email them first.
  - allears.net — organized Lightning Lane price tables and historical event archive.
    robots.txt allows general crawlers, no ToS prohibition found. Recommended:
    one-time targeted scrape, not continuous polling.
  - wdwstats.com — hobbyist site, no robots.txt, no ToS. Lighter LL pricing data
    than Thrill Data. Use respectfully if Thrill Data email gets no response.
  - School break dates — no free compiled dataset exists. Manual collection from
    FL, NY, TX, CA, IL state DOE websites. One-time annual effort per school year.
  - wdwmagic.com — event dates embedded in news articles (messy). AllEars is cleaner.

  RED (do not use):
  - touringplans.com — ToS explicitly prohibits all automated access. Hard stop.
  - easywdw.com — site is defunct (operator deceased 2022).
  - undercovertourist.com — Cloudflare blocks all automation.
  - Expedia / Booking.com / Hotels.com — no free historical rate API exists.

  BLOCKER: Self-defeat hypothesis requires historical crowd calendar predictions
  vs. actual crowd levels. TouringPlans is the only source with this data and it
  is off-limits. May need to reframe hypothesis or contact TouringPlans about
  a licensing arrangement.

- Next: email thrilldataofficial@gmail.com, then build queue-times + weather
  ingestion scripts (green sources first)
- README: disney/README.md does not exist yet

### NFL Injury Rate Analysis
- Schema setup complete
- nflreadpy, polars, pyarrow all installed and working
- Ingestion complete (2026-04-22): 02_ingest_nfl_injuries.py pulls injuries, schedules, and rosters
  - sports_raw.nfl_injuries: weekly injury reports 2009-2024
  - sports_raw.nfl_schedules: game schedules 2009-2024 (includes surface type for turf analysis)
  - sports_raw.nfl_rosters: weekly rosters 2009-2024 (position, years_exp, age, weight)
- Cleaning notebook complete (2026-04-23): nfl/notebooks/03_nfl_clean_and_join.ipynb
  - sports_clean.nfl_master: 84,667 rows, one row per player per game
  - Injury proxy: Out status only (no IR designations in weekly report data — IR is a roster transaction)
  - Surface standardization: all variants mapped to artificial/natural, sportturf fixed
  - Rookie flag: years_exp == 0 OR entry_year == season
- Analysis notebook: nfl/notebooks/04_nfl_injury_analysis.ipynb
  - statsmodels installed in venv
- Section 1 complete and published (2026-05-05)
  - Blog post live: nicksumnicht.com/posts/nfl-turf-injury-analysis.html
  - Narrative draft: nfl/narrative/section1_surface_type.md
  - Timeline chart built in notebook but not yet exported or included in post

### NFL Section 1 Findings: Surface Type vs. Injury (COMPLETE)

Overall rates: artificial 17.84% vs natural 17.82%, chi-square p=0.92, no meaningful difference.
Injury type mix: no meaningful difference (p=0.97, Cramer's V=0.004). Lower body 69.4% on both.
Ankle: weak signal, artificial slightly higher (p=0.12), not significant.
Knee: no surface effect (p=0.52).
Surface sub-type global chi-square: p=0.0089, signal exists somewhere.
Overall injury logistic regression by surface (vs. grass reference):
  - AstroTurf: OR=1.175, p=0.004 (significant, 17% higher overall injury odds)
  - SportTurf: OR=1.082, p=0.048 (borderline significant, treat with caution, n=4,521)
  - FieldTurf: OR=0.97, p=0.159 (not different from grass)
  - All other modern surfaces: not significant
AstroTurf injury type breakdown vs grass: p=0.85, no category-level difference found.
  Lower body actually slightly lower on AstroTurf (68.3% vs 69.5%). Puzzle unresolved.
Severity: Mann-Whitney U p=0.41, no difference in games missed between artificial and natural.
  Median injury on both surfaces is a single game.
Key limitation: IR not in weekly report data (roster transaction, separate feed).
  Severity score is duration-only (log curve, 1.22 for 1 game to 1.90 for full season).

### NFL Next Steps (pick up here)
1. Section 2: rookie and experience analysis (Nick's hypothesis: rookies injured more)
2. Section 3: weather, team injury context (injured teams breed more injuries)
3. Final: logistic regression risk model combining all significant variables
4. Export timeline chart from notebook and add to blog post
5. Add notebook GitHub link to blog post once notebook is pushed to GitHub

- Use venv at project root (64-bit Python 3.11.2)

### Portfolio Website (nicksumnicht.com)
- LIVE at https://nsumnicht.github.io
- Completed: nav, hero, about, projects, dashboards, blog preview, resume, contact, footer
- blog.html and posts/template.html built and pushed
- Resume PDF live (nick_sumnicht_resume.pdf)
- nicksumnicht.com domain connected ✅ — HTTPS enforced, live
- F1 DPI project card updated: GitHub notebook link added to both card and blog post ✅
- Blocked on: headshot photo (nothing else to do until photo is provided)

## 📝 Prompts Ready (not started)
- Pantheon's Last Stand → prompts/pantheons_last_stand_opus_prompt.md
- Proposal Escape Room Box → prompts/proposal_box_opus_prompt.md

## 🧠 Agents Available
Run /[agent name] in Claude Code VS Code extension
See .claude/commands/ for full list

## ⚠️ README Gaps
- f1/README.md exists but is empty (1 line) — needs content

## 🗂️ Blocked / On Hold
- CDI (Car Dominance Index) — F1 DPI extension, design in progress
- F1 Spider Chart — tabled, design in progress
- Hospital Price Transparency — combined with Desert Index prompt

## 🖼️ Portfolio Site TODOs
- [ ] Headshot photo — provide to Claude to add to assets/images/
- [x] Connect nicksumnicht.com via Namecheap DNS ✅
- [ ] Background grey colors may be too light — revisit (see TODO in style.css variables)
- [ ] Add blog post once F1 DPI writeup is ready
- [ ] Confirm LinkedIn slug is correct (currently linkedin.com/in/nicholassumnicht)

## 📌 Open Questions / Decisions Needed
- Colorado HCPF report contents — verify has hospital file URLs
  before running healthcare equity prompt

## 💡 Ideas / Future Enhancements
- SDOH Composite Index: build a single SDOH vulnerability score from Census ACS variables
  (poverty, uninsured, unemployment, no vehicle, limited English, low education, low income)
  and feed it into the Desert Index as a fifth component or as a standalone layer in the
  Health Equity dashboard. Goal: show which populations are most vulnerable, not just which
  tracts lack geographic access. SDOH index + desert index together would capture both the
  supply-side problem (no doctors nearby) and the demand-side problem (people who cannot
  navigate the system even when care exists).
- SDOH variables (Census ACS, EPA walkability) should also appear as context layers in the
  Health Equity Dash app detail panel once Phase 4 data is ingested. No methodology change
  needed, just surface the data that is already in the database.
- ED utilization and Health Equity are separate projects but tell a connected story:
  desert communities use the ED as primary care, which shows up as high LWBS rates and
  long wait times. Cross-reference the two in portfolio writeups when both are complete.
