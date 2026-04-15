import plotly.graph_objects as go
import pandas as pd
import json 
import requests
import style


def fetch_co_tract_geojson() -> dict:
    """
    Downloads Colorado census tract boundaries from the Census TIGER API.
    Plotly's choropleth needs these shapes to know where to draw each tract.
    Returns a GeoJSON dict — the standard format for geographic shapes.
    """
    url = (
        "https://tigerweb.geo.census.gov/arcgis/rest/services/"
        "TIGERweb/tigerWMS_Census2020/MapServer/8/query"
        "?where=STATE%3D08"
        "&outFields=GEOID,NAME"
        "&returnGeometry=true"
        "&f=geojson"
        "&outSR=4326"
    )
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.json()


def build_desert_map(df_desert: pd.DataFrame, geojson: dict) -> go.Figure:
    """
    Builds the choropleth map — census tracts colored by desert score.
    df_desert: the desert index DataFrame from data_loader
    geojson: the tract boundary shapes from fetch_co_tract_geojson()
    """
    fig = go.Figure()

    fig.add_trace(go.Choroplethmapbox(
        # The geographic shapes to fill
        geojson=geojson,

        # How to match DataFrame rows to GeoJSON shapes
        # The GeoJSON has a "GEOID" property on each feature
        # Our DataFrame has a "tract_geoid" column
        featureidkey="properties.GEOID",
        locations=df_desert["tract_geoid"],

        # The values that determine the color of each tract
        z=df_desert["desert_score"],

        # Color scale from style.py
        colorscale=style.DESERT_COLORSCALE,
        zmin=0,
        zmax=1,

        # Tooltip text when user hovers over a tract
        text=df_desert.apply(
            lambda r: (
                f"Tract: {r['tract_geoid']}<br>"
                f"Tier: {r['desert_tier']}<br>"
                f"Desert Score: {r['desert_score']:.3f}<br>"
                f"Drive Time: {r['drive_time_minutes']:.1f} min<br>"
                f"Uninsured: {r['uninsured_rate']*100:.1f}%<br>"
                f"Poverty: {r['poverty_rate']*100:.1f}%"
            ),
            axis=1
        ),
        hoverinfo="text",

        # Color bar (legend) styling
        colorbar=dict(
            title="Desert Score",
            titlefont=dict(color=style.TEXT_PRIMARY),
            tickfont=dict(color=style.TEXT_PRIMARY),
            bgcolor=style.BG_CARD,
            bordercolor=style.COLOR_BORDER,
            thickness=15,
        ),

        marker_opacity=0.7,
        marker_line_width=0.3,
        marker_line_color="#ffffff",
    ))

    fig.update_layout(
        **style.PLOTLY_LAYOUT,
        mapbox=dict(
            style="open-street-map",
            center=dict(lat=39.0, lon=-105.5),  # center of Colorado
            zoom=6,
        ),
        height=650,
        title="Colorado Healthcare Desert Index by Census Tract",
        title_font=dict(color=style.TEXT_PRIMARY, size=16),
    )

    return fig


def add_hospital_layer(fig: go.Figure, df_hospitals: pd.DataFrame) -> go.Figure:
    """
    Adds hospital dots on top of the choropleth map.
    Takes an existing figure and adds a Scattermapbox trace to it.
    Color = PRA compliance grade. Size = fixed (bed count data is sparse).
    """
    # Map each hospital's grade to a color from style.py
    dot_colors = df_hospitals["pra_compliance_grade"].map(
        lambda g: style.GRADE_COLORS.get(g, style.GRADE_COLORS["Unknown"])
    )

    fig.add_trace(go.Scattermapbox(
        lat=df_hospitals["latitude"],
        lon=df_hospitals["longitude"],

        mode="markers",
        marker=dict(
            size=10,
            color=dot_colors,
            opacity=0.9,
        ),

        # Tooltip for each hospital dot
        text=df_hospitals.apply(
            lambda r: (
                f"<b>{r['hospital_name']}</b><br>"
                f"{r['city']}, CO<br>"
                f"Type: {r['hospital_type']}<br>"
                f"Transparency Grade: {r['pra_compliance_grade'] or 'Unknown'}<br>"
                f"Enforcement Actions: {r['cms_enforcement_count'] or 0}"
            ),
            axis=1
        ),
        hoverinfo="text",
        name="Hospitals",
    ))

    return fig


def build_full_map(df_desert: pd.DataFrame, df_hospitals: pd.DataFrame, geojson: dict) -> go.Figure:
    """
    Builds the complete map: choropleth base + hospital dots.
    This is what app.py will call.
    """
    fig = build_desert_map(df_desert, geojson)
    fig = add_hospital_layer(fig, df_hospitals)
    return fig