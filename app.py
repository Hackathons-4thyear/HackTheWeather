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
st.markdown("""
<style>
  .block-container {padding-top: 1.2rem; padding-bottom: 3rem; max-width: 46rem;}
  .risk-card {border-radius: 14px; padding: 1.1rem 1.2rem; color: #fff;
              margin-bottom: 0.9rem;}
  .risk-level {font-size: 2.1rem; font-weight: 800; line-height: 1.1;
               letter-spacing: -0.5px;}
  .risk-sub {font-size: 1rem; opacity: 0.95; margin-top: 0.15rem;}
  .why-box {background: #f4f6f8; border-left: 4px solid #8a9299;
            border-radius: 6px; padding: 0.8rem 1rem; margin-top: 0.5rem;}
  .win-card {border: 1px solid #d8dde2; border-left: 5px solid #2e7d32;
             border-radius: 10px; padding: 0.75rem 0.95rem; margin-bottom: 0.6rem;
             background: #fff;}
  .win-fair {border-left-color: #f9a825;}
  .win-title {font-weight: 700; font-size: 1.08rem;}
  .win-meta {color: #5b6670; font-size: 0.88rem; margin-top: 0.15rem;}
  .sms-box {background: #eef3ee; border: 1px solid #cbd9cb; border-radius: 10px;
            padding: 0.8rem 0.9rem; font-size: 0.95rem; min-height: 8.5rem;}
  .sms-meta {color: #5b6670; font-size: 0.8rem; margin-top: 0.45rem;}
  .badge {display: inline-block; padding: 0.12rem 0.5rem; border-radius: 999px;
          font-size: 0.72rem; font-weight: 700; margin-left: 0.3rem;}
  .badge-warn {background: #fff3cd; color: #7a5b00; border: 1px solid #e8d08a;}
  @media (max-width: 640px) {
    .risk-level {font-size: 1.8rem;}
    .block-container {padding-left: 0.8rem; padding-right: 0.8rem;}
  }
</style>
""", unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Data loading (cached so a rerun does not re-hit the station)
# --------------------------------------------------------------------------

@st.cache_data(ttl=600, show_spinner="Reading the weather station...")
def get_observations(days: int) -> ds.DataStatus:
    return ds.load_observations(days=days)


@st.cache_data(ttl=900, show_spinner="Fetching the 7-day forecast...")
def get_forecast() -> fc.ForecastResult:
    return fc.fetch_forecast()


@st.cache_data(ttl=3600, show_spinner="Replaying the history...")
def get_backtest(alert_hour: int) -> pd.DataFrame:
    hist, _ = bt.load_history()
    if hist.empty:
        return pd.DataFrame()
    return bt.run_backtest(hist, alert_hour=alert_hour, with_spray=False)


# --------------------------------------------------------------------------
# Banner
# --------------------------------------------------------------------------

def render_status_banner(status: ds.DataStatus, forecast: fc.ForecastResult) -> None:
    """Say plainly where every number on the page came from."""
    if status.source == ds.LIVE:
        st.success(f"**Live station data** - {status.detail}", icon="📡")
    elif status.source == ds.CACHED:
        st.warning(
            f"**Station is unreachable - showing cached history.** "
            f"This is real Conduit data, {status.age_text()}. {status.detail}",
            icon="🗄️",
        )
    else:
        st.error(
            "### DEMO DATA - NOT REAL STATION READINGS\n"
            "No live or cached Conduit data was available, so everything below "
            "the forecast is generated to demonstrate how the app behaves.",
            icon="⚠️",
        )

    # The forecast is independent - it needs no key and is real either way.
    if forecast.ok:
        st.info(
            f"**7-day forecast is live** from Open-Meteo for {config.SITE_NAME} "
            f"- real data, independent of the station.",
            icon="🌦️",
        )
    else:
        st.warning(f"Forecast unavailable: {forecast.error}", icon="🌦️")

    if status.attempts:
        with st.expander("What we tried"):
            for line in status.attempts:
                st.write(f"- {line}")

    render_data_quality()


def render_data_quality() -> None:
    """Compact summary of what we found wrong with the station data.

    Kept short here; docs/DATA_QUALITY.md carries the full evidence. Judges and
    farmers both benefit from seeing that the numbers were checked rather than
    trusted.
    """
    with st.expander("Data quality — what we checked"):
        st.markdown(
            "We did not trust the obvious field names. Three of these would "
            "have produced confidently wrong advice."
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

RISK_HEADLINE = {
    "HIGH": ("Spray now if you can", "Blight can take the crop this week."),
    "MODERATE": ("Watch your crop closely", "Conditions are turning against you."),
    "LOW": ("No blight action needed", "Conditions have been too dry for it."),
    de.UNKNOWN: ("Not enough data to advise", "The station has gaps we will not guess across."),
}


def render_risk_card(risk: de.RiskAssessment, is_demo: bool) -> None:
    colour = config.RISK_COLORS.get(risk.level, config.RISK_COLORS[de.UNKNOWN])
    headline, sub = RISK_HEADLINE.get(risk.level, RISK_HEADLINE[de.UNKNOWN])
    demo_tag = " <span class='badge badge-warn'>DEMO DATA</span>" if is_demo else ""

    st.markdown(
        f"""<div class="risk-card" style="background:{colour}">
              <div style="font-size:0.82rem;opacity:0.9;letter-spacing:1px;">
                TOMATO / POTATO LATE BLIGHT{demo_tag}
              </div>
              <div class="risk-level">{risk.level}</div>
              <div class="risk-sub">{headline} - {sub}</div>
            </div>""",
        unsafe_allow_html=True,
    )

    st.markdown("**Why**")
    st.markdown(
        "<div class='why-box'>"
        + "".join(f"<div>• {r}</div>" for r in risk.reasons[:6])
        + "</div>",
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Hutton days in a row", risk.consecutive_hutton_days,
              help="Two in a row is the official late-blight warning.")
    c2.metric("Humid hours (24h)", f"{risk.humid_hours_last_24h:.0f}",
              help=f"Hours at or above {config.HUTTON_RH_THRESHOLD_PCT:.0f}% humidity.")
    c3.metric("Hutton verdict", risk.hutton_level,
              help="From the official criteria. HIGH here is what drives a HIGH alert.")

    if risk.data_warnings:
        for w in risk.data_warnings:
            st.caption(f"Data note: {w}")


# --------------------------------------------------------------------------
# Spray windows
# --------------------------------------------------------------------------

def render_spray(advice: sw.SprayAdvice) -> None:
    st.subheader("When to spray")

    if not advice.has_window:
        st.warning("**No good spray window in the next 3 days.**")
        for r in advice.reasons:
            st.write(f"- {r}")
        _render_rejections(advice)
        return

    st.caption(
        f"Checked {advice.hours_considered} hours "
        f"({advice.evaluated_from:%a %H:%M} to {advice.evaluated_to:%a %H:%M}). "
        f"Best first."
    )

    for i, w in enumerate(advice.windows[:5], 1):
        cls = "win-card" if w.quality == "GOOD" else "win-card win-fair"
        clipped = " · trimmed to daylight" if w.was_clipped else ""
        rh = f" · humidity up to {w.max_humidity_pct:.0f}%" if w.max_humidity_pct else ""
        st.markdown(
            f"""<div class="{cls}">
                  <div class="win-title">{i}. {w.label}</div>
                  <div class="win-meta">
                    {w.duration_hours:.1f} hours · {w.quality} (score {w.score:.2f})
                    · wind {w.mean_wind_ms:.1f} m/s{rh} · dry for
                    {w.dry_hours_after:.0f}h after{clipped}
                  </div>
                </div>""",
            unsafe_allow_html=True,
        )
        with st.expander(f"Why window {i} works"):
            for r in w.reasons:
                st.write(f"- {r}")

    _render_rejections(advice)


def _render_rejections(advice: sw.SprayAdvice) -> None:
    if not advice.rejection_counts:
        return
    total = sum(advice.rejection_counts.values())
    top = sorted(advice.rejection_counts.items(), key=lambda kv: -kv[1])
    detail = ", ".join(f"**{n}h** {reason}" for reason, n in top)
    st.caption(f"Ruled out {total} of {advice.hours_considered} hours: {detail}.")


# --------------------------------------------------------------------------
# Forecast chart
# --------------------------------------------------------------------------

def render_forecast_chart(df: pd.DataFrame) -> None:
    st.subheader("Next 7 days")

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

    fig.update_layout(
        height=620, showlegend=False, margin=dict(l=10, r=10, t=46, b=10),
        hovermode="x unified", plot_bgcolor="#fbfcfd", paper_bgcolor="#fff",
        font=dict(size=12),
    )
    fig.update_xaxes(showgrid=True, gridcolor="#eceff1")
    fig.update_yaxes(showgrid=True, gridcolor="#eceff1")
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


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
    st.dataframe(show, hide_index=True, use_container_width=True)
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
    st.subheader("The message a farmer would get")

    if decision.send:
        st.success(f"**Would send now** - {decision.reason}", icon="📲")
    else:
        st.info(f"**Would not send** - {decision.reason}", icon="🔕")

    col_en, col_sw = st.columns(2)
    for col, lang in ((col_en, "en"), (col_sw, "sw")):
        with col:
            badge = ""
            if lang == "sw" and not msg.SW_TRANSLATION_REVIEWED:
                badge = ("<span class='badge badge-warn'>"
                         "Kiswahili not yet reviewed</span>")
            text = alert.sms_for(lang)
            st.markdown(
                f"**{msg.LANGUAGE_NAMES[lang]}**{badge}", unsafe_allow_html=True)
            st.markdown(
                f"<div class='sms-box'>{text}"
                f"<div class='sms-meta'>{len(text)} / {config.SMS_MAX_CHARS} "
                f"characters</div></div>",
                unsafe_allow_html=True,
            )

    if not msg.SW_TRANSLATION_REVIEWED:
        st.caption(
            "The Kiswahili was written by a non-native speaker and is awaiting "
            "review. Open questions are listed in services/messages.py."
        )
    st.caption(sms_sender.describe_config())


# --------------------------------------------------------------------------
# Backtest tab
# --------------------------------------------------------------------------

LEVEL_ORDER = {"LOW": 0, "MODERATE": 1, "HIGH": 2, de.UNKNOWN: -1}


def render_backtest(results: pd.DataFrame) -> None:
    st.subheader("Would it have worked?")
    st.write(
        "Replaying the real station history one morning at a time. At each "
        "point the engine sees only what had happened by then - no peeking "
        "ahead."
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

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Days replayed", f"{days:,}")
    c2.metric("HIGH-risk days", n_high)
    c3.metric("Texts sent", n_sent,
              help="After the send policy: escalation-gated, with a cooldown.")
    c4.metric("Without the policy", eligible,
              help="Every eligible day would have texted.")

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
    sent = results[results["would_send"]]
    if len(sent):
        fig.add_trace(go.Scatter(
            x=pd.to_datetime(sent["date"]), y=[3.35] * len(sent),
            mode="markers", name="SMS sent",
            marker=dict(symbol="triangle-down", size=11, color="#1a1a1a"),
            hovertemplate="%{x|%d %b}<br>SMS sent<extra></extra>",
        ))
    fig.update_layout(
        height=330, margin=dict(l=10, r=10, t=30, b=10),
        yaxis=dict(tickmode="array", tickvals=[0, 1, 2, 3],
                   ticktext=["Unknown", "Low", "Moderate", "High"],
                   range=[0, 3.8]),
        showlegend=True, legend=dict(orientation="h", y=1.15),
        plot_bgcolor="#fbfcfd", bargap=0.05,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

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


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> None:
    st.title("🌱 Shamba Pulse")
    st.caption(
        f"Late-blight warnings and spray timing for tomato and potato "
        f"around {config.SITE_NAME}."
    )

    status = get_observations(7)
    forecast = get_forecast()
    render_status_banner(status, forecast)

    risk = de.assess(status.df)

    forecast_hourly = forecast.df if forecast.ok else pd.DataFrame()
    raining = sw.is_raining_now(status.df) if status.is_real else None
    advice = sw.find_windows(forecast_hourly, currently_raining=raining) \
        if forecast.ok else sw.SprayAdvice(
            reasons=["No forecast available, so no spray windows."])

    alert = alerts_mod.build_alert(risk, advice)
    decision = alerts_mod.AlertPolicy().decide(alert)

    tab_now, tab_week, tab_test = st.tabs(
        ["Today", "This week", "Does it work?"])

    with tab_now:
        render_risk_card(risk, status.is_demo)
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
        st.subheader("Recent station readings")
        if status.df.empty:
            st.info("No station readings to show.")
        else:
            if status.is_demo:
                st.error("These are DEMO readings, not from the station.",
                         icon="⚠️")
            recent = dp.to_hourly(status.df).tail(72)
            rf = go.Figure()
            rf.add_trace(go.Scatter(x=recent["timestamp"], y=recent["humidity_pct"],
                                    name="Humidity %", line=dict(color="#1565c0")))
            rf.add_trace(go.Scatter(x=recent["timestamp"], y=recent["temperature_c"],
                                    name="Temp °C", line=dict(color="#ef6c00")))
            rf.add_hline(y=config.HUTTON_RH_THRESHOLD_PCT,
                         line=dict(color="#c62828", dash="dash"))
            rf.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                             hovermode="x unified", plot_bgcolor="#fbfcfd",
                             legend=dict(orientation="h", y=1.15))
            st.plotly_chart(rf, use_container_width=True,
                            config={"displayModeBar": False})

    with tab_test:
        render_backtest(get_backtest(bt.DEFAULT_ALERT_HOUR))

    st.divider()
    st.caption(
        "Shamba Pulse · Hack The Weather 2026 · JHUB Africa / JKUAT. "
        "Risk follows the Hutton criteria for potato and tomato late blight. "
        "Forecast from Open-Meteo. Station data from the JKUAT Conduit station."
    )


if __name__ == "__main__":
    main()
