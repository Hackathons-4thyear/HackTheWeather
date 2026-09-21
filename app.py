"""Shamba Pulse - farm decisions from the JKUAT Conduit weather station.

Streamlit dashboard. Written farmer-first: the answer comes before the data,
plain language before jargon, and anything we are unsure about says so.

Presentation lives in ui/styles.css and ui/components.py; this module wires the
services to those components and computes nothing the engine has not already
produced.

    streamlit run app.py
"""

from __future__ import annotations

import html
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
from ui import components as C  # noqa: E402

APP_URL = "https://shamba-pulse-jkuat.streamlit.app/"
REPO_URL = "https://github.com/Hackathons-4thyear/HackTheWeather"

st.set_page_config(
    page_title="Shamba Pulse",
    page_icon="🌱",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown(C.load_css(), unsafe_allow_html=True)


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
    """One compact strip per source, saying plainly what the page is built on."""
    esc = C.esc
    if status.source == ds.DEMO:
        kind, glyph = "demo", "&#9888;"
        body = ("<b>DEMO DATA &mdash; not real station readings.</b> No live or "
                "cached Conduit data was available, so everything below the "
                "forecast is generated to demonstrate how the app behaves.")
    elif status.source == ds.CACHED:
        kind, glyph = "cached", "&#128451;"
        body = (f"<b>Station unreachable &mdash; showing cached history.</b> "
                f"Real Conduit data, {esc(status.age_text())}. "
                f"{esc(status.detail)}")
    elif status.is_stale:
        kind, glyph = "stale", "&#128337;"
        body = (f"<b>Station connected, newest reading is "
                f"{esc(status.age_text())}</b> "
                f"({esc(format(status.latest, '%a %d %b %H:%M'))}). The station "
                f"publishes on a lag, so risk below is based on data up to that "
                f"time, not this minute.")
    else:
        kind, glyph = "live", "&#128225;"
        body = f"<b>Live station data.</b> {esc(status.detail)}"
    st.markdown(C.freshness(kind, body, glyph), unsafe_allow_html=True)

    if forecast.ok:
        st.markdown(C.freshness(
            "cached",
            f"<b>7-day forecast is live</b> from Open-Meteo for "
            f"{esc(config.SITE_NAME)} &mdash; real data, independent of the "
            f"station.", "&#127782;"), unsafe_allow_html=True)
    else:
        st.markdown(C.freshness(
            "stale", f"Forecast unavailable: {esc(forecast.error)}",
            "&#127782;"), unsafe_allow_html=True)


def render_hero(status: ds.DataStatus, risk: de.RiskAssessment) -> None:
    if status.source == ds.DEMO:
        src_text, dot = "DEMO DATA", "#B3261E"
    elif status.source == ds.CACHED:
        src_text, dot = "Cached station data", "#5F6B73"
    elif status.is_stale:
        src_text, dot = "Station connected (lagging)", "#E8A317"
    else:
        src_text, dot = "Live station data", "#2E7D4F"

    updated = (f"Updated {status.latest:%a %d %b %H:%M}"
               if status.latest is not None else "Updated —")
    pills = [(src_text, dot), (updated, ""), ("OND 2026 El Niño season", "")]
    st.markdown(C.hero(risk.level, pills), unsafe_allow_html=True)


RISK_ACTION = {
    "HIGH": "Spray your tomatoes and potatoes as soon as you safely can.",
    "MODERATE": "Check your tomatoes and potatoes today.",
    "LOW": "No blight action needed today.",
    de.UNKNOWN: "Not enough station data to advise right now.",
}


def render_action(risk: de.RiskAssessment, advice: sw.SprayAdvice) -> None:
    window = advice.best if advice else None
    when = window.label if window else None
    st.markdown(C.action_card(RISK_ACTION.get(risk.level, RISK_ACTION[de.UNKNOWN]),
                              when, list(risk.reasons)),
                unsafe_allow_html=True)
    if len(risk.reasons) > 3:
        with st.expander("More detail"):
            for r in risk.reasons[3:]:
                st.write(f"- {r}")


def render_tiles(risk: de.RiskAssessment, obs: pd.DataFrame,
                 advice: sw.SprayAdvice) -> None:
    min_t = rain = "—"
    if obs is not None and not obs.empty and "timestamp" in obs.columns:
        recent = obs[obs["timestamp"] >= obs["timestamp"].max() - pd.Timedelta(hours=24)]
        if "temperature_c" in recent and recent["temperature_c"].notna().any():
            min_t = f"{recent['temperature_c'].min():.1f}"
        if "rain_mm" in recent and recent["rain_mm"].notna().any():
            rain = f"{recent['rain_mm'].sum():.1f}"

    window = advice.best if advice else None
    if window:
        parts = window.label.split(" ", 1)
        win_val = parts[1] if len(parts) > 1 else window.label
        win_note = f"{parts[0]} · wind {window.mean_wind_ms:.1f} m/s"
    else:
        win_val, win_note = "None", "no dry, calm daylight hours in 3 days"

    st.markdown(C.tiles([
        ("droplet", "Humid hours (24 h)", f"{risk.humid_hours_last_24h:.0f}", " h",
         f"≥{config.HUTTON_MIN_HUMID_HOURS} h triggers a Hutton day"),
        ("thermometer", "Lowest temp (24 h)", min_t, " °C",
         f"blight needs ≥{config.HUTTON_MIN_TEMP_C:.0f} °C"),
        ("rain", "Rain (24 h)", rain, " mm", "measured at the station"),
        ("clock", "Next spray window", win_val, "", win_note),
    ]), unsafe_allow_html=True)


def render_week(forecast_df: pd.DataFrame) -> None:
    """Seven day tiles built from the forecast we already compute."""
    st.markdown(C.section(
        "The week ahead",
        "Humid hours per day are what drive the Hutton criteria."),
        unsafe_allow_html=True)

    days: list[dict] = []
    if forecast_df is not None and not forecast_df.empty:
        daily = fc.daily_outlook(forecast_df)
        for _, row in daily.head(7).iterrows():
            humid = int(row.get("humid_hours", 0) or 0)
            tmin = row.get("temp_min_c")
            warm = tmin is not None and tmin >= config.HUTTON_MIN_TEMP_C
            if humid >= config.HUTTON_MIN_HUMID_HOURS and warm:
                level = "HIGH"
            elif humid >= config.HUMID_HOURS_MODERATE:
                level = "MODERATE"
            else:
                level = "LOW"
            d = pd.Timestamp(row["day"])
            days.append({"name": f"{d:%a}", "date": f"{d:%d %b}",
                         "level": level, "humid_hours": humid})
    st.markdown(C.week_strip(days), unsafe_allow_html=True)
    if days:
        st.caption(
            "A day shown red meets both Hutton conditions on the forecast "
            "— two of those in a row is what triggers a HIGH warning. "
            "Forecast days are an outlook, not a measurement."
        )


def _daylight_rows(advice: sw.SprayAdvice) -> list[dict]:
    """Turn windows into per-day bars positioned across the daylight span.

    Pure geometry over values spray_window already produced - no thresholds or
    decisions are made here.
    """
    d0 = config.SPRAY_DAYLIGHT_START
    d1 = config.SPRAY_DAYLIGHT_END
    span_min = (d1.hour * 60 + d1.minute) - (d0.hour * 60 + d0.minute)
    if span_min <= 0:
        return []

    by_day: dict = {}
    for w in advice.windows:
        key = w.start.date()
        start_min = w.start.hour * 60 + w.start.minute - (d0.hour * 60 + d0.minute)
        end_min = w.end.hour * 60 + w.end.minute - (d0.hour * 60 + d0.minute)
        left = max(0.0, 100.0 * start_min / span_min)
        right = min(100.0, 100.0 * end_min / span_min)
        if right <= left:
            continue
        rh = (f", RH up to {w.max_humidity_pct:.0f}%"
              if w.max_humidity_pct else "")
        by_day.setdefault(key, {"day": f"{w.start:%a %d}", "segments": []})
        by_day[key]["segments"].append({
            "left_pct": left, "width_pct": right - left, "quality": w.quality,
            "title": (f"{w.label} · {w.quality} · wind "
                      f"{w.mean_wind_ms:.1f} m/s{rh}"),
        })
    return [by_day[k] for k in sorted(by_day)]


def render_spray(advice: sw.SprayAdvice) -> None:
    st.markdown(C.section(
        "When to spray",
        "Daylight hours only. Green means the fungicide will stay where you "
        "put it."), unsafe_allow_html=True)

    if not advice.has_window:
        reasons = " ".join(advice.reasons) if advice.reasons else ""
        st.markdown(
            f"<div class='sp-note'><b>No good spray window in the next "
            f"3 days.</b> {C.esc(reasons)}</div>", unsafe_allow_html=True)
        _render_rejections(advice)
        return

    rows = _daylight_rows(advice)
    if rows:
        st.markdown(
            C.spray_timeline(rows,
                             f"{config.SPRAY_DAYLIGHT_START:%H:%M}",
                             f"{config.SPRAY_DAYLIGHT_END:%H:%M}"),
            unsafe_allow_html=True)

    best = advice.best
    rh = f" &middot; RH up to {best.max_humidity_pct:.0f}%" if best.max_humidity_pct else ""
    st.markdown(
        f"<div class='sp-note'><b>Best: {C.esc(best.label)}</b> &mdash; "
        f"{best.duration_hours:.1f} hours &middot; {C.esc(best.quality)} "
        f"&middot; wind {best.mean_wind_ms:.1f} m/s{rh} &middot; stays dry "
        f"{best.dry_hours_after:.0f} h afterwards.</div>",
        unsafe_allow_html=True)

    with st.expander(f"Why {C.esc(best.label)} works"):
        for r in best.reasons:
            st.write(f"- {r}")
    if len(advice.windows) > 1:
        with st.expander(f"Other windows ({len(advice.windows) - 1})"):
            for w in advice.windows[1:5]:
                st.write(f"**{w.label}** - {w.duration_hours:.1f} h, {w.quality}, "
                         f"wind {w.mean_wind_ms:.1f} m/s")

    _render_rejections(advice)


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
    if not advice.rejection_counts:
        return
    total = sum(advice.rejection_counts.values())
    parts = [f"{n} {_REJECT_PHRASE.get(r, r)}"
             for r, n in sorted(advice.rejection_counts.items(),
                                key=lambda kv: -kv[1])]
    listed = (", ".join(parts[:-1]) + " and " + parts[-1]
              if len(parts) > 1 else parts[0])
    st.markdown(
        f"<div class='sp-note'>Other hours were ruled out: {C.esc(listed)}. "
        f"That is {total} of {advice.hours_considered} hours checked.</div>",
        unsafe_allow_html=True)


# One Plotly template so every figure reads as the same system.
CHART_FONT = dict(family="Inter, system-ui, sans-serif", size=12, color="#1C2A24")
GRID = "#E4DCCD"
PAPER = "#FAF7F0"


def style_fig(fig, height: int, *, legend: bool = False, ytitle: str = "") -> None:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=26, b=8),
        hovermode="x unified",
        plot_bgcolor="#FFFFFF",
        paper_bgcolor="rgba(0,0,0,0)",
        font=CHART_FONT,
        showlegend=legend,
        legend=dict(orientation="h", yanchor="top", y=-0.18, x=0,
                    bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(font_size=12, bgcolor="#FFFFFF", bordercolor=GRID,
                        font_family="Inter, sans-serif"),
    )
    fig.update_xaxes(showgrid=True, gridcolor=GRID, linecolor=GRID,
                     nticks=6, ticks="outside", tickcolor=GRID)
    fig.update_yaxes(showgrid=True, gridcolor=GRID, linecolor=GRID,
                     nticks=5, title_text=ytitle, zeroline=False)


