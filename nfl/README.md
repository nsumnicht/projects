# NFL Injury Rate Analysis

## What This Is
Statistical investigation into whether playing surface 
(artificial turf vs natural grass) independently affects 
NFL injury rates, controlling for player position, weather, 
experience, and game context. Built as a portfolio-quality 
Jupyter notebook structured as a blog post.

## Status
- **Current phase:** Section 2 (rookie and experience analysis)
- **Last updated:** 2026-05-05
- **Blockers:** none

## Published
- Section 1 blog post: nicksumnicht.com/posts/nfl-turf-injury-analysis.html
- Narrative draft: nfl/narrative/section1_surface_type.md

## End Goal
Jupyter notebook published as a portfolio blog post with 
logistic regression model, coefficient plot, and section 
on rookie injury trends over time.

---

## Data Sources
| Source | What It Contains | Location in DB | Status |
|--------|-----------------|----------------|--------|
| nflreadpy load_injuries() | Weekly injury reports 2009-present | sports_raw.nfl_injuries | complete |
| nflreadpy load_schedules() | Game data, surface type, weather, roof | sports_raw.nfl_schedules | complete |
| nflreadpy load_rosters() | Player attributes, experience, position | sports_raw.nfl_rosters | complete |
| nflreadpy import_draft_picks() | Draft data for rookie identification | sports_raw.nfl_drafts | not started |

---

## Database Schema
**Raw tables (JSONB payload pattern):**
- `sports_raw.nfl_injuries` — weekly injury report rows
- `sports_raw.nfl_schedules` — game/schedule rows
- `sports_raw.nfl_rosters` — roster rows per season
- `sports_raw.nfl_drafts` — draft pick rows

**Clean tables:**
- `sports_clean.nfl_master` — joined master table, 
  one row per player per game with all attributes attached

---

## File Structure
nfl/
├── scripts/
│   ├── 01_nfl_schema_setup.py       creates all raw tables
│   ├── 02_ingest_nfl_injuries.py    weekly injury reports, schedules, and rosters
├── notebooks/
│   ├── 03_nfl_clean_and_join.ipynb  cleaning and joining
│   └── 04_nfl_injury_analysis.ipynb main analysis blog post
├── narrative/
│   └── section1_surface_type.md    Section 1 blog post draft
└── README.md

---

## Key Decisions and Methodology

### Why logistic regression not OLS
Injury outcome is binary (injured/not injured). OLS on 
a 0/1 outcome produces predicted probabilities outside 
0-1 range. Logistic regression is the correct model for 
binary outcomes.

### Why rate not raw count for surface comparison
Teams play different numbers of home/away games on each 
surface. Raw counts would be biased toward teams that 
happen to play more games on one surface type.

### Why use Out as injury proxy
IR designation is a roster transaction, not a game-week 
status — it does not appear in load_injuries() data. 
Out is the most consistent and clearly defined status 
across all seasons. Questionable players who play through 
pain are undercounted — documented limitation.

### Severity scoring
Duration-based log curve: 1.0 + 0.9 * log(games_missed + 1) / log(18).
Range: 1.22 for one game missed to 1.90 for a full season.
IR bonus was designed but cannot be applied — IR is not 
in the weekly report feed.

### Rookie identification
Uses both years_exp == 0 from rosters AND entry_year == 
season to catch undrafted free agents that years_exp might miss.

---

## Dependencies
```bash
pip install polars==0.20.31
pip install nflreadpy@git+https://github.com/nflverse/nflreadpy
pip install pandas==1.3.4
pip install pg8000
pip install statsmodels
```

**Known compatibility notes:**
- pandas pinned at 1.3.4 — do not upgrade
- nflreadpy returns Polars DataFrames — ALWAYS call 
  .to_pandas() immediately after any nflreadpy function
- Install polars BEFORE nflreadpy or install will fail

---

## Known Issues and Open Questions
- [x] nflreadpy Windows install — resolved
- [x] Confirm load_injuries() earliest year — confirmed 2009
- [x] Surface type standardization — complete
- [x] Concussion underreporting — documented as limitation
- [ ] IR transaction data not in weekly report feed — 
      would require load_transactions() ingestion to add severity designation bonus
- [ ] Timeline chart built in notebook but not yet exported to blog post assets
- [x] Notebook pushed to GitHub — notebook link added to blog post and project card
- [ ] SportTurf OR=1.082 p=0.048 borderline significant — worth monitoring in Section 2 controls

---

## How to Run
```bash
# 1. Schema setup (run once)
python 01_nfl_schema_setup.py

# 2. Ingestion
python 02_ingest_nfl_injuries.py

# 3. Clean and join
# Open 03_nfl_clean_and_join.ipynb in Jupyter

# 4. Analysis
# Open 04_nfl_injury_analysis.ipynb in Jupyter
```

---

## Results and Findings

### Section 1 — Surface Type (COMPLETE, published 2026-05-05)

**Overall injury rate:** artificial 17.84% vs. natural 17.82%. No meaningful difference
(chi-square p=0.92). The commonly held belief that artificial turf produces more injuries
does not hold at the aggregate level.

**Injury type mix:** no meaningful difference in lower body, concussion, or upper body
proportions between artificial and natural (p=0.97). Lower body injuries account for
~69% of injuries on both surfaces. Knee: p=0.52, no effect. Ankle: p=0.12, weak signal.

**Key finding — surface sub-types matter:**
Global chi-square across all surface sub-types: p=0.0089.

Logistic regression (outcome: any injury, reference: natural grass):
- AstroTurf: OR=1.175, p=0.004 — 17% higher overall injury odds, statistically significant
- SportTurf: OR=1.082, p=0.048 — borderline significant, treat with caution (n=4,521)
- FieldTurf: OR=0.970, p=0.159 — not meaningfully different from grass
- All other modern surfaces: not significant

**AstroTurf puzzle:** injury type breakdown on AstroTurf vs. grass is not meaningful
(p=0.85). The elevated overall rate is not concentrated in any injury category. Unresolved.

**Severity:** Mann-Whitney U p=0.41. No difference in games missed between surfaces.
Median injury on both surfaces is a single-game absence.

**Interpretation:** Modern FieldTurf is not more dangerous than natural grass. The public
narrative traces back to old-generation AstroTurf, which is more dangerous overall.
The narrative has not kept pace with the product evolution.

### Section 2 — Rookie and Experience Analysis (NOT STARTED)

### Section 3 — Weather and Team Injury Context (NOT STARTED)

### Final — Logistic Regression Risk Model (NOT STARTED)

---

## References
- nflverse/nflreadpy — https://github.com/nflverse/nflreadpy
- NFL injury underreporting study — 
  https://pmc.ncbi.nlm.nih.gov/articles/PMC10829213/
