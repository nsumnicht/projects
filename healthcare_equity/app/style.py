"""
style.py
========
Central theme configuration for the Colorado Healthcare Equity app.

All colors, fonts, and spacing live here. Other files import from this
module instead of hardcoding values — so changing the theme means
changing one file, not hunting through five.

DARK THEME RATIONALE
--------------------
Dark backgrounds reduce eye strain for map-heavy tools (the map is the
primary content). Light text on dark background also makes the purple
desert gradient and hospital dot colors pop more visually.
"""

# ── Background colors ────────────────────────────────────────────────────────
BG_PAGE      = "#1c242d"   # darkest — page background
BG_CARD      = "#333f48"   # slightly lighter — cards, panels
BG_SIDEBAR   = "#1f252b"   # sidebar background
BG_HEADER    = "#1f262b"   # top nav bar

# ── Text colors ──────────────────────────────────────────────────────────────
TEXT_PRIMARY   = "#f0f2f5"  # near-white — headings, primary labels
TEXT_SECONDARY = "#9aa0b4"  # muted grey — subtext, footnotes
TEXT_ACCENT    = "#85F8F2"  # light teal — links, highlights

# ── Desert tier colors (light to dark purple = worse access) ─────────────────
# Used on the choropleth map and tier badges
DESERT_COLORS = {
    "Adequate Access":  "#A1DCD9",   # soft teal
    "Underserved":      "#B56F53",   # light blue-orange
    "Moderate Desert":  "#B85D3D",   # medium orange
    "Severe Desert":    "#BB4A26",   # deep orange
}

# Continuous colorscale for choropleth (Plotly format: list of [0-1, color])
DESERT_COLORSCALE = [
    [0.00, "#A1DCD9"],
    [0.33, "#B56F53"],
    [0.66, "#B85D3D"],
    [1.00, "#BB4A26"],
]

# ── Transparency grade colors ────────────────────────────────────────────────
# Used on hospital dots on the map and grade badges
GRADE_COLORS = {
    "A":       "#14a9a2",   # green — fully compliant
    "B":       "#C4BE66",   # yellow — mostly compliant
    "C":       "#fec552",   # orange — partial compliance
    "D":       "#f9a871",   # red — poor compliance
    "F":       "#882e0b",   # dark red — non-compliant
    "Unknown": "#a3aaad",   # grey — no data
}

# ── Accent / UI colors ───────────────────────────────────────────────────────
COLOR_PRIMARY   = "#40e0d0"  # teal — buttons, active states
COLOR_DANGER    = "#882e0b"  # red — enforcement flags, warnings
COLOR_SUCCESS   = "#14a9a2"  # green — compliant badges
COLOR_BORDER    = "#727f8a"  # subtle border between panels

# ── Typography ───────────────────────────────────────────────────────────────
FONT_FAMILY = "'Inter', 'Segoe UI', sans-serif"
FONT_SIZE_BASE = "14px"
FONT_SIZE_SMALL = "12px"
FONT_SIZE_LARGE = "18px"

# ── Spacing ──────────────────────────────────────────────────────────────────
PADDING_CARD = "20px"
PADDING_SECTION = "16px"
BORDER_RADIUS = "8px"

# ── Plotly figure base template ──────────────────────────────────────────────
# Applied to every chart/map so they all match the dark theme.
# You pass this as: go.Figure(layout=PLOTLY_LAYOUT)
PLOTLY_LAYOUT = {
    "paper_bgcolor": BG_CARD,
    "plot_bgcolor":  BG_CARD,
    "font": {
        "color":  TEXT_PRIMARY,
        "family": FONT_FAMILY,
        "size":   12,
    },
    "margin": {"l": 0, "r": 0, "t": 40, "b": 0},
}

# ── Reusable inline style dicts ──────────────────────────────────────────────
# Use these in layout components: html.Div(style=CARD_STYLE)

CARD_STYLE = {
    "backgroundColor": BG_CARD,
    "borderRadius":    BORDER_RADIUS,
    "padding":         PADDING_CARD,
    "border":          f"1px solid {COLOR_BORDER}",
    "marginBottom":    "16px",
}

HEADER_STYLE = {
    "backgroundColor": BG_HEADER,
    "padding":         "12px 24px",
    "borderBottom":    f"1px solid {COLOR_BORDER}",
    "display":         "flex",
    "alignItems":      "center",
    "justifyContent":  "space-between",
}

SIDEBAR_STYLE = {
    "backgroundColor": BG_SIDEBAR,
    "width":           "280px",
    "minHeight":       "100vh",
    "padding":         "20px",
    "borderRight":     f"1px solid {COLOR_BORDER}",
    "flexShrink":      "0",
}

LABEL_STYLE = {
    "color":        TEXT_SECONDARY,
    "fontSize":     FONT_SIZE_SMALL,
    "fontWeight":   "600",
    "letterSpacing": "0.5px",
    "textTransform": "uppercase",
    "marginBottom": "6px",
}
