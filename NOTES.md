# Project Notes
Last updated: 2026-04-15

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
- Dash app in progress: style.py ✅, data_loader.py ✅, map_utils.py ✅
- Next: app.py (layout) then callbacks.py — paused to work on portfolio site first
- Prompt: prompts/health_desert_transparency_dashboard_prompt.txt

### NFL Injury Rate Analysis
- Schema setup complete
- Next: write 02_ingest_nfl_injuries.py
- Use venv at project root (64-bit Python 3.11.2) — polars and nflreadpy now installed

### Portfolio Website (nicksumnicht.com)
- LIVE at https://nsumnicht.github.io
- Completed: nav, hero, about, projects, dashboards, blog preview, resume, contact, footer
- blog.html and posts/template.html built and pushed
- Resume PDF live (nick_sumnicht_resume.pdf)
- nicksumnicht.com domain connected ✅ — HTTPS enforced, live
- Next: update F1 DPI project card once notebook is polished, then link live demo
- Blocked on: headshot photo

## 📝 Prompts Ready (not started)
- Pantheon's Last Stand → prompts/pantheons_last_stand_opus_prompt.md
- Disney World Crowd Predictor → prompts/disney_crowd_opus_prompt.md
- Proposal Escape Room Box → prompts/proposal_box_opus_prompt.md

## 🧠 Agents Available
Run /[agent name] in Claude Code VS Code extension
See .claude/commands/ for full list

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