def render_forecast_chart(df: pd.DataFrame) -> None:
    st.markdown("<div class='sp-h'>Next 7 days</div>", unsafe_allow_html=True)

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
            line=dict(color="#2E7D4F", width=2.4), hovertemplate="%{y:.0f}%<extra></extra>",
        ), row=1, col=1)
        fig.add_hline(
            y=config.HUTTON_RH_THRESHOLD_PCT, row=1, col=1,
            line=dict(color="#B3261E", width=2, dash="dash"),
            annotation_text=f"{config.HUTTON_RH_THRESHOLD_PCT:.0f}% blight threshold",
            annotation_position="top left",
            annotation_font=dict(size=11, color="#B3261E"),
        )
        fig.update_yaxes(range=[0, 105], row=1, col=1, title_text="%")

    if "temperature_c" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["timestamp"], y=df["temperature_c"], name="Temperature",
            line=dict(color="#B3261E", width=2.4), hovertemplate="%{y:.1f}°C<extra></extra>",
        ), row=2, col=1)
        fig.add_hline(
            y=config.HUTTON_MIN_TEMP_C, row=2, col=1,
            line=dict(color="#B3261E", width=2, dash="dash"),
            annotation_text=f"{config.HUTTON_MIN_TEMP_C:.0f}°C blight threshold",
            annotation_position="bottom left",
            annotation_font=dict(size=11, color="#B3261E"),
        )
        fig.update_yaxes(row=2, col=1, title_text="°C")

    if "rain_mm" in df.columns:
        fig.add_trace(go.Bar(
            x=df["timestamp"], y=df["rain_mm"], name="Rain",
            marker_color="#2E7D4F", hovertemplate="%{y:.1f} mm<extra></extra>",
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
    st.markdown(C.section(
        "The message a farmer would get",
        "Farmers receive an SMS — they do not open this dashboard. One "
        "message, under 160 characters, on any phone."), unsafe_allow_html=True)

    if decision.send:
        st.markdown(C.freshness(
            "live", f"<b>Would send now</b> &mdash; {C.esc(decision.reason)}",
            "&#128241;"), unsafe_allow_html=True)
    else:
        st.markdown(C.freshness(
            "cached", f"<b>Would not send</b> &mdash; {C.esc(decision.reason)}",
            "&#128276;"), unsafe_allow_html=True)

    labels = {"en": "English", "sw": "Kiswahili"}
    choice = st.radio("Language", [labels[l] for l in msg.LANGUAGES],
                      horizontal=True, label_visibility="collapsed",
                      key="sms_lang")
    lang = next(l for l in msg.LANGUAGES if labels[l] == choice)
    text = alert.sms_for(lang)

    clock = (f"{alert.created_at:%H:%M}" if alert.created_at is not None
             else "08:30")
    st.markdown(C.phone(text, clock=clock), unsafe_allow_html=True)

    # The Kiswahili badge is shown whichever language is on screen: the
    # translation is unreviewed whether or not the viewer happens to have the
    # toggle set to Kiswahili, and hiding that behind a click would be a way of
    # not saying it.
    marks = [(f"{len(text)} / {config.SMS_MAX_CHARS} characters", "mute"),
             ("Dry-run: no real SMS sent", "mute")]
    if not msg.SW_TRANSLATION_REVIEWED:
        marks.append(("Kiswahili pending native review", "warn"))
    st.markdown(C.badges(marks), unsafe_allow_html=True)

    if not msg.SW_TRANSLATION_REVIEWED:
        st.caption(
            "The Kiswahili was written by a non-native speaker and is awaiting "
            "review. Open questions are listed in services/messages.py.")
    st.caption(sms_sender.describe_config())


def render_station_card(status: ds.DataStatus) -> None:
    """Where the data comes from, with the station on a map."""
    st.markdown(C.section(
        "Where the data comes from",
        "One weather station, on the JKUAT campus in Juja."),
        unsafe_allow_html=True)

    left, right = st.columns([1, 1])
    with left:
        st.map(pd.DataFrame({"lat": [config.JKUAT_LAT],
                             "lon": [config.JKUAT_LON]}),
               zoom=11, size=180, color="#2E7D4F")
    with right:
        st.markdown(
            f"<div class='sp-card'><b>JKUAT Conduit station</b><br>"
            f"<span style='color:#6B7770'>{config.JKUAT_LAT:.4f}, "
            f"{config.JKUAT_LON:.4f} &middot; 1,524 m</span>"
            f"{C.chips(['Humidity', 'Temperature', 'Rainfall', 'Wind', 'Pressure'])}"
            f"<div class='sp-tile-note' style='margin-top:.6rem'>"
            f"A reading every {config.STATION_INTERVAL_MINUTES} minutes "
            f"&mdash; 96 a day. Archive: 476 days, 46,183 readings."
            f"</div></div>", unsafe_allow_html=True)
    st.caption(
        "There is no soil-moisture or leaf-wetness sensor on this station, and "
        "we never invent one — humidity at or above "
        f"{config.HUTTON_RH_THRESHOLD_PCT:.0f}% is our stated leaf-wetness proxy."
    )


LEVEL_ORDER = {"LOW": 0, "MODERATE": 1, "HIGH": 2, de.UNKNOWN: -1}


def render_backtest(results: pd.DataFrame, scope: str = "season") -> None:
    st.markdown("<div class='sp-h'>Would it have worked?</div>"
                "<div class='sp-h-sub'>Replaying the real station history one "
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
    era5_note = "—"
    if era5.ok:
        _st = vr.summarise_comparison(era5.df)
        era5_note = f"within {abs(_st['ratio'] - 1) * 100:.0f}%"

    st.markdown(C.stat_band([
        (f"{days:,}", "days replayed, one morning at a time"),
        (str(n_high), "days at HIGH risk"),
        (f"{n_sent} of {eligible}", "texts sent, under the send policy"),
        (era5_note, "of ERA5 on the season rainfall total"),
    ]), unsafe_allow_html=True)

    if eligible:
        st.caption(
            f"The send policy cuts {eligible} texts down to {n_sent} "
            f"({100 * (1 - n_sent / eligible):.0f}% fewer) without hiding "
            f"anything - every day's level is still shown below."
        )

    fig = go.Figure()
    colours = [C.risk_bg(l) for l in results["level"]]
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
            fillcolor="#0F3D2E", opacity=0.10, line_width=0, layer="below",
            annotation_text="KMD advisory", annotation_position="top left",
            annotation_font=dict(size=11, color="#0F3D2E"),
        )

    sent = results[results["would_send"]]
    if len(sent):
        fig.add_trace(go.Scatter(
            x=pd.to_datetime(sent["date"]), y=[3.35] * len(sent),
            mode="markers", name="SMS sent",
            marker=dict(symbol="triangle-down", size=11, color="#1C2A24"),
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
    st.markdown("<div class='sp-h'>Cross-check against ERA5 reanalysis</div>",
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
        marker_color="#2E7D4F",
        hovertemplate="%{x|%d %b}<br>Station %{y:.1f} mm<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=df["date"], y=df["era5_mm"], name="ERA5 reanalysis",
        marker_color="#F2B705",
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


def render_findings() -> None:
    """The four data-quality findings as icon cards."""
    st.markdown(C.section(
        "What we found in the station data",
        "We did not trust the obvious field names. Three of these would have "
        "produced confidently wrong advice."), unsafe_allow_html=True)
    st.markdown(C.finding_cards([
        ("rain", "Rain field captures only 6% of rainfall",
         "The per-interval field rg1 recorded 18.6 mm against 301.8 mm actual "
         "over OND 2025. The spray adviser would have believed it never rains. "
         "We now derive rainfall by differencing the running daily total."),
        ("droplet", "Rain gauge 2 is faulty",
         "Its daily total resets about 21 times a day instead of once, "
         "reconstructing to 4,365 mm in a ~300 mm season. It had been used to "
         "fill gaps in gauge 1. It no longer is."),
        ("clock", "Timestamps are UTC, not local",
         "Kenya is UTC+3. Read naively, every overnight humid-hours count "
         "shifts three hours and the daily boundary moves - silently. We "
         "convert to Africa/Nairobi on ingest."),
        ("thermometer", "Cross-checked against ERA5",
         "Our reconstructed rainfall totals 301.8 mm against 268.6 mm from "
         "ERA5 reanalysis - within 12% over 92 days. A credibility check, not "
         "a calibration."),
    ]), unsafe_allow_html=True)


def main() -> None:
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

    render_hero(status, risk)

    tab_now, tab_week, tab_test = st.tabs(
        ["🌱 Today", "🗓 This week", "✅ Does it work?"])

    with tab_now:
        render_action(risk, advice)
        render_tiles(risk, status.df, advice)
        render_freshness(status, forecast)
        with st.expander("Where this data comes from"):
            if status.attempts:
                st.markdown("**What we tried, in order**")
                for line in status.attempts:
                    st.write(f"- {line}")
            st.markdown(
                f"Station readings come from the **JKUAT Conduit station** "
                f"(a reading every {config.STATION_INTERVAL_MINUTES} minutes); "
                f"the forecast comes from **Open-Meteo**. We check the sensors "
                f"rather than trusting them — the four faults we found, "
                f"with numbers, are in the **Does it work?** tab and in "
                f"[docs/DATA_QUALITY.md]({REPO_URL}/blob/main/docs/DATA_QUALITY.md)."
            )
        st.divider()
        render_spray(advice)
        st.divider()
        render_sms_preview(alert, decision)

    with tab_week:
        render_week(forecast_hourly)
        st.divider()
        if forecast.ok:
            render_forecast_chart(forecast.df)
            render_outlook_table(forecast.df)
        else:
            st.markdown(C.freshness(
                "stale", f"Forecast unavailable: {C.esc(forecast.error)}. The "
                f"week ahead needs the Open-Meteo forecast; everything else on "
                f"this page still works from station data.", "&#127782;"),
                unsafe_allow_html=True)

        st.divider()
        st.markdown(C.section("Recent station readings"), unsafe_allow_html=True)
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
                name="Humidity", line=dict(color="#2E7D4F", width=2.4),
                hovertemplate="%{y:.0f}%<extra>Humidity</extra>"))
            rf.add_trace(go.Scatter(
                x=recent["timestamp"], y=recent["temperature_c"],
                name="Temperature", line=dict(color="#B3261E", width=2.4),
                hovertemplate="%{y:.1f}°C<extra>Temperature</extra>"))
            rf.add_hline(
                y=config.HUTTON_RH_THRESHOLD_PCT,
                line=dict(color="#B3261E", dash="dash", width=2),
                annotation_text=f"{config.HUTTON_RH_THRESHOLD_PCT:.0f}% blight threshold",
                annotation_position="top left",
                annotation_font=dict(size=11, color="#B3261E"))
            style_fig(rf, 310, legend=True)
            st.plotly_chart(rf, width="stretch",
                            config={"displayModeBar": False})
        st.divider()
        render_station_card(status)

    with tab_test:
        with st.expander("Options"):
            scope_label = st.radio(
                "Period to replay",
                [f"{config.BACKTEST_WINDOW['label']} short rains "
                 f"(1 Oct - 31 Dec 2025)",
                 "Full station archive (Jun 2025 - Sep 2026)"],
                index=0,
                help="The season view is the window quoted in the README.",
            )
        scope = ("season" if scope_label.startswith(config.BACKTEST_WINDOW["label"])
                 else "all")
        render_backtest(get_backtest(bt.DEFAULT_ALERT_HOUR, scope), scope)
        st.divider()
        render_findings()
        st.divider()
        render_era5_check(get_rain_validation())

    st.markdown(C.footer(REPO_URL), unsafe_allow_html=True)


if __name__ == "__main__":
    main()
