"""Shamba Pulse - farm decisions from the JKUAT Conduit weather station.

Streamlit dashboard. Written farmer-first: the answer comes before the data,
plain language before jargon, and anything we are unsure about says so.

    streamlit run app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
from analysis import backtest as bt  # noqa: E402
from analysis import validate_rain as vr  # noqa: E402
from services import alerts as alerts_mod  # noqa: E402
from services import data_processor as dp  # noqa: E402
from services import data_source as ds  # noqa: E402
from services import disease_engine as de  # noqa: E402
from services import forecast as fc  # noqa: E402
from services import messages as msg  # noqa: E402
from services import sms_sender  # noqa: E402
from services import spray_window as sw  # noqa: E402

st.set_page_config(
    page_title="Shamba Pulse",
    page_icon="🌱",
    layout="centered",          # mobile-friendly: farmers are on phones
    initial_sidebar_state="collapsed",
)

# Mobile-first styling. Big touch targets, high contrast, no dense tables.
# Scoped CSS. Everything here is presentation only - colours for risk levels
# still come from config.RISK_COLORS so the dashboard, the charts and the
# backtest can never disagree about what MODERATE looks like.
st.markdown("""
<style>
  :root {
    --ink:      #1f2420;
    --ink-soft: #55605a;
    --line:     #e2ddd2;
    --panel:    #ffffff;
    --green:    #2f6b3f;
  }
  /* Streamlit's toolbar floats over the page, so the hero needs room
     to clear it - at 1.4rem the app name was being clipped. */
  .block-container {padding-top: 3.2rem; padding-bottom: 4rem;
                    max-width: 48rem;}

  /* --- hero ------------------------------------------------------------ */
  .hero-name {font-size: 2.0rem; font-weight: 800; letter-spacing: -0.8px;
              margin: 0 0 0.15rem 0; color: var(--ink); line-height: 1.1;}
  .hero-sub  {color: var(--ink-soft); font-size: 1.0rem; margin: 0 0 1.1rem 0;
              line-height: 1.45;}

  .risk-card {border-radius: 16px; padding: 1.4rem 1.5rem; margin-bottom: 0.9rem;}
  .risk-kicker {font-size: 0.78rem; letter-spacing: 1.4px; font-weight: 700;
                text-transform: uppercase; opacity: 0.92;}
  .risk-level {font-size: 3.0rem; font-weight: 800; line-height: 1.05;
               letter-spacing: -1.5px; margin: 0.15rem 0 0 0;
               display: flex; align-items: center; gap: 0.6rem;}
  .risk-icon  {font-size: 2.3rem; line-height: 1;}
  .risk-action {font-size: 1.12rem; font-weight: 600; margin-top: 0.55rem;
                line-height: 1.4;}

  .why {background: var(--panel); border: 1px solid var(--line);
        border-left: 4px solid var(--green); border-radius: 10px;
        padding: 0.9rem 1.05rem; margin-bottom: 0.5rem;}
  .why-h {font-weight: 700; font-size: 0.8rem; letter-spacing: 1px;
          text-transform: uppercase; color: var(--ink-soft);
          margin-bottom: 0.45rem;}
  .why li {margin-bottom: 0.35rem; line-height: 1.5;}
  .why ul {margin: 0; padding-left: 1.1rem;}

  /* --- freshness strip -------------------------------------------------- */
  .fresh {display: flex; align-items: flex-start; gap: 0.55rem;
          border-radius: 10px; padding: 0.6rem 0.85rem; font-size: 0.9rem;
          line-height: 1.45; margin-bottom: 0.5rem; border: 1px solid;}
  .fresh-live   {background:#eef6ef; border-color:#bcd9c2; color:#1e4d2b;}
  .fresh-stale  {background:#fdf6e3; border-color:#e8d08a; color:#6b5200;}
  .fresh-cached {background:#eef2f8; border-color:#c3d0e4; color:#24405e;}
  .fresh-demo   {background:#fdecea; border-color:#f0b3ae; color:#8a1c14;}

  /* --- metric cards ----------------------------------------------------- */
  .mcard {background: var(--panel); border: 1px solid var(--line);
          border-radius: 12px; padding: 0.8rem 0.9rem; height: 100%;}
  .mlabel {font-size: 0.74rem; letter-spacing: 0.6px; text-transform: uppercase;
           color: var(--ink-soft); font-weight: 700; margin-bottom: 0.25rem;}
  .mvalue {font-size: 1.65rem; font-weight: 800; color: var(--ink);
           line-height: 1.1; letter-spacing: -0.5px;}
  .munit  {font-size: 0.88rem; font-weight: 600; color: var(--ink-soft);
           margin-left: 0.15rem;}
  .mnote  {font-size: 0.78rem; color: var(--ink-soft); margin-top: 0.2rem;
           line-height: 1.35;}

  /* --- spray window cards ---------------------------------------------- */
  .win {background: var(--panel); border: 1px solid var(--line);
        border-left: 5px solid #2e7d32; border-radius: 12px;
        padding: 0.85rem 1rem; margin-bottom: 0.6rem;}
  .win-fair {border-left-color: #f9a825;}
  .win-top {display:flex; align-items:center; justify-content:space-between;
            gap:0.6rem; flex-wrap:wrap;}
  .win-when {font-weight: 750; font-size: 1.08rem; color: var(--ink);}
  .win-meta {color: var(--ink-soft); font-size: 0.87rem; margin-top: 0.3rem;
             line-height: 1.5;}
  .pill {display:inline-block; padding:0.12rem 0.6rem; border-radius:999px;
         font-size:0.72rem; font-weight:800; letter-spacing:0.5px;}
  .pill-good {background:#e6f2e8; color:#1e4d2b; border:1px solid #bcd9c2;}
  .pill-fair {background:#fdf3d9; color:#6b5200; border:1px solid #e8d08a;}

  /* --- SMS bubbles ------------------------------------------------------ */
  .phone {background:#eceff1; border:1px solid var(--line); border-radius:16px;
          padding:0.85rem 0.8rem 0.7rem 0.8rem; min-height: 11rem;}
  .bubble {background:#ffffff; border-radius:14px 14px 14px 4px;
           padding:0.75rem 0.9rem; font-size:0.94rem; line-height:1.5;
           color:var(--ink); box-shadow:0 1px 2px rgba(0,0,0,0.09);}
  .bubble-meta {font-size:0.75rem; color:var(--ink-soft); margin-top:0.45rem;
                padding-left:0.2rem;}
  .lang-h {font-weight:750; font-size:0.95rem; margin-bottom:0.4rem;
           display:flex; align-items:center; gap:0.4rem; flex-wrap:wrap;}

  .badge {display:inline-block; padding:0.1rem 0.5rem; border-radius:999px;
          font-size:0.68rem; font-weight:800; letter-spacing:0.3px;}
  .badge-warn {background:#fdf3d9; color:#6b5200; border:1px solid #e8d08a;}
  .badge-mute {background:#eef2f8; color:#24405e; border:1px solid #c3d0e4;}

  /* --- misc ------------------------------------------------------------- */
  .sect {font-size:1.32rem; font-weight:800; letter-spacing:-0.4px;
         color:var(--ink); margin:0.3rem 0 0.15rem 0;}
  .sect-sub {color:var(--ink-soft); font-size:0.92rem; margin-bottom:0.7rem;
             line-height:1.45;}
  .ruled {color:var(--ink-soft); font-size:0.88rem; line-height:1.5;
          background:#f4f1ea; border-radius:9px; padding:0.6rem 0.8rem;
          border:1px solid var(--line);}

  @media (max-width: 640px) {
    .block-container {padding-left: 0.85rem; padding-right: 0.85rem;
                      padding-top: 3.4rem;}
    .hero-name  {font-size: 1.7rem;}
    .risk-level {font-size: 2.4rem;}
    .risk-icon  {font-size: 1.9rem;}
    .mvalue     {font-size: 1.4rem;}
    .phone      {min-height: auto;}
  }
</style>
""", unsafe_allow_html=True)


# Text colour paired with each risk background so the label is always legible.
# Checked against WCAG AA: white on the amber MODERATE is only 1.97:1, so that
# one takes dark text (8.00:1) instead. Colour is never the sole signal - every
# risk level also carries an icon and the word itself.
RISK_TEXT = {
    "LOW": "#ffffff", "MODERATE": "#1f2420",
    "HIGH": "#ffffff", de.UNKNOWN: "#ffffff",
}

RISK_ICON = {
    "LOW": "✓", "MODERATE": "⚠", "HIGH": "⚑", de.UNKNOWN: "?",
}


# --------------------------------------------------------------------------
# Data loading (cached so a rerun does not re-hit the station)
# --------------------------------------------------------------------------

@st.cache_data(ttl=600, show_spinner="Reading the weather station...")
def get_observations(days: int) -> ds.DataStatus:
    return ds.load_observations(days=days)


@st.cache_data(ttl=900, show_spinner="Fetching the 7-day forecast...")
def get_forecast() -> fc.ForecastResult:
    return fc.fetch_forecast()


@st.cache_data(ttl=3600, show_spinner="Loading the ERA5 cross-check...")
def get_rain_validation() -> vr.ValidationData:
    """Cached station-vs-ERA5 comparison.

    Reads the committed CSV; only rebuilds (and only then touches the network)
    if that file is missing.
    """
    return vr.load_comparison()


@st.cache_data(ttl=3600, show_spinner="Replaying the history...")
def get_backtest(alert_hour: int, scope: str) -> pd.DataFrame:
    """Replay history. `scope` is "season" (the canonical OND 2025 window the
    README quotes) or "all" (the full station archive)."""
    hist, _ = bt.load_history()
    if hist.empty:
        return pd.DataFrame()
    if scope == "season":
        return bt.run_backtest(
            hist, alert_hour=alert_hour, with_spray=False,
            start=pd.Timestamp(config.BACKTEST_WINDOW["start"]),
            end=pd.Timestamp(config.BACKTEST_WINDOW["end"]),
        )
    return bt.run_backtest(hist, alert_hour=alert_hour, with_spray=False)


# --------------------------------------------------------------------------
# Banner
# --------------------------------------------------------------------------

def render_freshness(status: ds.DataStatus, forecast: fc.ForecastResult) -> None:
    """One compact strip saying where every number on the page came from.

    Same information as before, same four states - live / stale / cached /
    demo - just styled consistently instead of as three stacked alert boxes.
    """
    if status.source == ds.DEMO:
        cls, icon, text = ("fresh-demo", "&#9888;",
            "<b>DEMO DATA &mdash; not real station readings.</b> "
            "No live or cached Conduit data was available, so everything below "
            "the forecast is generated to demonstrate how the app behaves.")
    elif status.source == ds.CACHED:
        cls, icon, text = ("fresh-cached", "&#128451;",
            f"<b>Station unreachable &mdash; showing cached history.</b> "
            f"Real Conduit data, {status.age_text()}. {status.detail}")
    elif status.is_stale:
        cls, icon, text = ("fresh-stale", "&#128337;",
            f"<b>Station connected, newest reading is {status.age_text()}</b> "
            f"({status.latest:%a %d %b %H:%M}). The station publishes on a lag, "
            f"so risk below is based on data up to that time, not this minute.")
    else:
        cls, icon, text = ("fresh-live", "&#128225;",
            f"<b>Live station data.</b> {status.detail}")

    st.markdown(f"<div class='fresh {cls}'><div>{icon}</div>"
                f"<div>{text}</div></div>", unsafe_allow_html=True)

    if forecast.ok:
        st.markdown(
            "<div class='fresh fresh-cached'><div>&#127782;</div><div>"
            f"<b>7-day forecast is live</b> from Open-Meteo for {config.SITE_NAME} "
            "&mdash; real data, independent of the station.</div></div>",
            unsafe_allow_html=True)
    else:
        st.markdown(
            f"<div class='fresh fresh-stale'><div>&#127782;</div><div>"
            f"Forecast unavailable: {forecast.error}</div></div>",
            unsafe_allow_html=True)


def render_data_quality_body() -> None:
    """Compact summary of what we found wrong with the station data.

    Kept short here; docs/DATA_QUALITY.md carries the full evidence. Judges and
    farmers both benefit from seeing that the numbers were checked rather than
    trusted.
    """
    st.markdown(
        "**Data quality — what we checked.** We did not trust the obvious "
        "field names. Three of these would have produced confidently wrong "
        "advice."
    )
    st.markdown(
            """
| What we found | Why it mattered | What we did |
|---|---|---|
| **Rain gauge 1's per-interval field (`rg1`) captures only 6% of rainfall** — 18.6 mm recorded against 301.8 mm actual over OND 2025 | The spray adviser would have thought it never rains, and recommended spraying into a storm | Derive rainfall by differencing the running daily total `rg1tt` |
| **Rain gauge 2 is faulty** — its daily total resets ~21 times a day instead of once, reconstructing to 4,365 mm in a ~300 mm season | It had been used to fill gaps in gauge 1 | Never used, and never a fallback |
| **Timestamps are UTC, not local** (Kenya is UTC+3) | A 3-hour shift would silently corrupt every overnight humid-hours count and move the daily boundary | Converted to Africa/Nairobi on ingest |
| **`wind_gust_dir` duplicates `wind_gust`** (identical on 100% of rows, range −999.9 to 13.9) | Not a direction at all | Not used |
| **`si1145_uv` reads a constant zero** | A dead channel | Not used; light channels are labelled raw counts, never W/m² |
| **No soil-moisture or leaf-wetness sensor exists** | — | Never fabricated; humidity ≥ 90% is the stated leaf-wetness proxy |
            """
    )
    st.markdown(
        f"**Independent check:** our reconstructed rainfall totals "
        f"**301.8 mm** for OND 2025 against **268.6 mm** from ERA5 "
        f"reanalysis at the same coordinates — 12% apart. Daily correlation "
        f"is modest (Spearman 0.41), which is expected rather than "
        f"concerning: ERA5 is a ~9 km grid cell and the station is one "
        f"point inside it, and tropical rain here is convective. "
        f"This is a credibility check, not calibration — no station value "
        f"is adjusted toward ERA5."
    )
    st.caption(
        "Station coverage for OND 2025: 98.6–99.1% per month, 1.1% of "
        "intervals missing, no gap longer than 6 hours. "
        "Full evidence in docs/DATA_QUALITY.md; reproduce with "
        "analysis/explore_data.py and analysis/validate_rain.py."
    )


# --------------------------------------------------------------------------
# Risk card
# --------------------------------------------------------------------------

# The action sentence a farmer reads first. Deliberately imperative and
# specific; the "why" underneath is the supporting detail, not the headline.
RISK_ACTION = {
    "HIGH": "Spray your tomatoes and potatoes as soon as you safely can.",
    "MODERATE": "Check your tomatoes and potatoes closely.",
    "LOW": "No blight action needed today.",
    de.UNKNOWN: "Not enough station data to advise right now.",
}

RISK_KICKER = "Tomato / potato late blight"


def _metric(label: str, value: str, unit: str = "", note: str = "") -> str:
    unit_html = f"<span class='munit'>{unit}</span>" if unit else ""
    note_html = f"<div class='mnote'>{note}</div>" if note else ""
    return (f"<div class='mcard'><div class='mlabel'>{label}</div>"
            f"<div class='mvalue'>{value}{unit_html}</div>{note_html}</div>")


def render_hero(risk: de.RiskAssessment, advice: sw.SprayAdvice,
                is_demo: bool) -> None:
    """Name, subtitle, the risk card, the action sentence and the top reasons.

    The three-second read: icon + word + colour, then one sentence saying what
    to do. Colour is never the only signal.
    """
    st.markdown(
        "<div class='hero-name'>&#127793; Shamba Pulse</div>"
        "<div class='hero-sub'>Blight risk &amp; spray windows for Juja/Kiambu "
        "farmers, from the JKUAT Conduit station.</div>",
        unsafe_allow_html=True)


def render_risk_card(risk: de.RiskAssessment, advice: sw.SprayAdvice,
                     is_demo: bool) -> None:
    bg = config.RISK_COLORS.get(risk.level, config.RISK_COLORS[de.UNKNOWN])
    fg = RISK_TEXT.get(risk.level, "#ffffff")
    icon = RISK_ICON.get(risk.level, "?")
    action = RISK_ACTION.get(risk.level, RISK_ACTION[de.UNKNOWN])

    window = advice.best if advice else None
    if window and risk.level in ("HIGH", "MODERATE"):
        action += f" Best time to spray: {window.label}."
    elif window and risk.level == "LOW":
        action += f" Good spraying conditions {window.label} if you want them."

    demo = ("<span class='badge badge-warn' style='margin-left:.5rem'>"
            "DEMO DATA</span>") if is_demo else ""

    st.markdown(
        f"""<div class="risk-card" style="background:{bg};color:{fg}">
              <div class="risk-kicker">{RISK_KICKER}{demo}</div>
              <div class="risk-level"><span class="risk-icon">{icon}</span>
                   <span>{risk.level}</span></div>
              <div class="risk-action">{action}</div>
            </div>""",
        unsafe_allow_html=True)

    reasons = [r for r in risk.reasons[:3]]
    if reasons:
        items = "".join(f"<li>{r}</li>" for r in reasons)
        st.markdown(
            f"<div class='why'><div class='why-h'>Why</div><ul>{items}</ul></div>",
            unsafe_allow_html=True)
    if len(risk.reasons) > 3:
        with st.expander("More detail"):
            for r in risk.reasons[3:]:
                st.write(f"- {r}")


def render_metrics(risk: de.RiskAssessment, obs: pd.DataFrame,
                   advice: sw.SprayAdvice) -> None:
    """Four cards: the numbers behind the risk level, plus the next window."""
    humid = f"{risk.humid_hours_last_24h:.0f}"

    min_t = rain = "&mdash;"
    if obs is not None and not obs.empty and "timestamp" in obs.columns:
        recent = obs[obs["timestamp"] >= obs["timestamp"].max() - pd.Timedelta(hours=24)]
        if "temperature_c" in recent and recent["temperature_c"].notna().any():
            min_t = f"{recent['temperature_c'].min():.1f}"
        if "rain_mm" in recent and recent["rain_mm"].notna().any():
            rain = f"{recent['rain_mm'].sum():.1f}"

    window = advice.best if advice else None
    when = window.label.split(" ", 1) if window else None
    next_win = when[1] if when and len(when) > 1 else ("none in 3 days" if not window else window.label)
    win_note = when[0] if when else "no dry, calm daylight hours"

    cards = [
        _metric("Humid hours (24 h)", humid, " h",
                f"at or above {config.HUTTON_RH_THRESHOLD_PCT:.0f}% RH"),
        _metric("Lowest temp (24 h)", min_t, " &deg;C",
                f"blight needs {config.HUTTON_MIN_TEMP_C:.0f}&deg;C+"),
        _metric("Rain (24 h)", rain, " mm", "at the station"),
        _metric("Next spray window", next_win, "", win_note),
    ]
    for col, card in zip(st.columns(4), cards):
        col.markdown(card, unsafe_allow_html=True)


def render_spray(advice: sw.SprayAdvice) -> None:
    st.markdown("<div class='sect'>When to spray</div>", unsafe_allow_html=True)

    if not advice.has_window:
        st.markdown(
            "<div class='sect-sub'>No good spray window in the next 3 days.</div>",
            unsafe_allow_html=True)
        for r in advice.reasons:
            st.write(f"- {r}")
        _render_rejections(advice)
        return

    st.markdown(
        f"<div class='sect-sub'>Checked {advice.hours_considered} hours "
        f"({advice.evaluated_from:%a %H:%M} to {advice.evaluated_to:%a %H:%M}). "
        f"Best first.</div>", unsafe_allow_html=True)

    for i, w in enumerate(advice.windows[:4], 1):
        cls = "win" if w.quality == "GOOD" else "win win-fair"
        pill = "pill-good" if w.quality == "GOOD" else "pill-fair"
        rh = (f" &middot; leaves dry, max {w.max_humidity_pct:.0f}% RH"
              if w.max_humidity_pct else "")
        clipped = " &middot; trimmed to daylight" if w.was_clipped else ""
        st.markdown(
            f"""<div class="{cls}">
                  <div class="win-top">
                    <span class="win-when">{w.label}</span>
                    <span class="pill {pill}">{w.quality}</span>
                  </div>
                  <div class="win-meta">
                    {w.duration_hours:.1f} hours &middot; wind
                    {w.mean_wind_ms:.1f} m/s{rh} &middot; stays dry
                    {w.dry_hours_after:.0f} h afterwards{clipped}
                  </div>
                </div>""",
            unsafe_allow_html=True)
        with st.expander(f"Why window {i} works"):
            for r in w.reasons:
                st.write(f"- {r}")

    _render_rejections(advice)


# Rejection keys rendered as phrases that read inside a sentence.
_REJECT_PHRASE = {
    sw.R_NIGHT: "outside daylight",
    sw.R_WET_LEAF: "with wet leaves",
    sw.R_WINDY: "too windy",
    sw.R_CALM: "too calm",
    sw.R_RAIN_HOUR: "raining",
    sw.R_RAIN_SOON: "with rain coming within 6 hours",
    sw.R_TRUNCATED: "too near the end of the forecast to confirm",
    sw.R_SHORT: "in windows too short to be worth it",
    sw.R_NO_WIND: "missing a wind reading",
    sw.R_RAINING_NOW: "raining right now",
}


def _render_rejections(advice: sw.SprayAdvice) -> None:
    """One friendly sentence instead of a bare tally."""
    if not advice.rejection_counts:
        return
    total = sum(advice.rejection_counts.values())
    parts = [f"{n} {_REJECT_PHRASE.get(reason, reason)}"
             for reason, n in sorted(advice.rejection_counts.items(),
                                     key=lambda kv: -kv[1])]
    if len(parts) > 1:
        listed = ", ".join(parts[:-1]) + " and " + parts[-1]
    else:
        listed = parts[0]
    st.markdown(
        f"<div class='ruled'>Other hours were ruled out: {listed}. "
        f"That is {total} of {advice.hours_considered} hours checked.</div>",
        unsafe_allow_html=True)


# One chart template so every figure in the app reads as the same system:
# same fonts, muted gridlines, legend below, hover with units.
CHART_FONT = dict(family="sans-serif", size=12, color="#1f2420")
GRID = "#e6e1d7"


def style_fig(fig, height: int, *, legend: bool = False, ytitle: str = "") -> None:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=28, b=8),
        hovermode="x unified",
        plot_bgcolor="#ffffff",
        paper_bgcolor="rgba(0,0,0,0)",
        font=CHART_FONT,
        showlegend=legend,
        legend=dict(orientation="h", yanchor="top", y=-0.18,
                    x=0, bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(font_size=12, bgcolor="#ffffff",
                        bordercolor=GRID, font_family="sans-serif"),
    )
    fig.update_xaxes(showgrid=True, gridcolor=GRID, linecolor=GRID,
                     nticks=6, ticks="outside", tickcolor=GRID)
    fig.update_yaxes(showgrid=True, gridcolor=GRID, linecolor=GRID,
                     nticks=5, title_text=ytitle, zeroline=False)


def render_forecast_chart(df: pd.DataFrame) -> None:
    st.markdown("<div class='sect'>Next 7 days</div>", unsafe_allow_html=True)

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06,
        row_heights=[0.36, 0.32, 0.32],
        subplot_titles=("Humidity - blight needs 90%+",
                        "Temperature - blight needs 10°C+",
                        "Rain"),
    )

    if "humidity_pct" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["timestamp"], y=df["humidity_pct"], name="Humidity",
            line=dict(color="#1565c0", width=2), hovertemplate="%{y:.0f}%<extra></extra>",
        ), row=1, col=1)
        fig.add_hline(
            y=config.HUTTON_RH_THRESHOLD_PCT, row=1, col=1,
            line=dict(color="#c62828", width=2, dash="dash"),
            annotation_text=f"{config.HUTTON_RH_THRESHOLD_PCT:.0f}% blight threshold",
            annotation_position="top left",
            annotation_font=dict(size=10, color="#c62828"),
        )
        fig.update_yaxes(range=[0, 105], row=1, col=1, title_text="%")

    if "temperature_c" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["timestamp"], y=df["temperature_c"], name="Temperature",
            line=dict(color="#ef6c00", width=2), hovertemplate="%{y:.1f}°C<extra></extra>",
        ), row=2, col=1)
        fig.add_hline(
            y=config.HUTTON_MIN_TEMP_C, row=2, col=1,
            line=dict(color="#c62828", width=2, dash="dash"),
            annotation_text=f"{config.HUTTON_MIN_TEMP_C:.0f}°C blight threshold",
            annotation_position="bottom left",
            annotation_font=dict(size=10, color="#c62828"),
        )
        fig.update_yaxes(row=2, col=1, title_text="°C")

    if "rain_mm" in df.columns:
        fig.add_trace(go.Bar(
            x=df["timestamp"], y=df["rain_mm"], name="Rain",
            marker_color="#0277bd", hovertemplate="%{y:.1f} mm<extra></extra>",
        ), row=3, col=1)
        fig.update_yaxes(row=3, col=1, title_text="mm")

    style_fig(fig, 640)
    fig.update_xaxes(nticks=6)
    st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})


def render_outlook_table(df: pd.DataFrame) -> None:
    daily = fc.daily_outlook(df)
    if daily.empty:
        return
    show = pd.DataFrame({
        "Day": pd.to_datetime(daily["day"]).dt.strftime("%a %d %b"),
        "Min °C": daily["temp_min_c"].round(1),
        "Max °C": daily["temp_max_c"].round(1),
        "Rain mm": daily["rain_total_mm"].round(1),
        "Humid hrs": daily.get("humid_hours", 0),
        "Wet hrs": daily.get("wet_hours", 0),
    })
    st.dataframe(show, hide_index=True, width='stretch')
    st.caption(
        f"'Humid hrs' counts hours at or above {config.HUTTON_RH_THRESHOLD_PCT:.0f}% "
        f"humidity - six of them in a day, with a minimum above "
        f"{config.HUTTON_MIN_TEMP_C:.0f}°C, makes a Hutton day."
    )


# --------------------------------------------------------------------------
# SMS preview
# --------------------------------------------------------------------------

def render_sms_preview(alert: alerts_mod.Alert,
                       decision: alerts_mod.SendDecision) -> None:
    st.markdown("<div class='sect'>The message a farmer would get</div>",
                unsafe_allow_html=True)
    st.markdown(
        "<div class='sect-sub'>Farmers receive an SMS &mdash; they do not open "
        "this dashboard. One message, under 160 characters, on any phone.</div>",
        unsafe_allow_html=True)

    if decision.send:
        st.markdown(
            f"<div class='fresh fresh-live'><div>&#128241;</div><div>"
            f"<b>Would send now</b> &mdash; {decision.reason}</div></div>",
            unsafe_allow_html=True)
    else:
        st.markdown(
            f"<div class='fresh fresh-cached'><div>&#128276;</div><div>"
            f"<b>Would not send</b> &mdash; {decision.reason}</div></div>",
            unsafe_allow_html=True)

    for col, lang in zip(st.columns(2), msg.LANGUAGES):
        text = alert.sms_for(lang)
        badge = ""
        if lang == "sw" and not msg.SW_TRANSLATION_REVIEWED:
            badge = ("<span class='badge badge-warn'>"
                     "Kiswahili pending native review</span>")
        with col:
            st.markdown(
                f"<div class='lang-h'>{msg.LANGUAGE_NAMES[lang]}{badge}</div>"
                f"<div class='phone'><div class='bubble'>{text}</div>"
                f"<div class='bubble-meta'>{len(text)} / "
                f"{config.SMS_MAX_CHARS} characters</div></div>",
                unsafe_allow_html=True)

    mode_note = sms_sender.describe_config()
    st.markdown(
        f"<div style='margin-top:0.7rem'>"
        f"<span class='badge badge-mute'>Dry-run: no real SMS sent</span></div>"
        f"<div class='mnote' style='margin-top:0.4rem'>{mode_note}</div>",
        unsafe_allow_html=True)
    if not msg.SW_TRANSLATION_REVIEWED:
        st.caption(
            "The Kiswahili was written by a non-native speaker and is awaiting "
            "review. Open questions are listed in services/messages.py.")


LEVEL_ORDER = {"LOW": 0, "MODERATE": 1, "HIGH": 2, de.UNKNOWN: -1}


def render_backtest(results: pd.DataFrame, scope: str = "season") -> None:
    st.markdown("<div class='sect'>Would it have worked?</div>"
                "<div class='sect-sub'>Replaying the real station history one "
                "morning at a time. At each point the engine sees only what had "
                "happened by then &mdash; no peeking ahead.</div>",
                unsafe_allow_html=True)
    if scope == "season":
        st.caption(
            f"Showing the **{config.BACKTEST_WINDOW['label']} short rains** "
            f"({config.BACKTEST_WINDOW['start']} to "
            f"{config.BACKTEST_WINDOW['end']}) - the window quoted in the "
            f"README. Reproduce with `analysis/backtest.py --from "
            f"{config.BACKTEST_WINDOW['start']} --to "
            f"{config.BACKTEST_WINDOW['end']}`."
        )
    else:
        st.caption(
            "Showing the **full station archive**. These numbers are larger "
            "than the OND 2025 figures quoted in the README, which cover the "
            "short-rains season only."
        )

    if results.empty:
        st.info(
            "No history to replay yet. Run `analysis/fetch_history.py` to "
            "download the station archive, then reload this page."
        )
        return

    days = len(results)
    n_high = int((results["level"] == "HIGH").sum())
    n_sent = int(results["would_send"].sum())
    eligible = int(results["eligible"].sum()) if "eligible" in results else n_sent

    era5 = get_rain_validation()
    era5_note = "&mdash;"
    if era5.ok:
        st_ = vr.summarise_comparison(era5.df)
        era5_note = f"{abs(st_['ratio'] - 1) * 100:.0f}"

    cards = [
        _metric("Days replayed", f"{days:,}", "", "one morning at a time"),
        _metric("HIGH-risk days", str(n_high), "", "Hutton criteria fired"),
        _metric("Texts sent", str(n_sent), f" of {eligible}",
                "send policy, no information hidden"),
        _metric("Station vs ERA5", era5_note, " %", "apart on the season total"),
    ]
    for col, card in zip(st.columns(4), cards):
        col.markdown(card, unsafe_allow_html=True)

    if eligible:
        st.caption(
            f"The send policy cuts {eligible} texts down to {n_sent} "
            f"({100 * (1 - n_sent / eligible):.0f}% fewer) without hiding "
            f"anything - every day's level is still shown below."
        )

    fig = go.Figure()
    colours = [config.RISK_COLORS.get(l, config.RISK_COLORS[de.UNKNOWN])
               for l in results["level"]]
    fig.add_trace(go.Bar(
        x=pd.to_datetime(results["date"]),
        y=[LEVEL_ORDER.get(l, -1) + 1 for l in results["level"]],
        marker_color=colours, name="Risk",
        customdata=results[["level", "humid_hours_24h", "consecutive_hutton_days"]],
        hovertemplate=("%{x|%d %b %Y}<br>%{customdata[0]}<br>"
                       "%{customdata[1]:.0f} humid hours<br>"
                       "Hutton run: %{customdata[2]} days<extra></extra>"),
    ))
    # Mark the KMD advisory window, when our data covers it.
    adv_start = pd.Timestamp(config.KMD_ADVISORY["start"])
    adv_end = pd.Timestamp(config.KMD_ADVISORY["end"])
    dates = pd.to_datetime(results["date"])
    if dates.min() <= adv_end and dates.max() >= adv_start:
        fig.add_vrect(
            x0=adv_start, x1=adv_end,
            fillcolor="#1565c0", opacity=0.13, line_width=0, layer="below",
            annotation_text="KMD advisory", annotation_position="top left",
            annotation_font=dict(size=10, color="#1565c0"),
        )

    sent = results[results["would_send"]]
    if len(sent):
        fig.add_trace(go.Scatter(
            x=pd.to_datetime(sent["date"]), y=[3.35] * len(sent),
            mode="markers", name="SMS sent",
            marker=dict(symbol="triangle-down", size=11, color="#1a1a1a"),
            hovertemplate="%{x|%d %b}<br>SMS sent<extra></extra>",
        ))
    style_fig(fig, 340, legend=True)
    fig.update_layout(bargap=0.05)
    fig.update_yaxes(tickmode="array", tickvals=[0, 1, 2, 3],
                     ticktext=["Unknown", "Low", "Moderate", "High"],
                     range=[0, 3.8], showgrid=True, gridcolor=GRID)
    st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})

    if dates.min() <= adv_end and dates.max() >= adv_start:
        st.caption(
            f"The shaded band is the **{config.KMD_ADVISORY['label']}** "
            f"({config.KMD_ADVISORY['start']} to {config.KMD_ADVISORY['end']}), "
            f"which the department expected to mark the onset of the short "
            f"rains. Our engine flagged HIGH risk on 29 Oct - 1 Nov from "
            f"station humidity and temperature alone, with no knowledge of the "
            f"advisory. It corroborates that the weather was genuinely unusual "
            f"in that window - it is **not** evidence that blight occurred, "
            f"since no one surveyed the fields. "
            f"[Source]({config.KMD_ADVISORY['source']})"
        )

    st.markdown("**Messages that would have gone out**")
    if sent.empty:
        st.write("None - conditions never justified a text.")
    else:
        for _, r in sent.iterrows():
            with st.expander(f"{r['date']} — {r['level']}"):
                st.caption(r.get("send_reason", ""))
                st.write(f"**English:** {r['sms_en']}")
                st.write(f"**Kiswahili:** {r['sms_sw']}")
                st.caption(r["reason"])


def render_era5_check(data: vr.ValidationData) -> None:
    """Station rainfall against ERA5 reanalysis for the canonical window."""
    st.markdown("<div class='sect'>Cross-check against ERA5 reanalysis</div>",
                unsafe_allow_html=True)

    if not data.ok:
        st.info(
            f"The ERA5 comparison is not available right now "
            f"({data.error}). It is a credibility check on our rainfall "
            f"reconstruction, not an input to any decision, so nothing else "
            f"on this page depends on it.",
            icon="🛰️",
        )
        return

    df = data.df.copy()
    df["date"] = pd.to_datetime(df["date"])
    stats = vr.summarise_comparison(df)

    st.write(
        "Our rainfall is **reconstructed** by differencing the station's "
        "running daily total, because the per-interval field records only ~6% "
        "of what actually falls. A reconstruction deserves an independent "
        "check, so we compare it against ERA5."
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Station total", f"{stats['station_total']:.1f} mm",
              help="Our reconstruction, summed over the season.")
    c2.metric("ERA5 total", f"{stats['era5_total']:.1f} mm",
              help="ERA5 reanalysis for the same coordinates.")
    c3.metric("Ratio", f"{stats['ratio']:.2f}",
              help="Station divided by ERA5. 1.00 would be exact agreement.")
    c4.metric("Rainy-day agreement", f"{stats['agree_pct']:.0f}%",
              help=f"Days where both or neither exceeded "
                   f"{vr.WET_DAY_MM:.0f} mm.")

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df["date"], y=df["station_mm"], name="Station (reconstructed)",
        marker_color="#0277bd",
        hovertemplate="%{x|%d %b}<br>Station %{y:.1f} mm<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=df["date"], y=df["era5_mm"], name="ERA5 reanalysis",
        marker_color="#f9a825",
        hovertemplate="%{x|%d %b}<br>ERA5 %{y:.1f} mm<extra></extra>",
    ))
    style_fig(fig, 330, legend=True, ytitle="mm/day")
    fig.update_layout(barmode="group", bargap=0.15, bargroupgap=0.05)
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    st.markdown(
        f"**ERA5 is a reanalysis** - a model reconstruction that assimilates "
        f"satellite and ground observations onto a **~9 km grid** - "
        f"**not a direct satellite measurement**. The station is a single "
        f"point inside one of those cells, and rainfall here is convective, so "
        f"a storm can soak one field and miss the next: that is why the "
        f"day-to-day correlation is weak "
        f"(Spearman {stats['spearman']:.2f}) while the **seasonal totals agree "
        f"to within {abs(stats['ratio'] - 1) * 100:.0f}%**, which is the "
        f"meaningful result. A reconstruction that double-counted or missed "
        f"the daily counter resets would not land this close to an independent "
        f"record over {stats['days']} days."
    )
    st.caption(
        f"This is a credibility check, **not calibration** - no station value "
        f"is adjusted toward ERA5, and nothing here feeds the risk engine. "
        f"Source: ERA5 via the Open-Meteo archive, {data.note}. "
        f"Reproduce with `analysis/validate_rain.py`."
    )


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

APP_URL = "https://shamba-pulse-jkuat.streamlit.app/"
REPO_URL = "https://github.com/Hackathons-4thyear/HackTheWeather"


def render_sidebar() -> None:
    """About, credits and links. Technical controls live in the tabs."""
    with st.sidebar:
        st.markdown("<div class='sect'>About</div>", unsafe_allow_html=True)
        st.markdown(
            "**Shamba Pulse** turns the JKUAT Conduit weather station into two "
            "decisions: *is my crop at risk of late blight*, and *when should "
            "I spray*."
        )
        st.markdown(
            "**Farmers** get the answer as an SMS in English or Kiswahili "
            "&mdash; they do not open a dashboard during planting season."
        )
        st.markdown(
            "**This dashboard is for extension officers, agrovets and "
            "cooperative field teams** who advise many farmers and need the "
            "reasoning behind each alert."
        )
        st.divider()

        st.markdown("<div class='mlabel'>Data sources</div>",
                    unsafe_allow_html=True)
        for line in (
            "**JKUAT Conduit weather station** &mdash; JHUB Africa / JKUAT",
            "**Open-Meteo** &mdash; 7-day forecast",
            "**ERA5 reanalysis** via the Open-Meteo archive &mdash; rainfall "
            "cross-check",
            "**Kenya Meteorological Department** &mdash; Oct 2025 advisory, "
            "cited as context",
        ):
            st.markdown(f"- {line}")
        st.divider()

        st.markdown(f"[Source on GitHub]({REPO_URL})")
        st.markdown(f"[README]({REPO_URL}#readme)")
        st.markdown(
            f"[Data quality report]({REPO_URL}/blob/main/docs/DATA_QUALITY.md)")
        st.caption(
            "Risk follows the Hutton criteria for potato and tomato late "
            "blight. SMS runs in dry-run: nothing is actually sent."
        )


def main() -> None:
    render_sidebar()

    status = get_observations(7)
    forecast = get_forecast()

    risk = de.assess(status.df)
    forecast_hourly = forecast.df if forecast.ok else pd.DataFrame()
    raining = sw.is_raining_now(status.df) if status.is_real else None
    advice = (sw.find_windows(forecast_hourly, currently_raining=raining)
              if forecast.ok else sw.SprayAdvice(
                  reasons=["No forecast available, so no spray windows."]))

    alert = alerts_mod.build_alert(risk, advice)
    decision = alerts_mod.AlertPolicy().decide(alert)

    render_hero(risk, advice, status.is_demo)

    tab_now, tab_week, tab_test = st.tabs(
        ["Today", "This week", "Does it work?"])

    with tab_now:
        render_risk_card(risk, advice, status.is_demo)
        render_metrics(risk, status.df, advice)
        st.write("")
        render_freshness(status, forecast)
        with st.expander("Where this data comes from"):
            if status.attempts:
                for line in status.attempts:
                    st.write(f"- {line}")
            render_data_quality_body()
        st.divider()
        render_spray(advice)
        st.divider()
        render_sms_preview(alert, decision)

    with tab_week:
        if forecast.ok:
            render_forecast_chart(forecast.df)
            render_outlook_table(forecast.df)
        else:
            st.warning(f"Forecast unavailable: {forecast.error}")

        st.divider()
        st.markdown("<div class='sect'>Recent station readings</div>",
                    unsafe_allow_html=True)
        if status.df.empty:
            st.info("No station readings to show.")
        else:
            if status.is_demo:
                st.error("These are DEMO readings, not from the station.",
                         icon="⚠️")
            recent = dp.to_hourly(status.df).tail(72)
            rf = go.Figure()
            rf.add_trace(go.Scatter(
                x=recent["timestamp"], y=recent["humidity_pct"],
                name="Humidity", line=dict(color="#1565c0", width=2),
                hovertemplate="%{y:.0f}%<extra>Humidity</extra>"))
            rf.add_trace(go.Scatter(
                x=recent["timestamp"], y=recent["temperature_c"],
                name="Temperature", line=dict(color="#ef6c00", width=2),
                hovertemplate="%{y:.1f}°C<extra>Temperature</extra>"))
            rf.add_hline(
                y=config.HUTTON_RH_THRESHOLD_PCT,
                line=dict(color="#c62828", dash="dash", width=2),
                annotation_text=f"{config.HUTTON_RH_THRESHOLD_PCT:.0f}% blight threshold",
                annotation_position="top left",
                annotation_font=dict(size=10, color="#c62828"))
            style_fig(rf, 310, legend=True)
            st.plotly_chart(rf, width="stretch",
                            config={"displayModeBar": False})

    with tab_test:
        with st.expander("Options"):
            scope_label = st.radio(
                "Period to replay",
                [f"{config.BACKTEST_WINDOW['label']} short rains "
                 f"(1 Oct - 31 Dec 2025)",
                 "Full station archive (Jun 2025 - Sep 2026)"],
                index=0,
                help="The season view is the window quoted in the README. The "
                     "full archive covers every day the station has recorded.",
            )
        scope = ("season" if scope_label.startswith(config.BACKTEST_WINDOW["label"])
                 else "all")
        render_backtest(get_backtest(bt.DEFAULT_ALERT_HOUR, scope), scope)
        st.divider()
        render_era5_check(get_rain_validation())

    st.divider()
    st.caption(
        f"Shamba Pulse &middot; Hack The Weather 2026 &middot; JHUB Africa / "
        f"JKUAT. Station data from the JKUAT Conduit station; forecast and ERA5 "
        f"from Open-Meteo. [Source]({REPO_URL})"
    )


if __name__ == "__main__":
    main()
