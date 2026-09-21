"""Central configuration for Shamba Pulse.

Every threshold the decision engine uses lives here, with a comment explaining
WHY the number was chosen. Judges (and farmers) should be able to read this file
and understand exactly what the app considers "risky" or "safe".

Nothing in here reads the network. Credentials come from .env via os.environ.
"""

from __future__ import annotations

import os
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Load .env from the project root if present. Never raises if the file is absent.
PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = PROJECT_ROOT / "data"

# --------------------------------------------------------------------------
# Location & time
# --------------------------------------------------------------------------

# JKUAT main campus, Juja, Kiambu County. The Conduit station sits here, and the
# Open-Meteo forecast is pulled for the same point so station and forecast agree.
JKUAT_LAT = -1.0914
JKUAT_LON = 37.0147
SITE_NAME = "JKUAT, Juja (Kiambu County)"

# Kenya has no DST, so this is a constant +03:00, but using a named zone keeps
# timestamps honest if the data is ever exported from another locale.
LOCAL_TZ = ZoneInfo("Africa/Nairobi")

# The station logs a reading every 15 minutes -> 4 readings per hour.
STATION_INTERVAL_MINUTES = 15

# How old the newest reading may be before we stop calling the data current.
# The API answering successfully is NOT the same as the data being fresh: the
# station publishes on a lag, so a healthy connection can still hand back
# readings many hours old. Two hours is eight missed readings - well beyond
# normal jitter, and enough to matter for an overnight humidity count.
STALE_AFTER_HOURS = 2

# --------------------------------------------------------------------------
# Credentials (from .env only - see .env.example)
# --------------------------------------------------------------------------

def _secret(name: str, default: str = "") -> str:
    """Read a credential from the environment, then from Streamlit secrets.

    Locally, .env supplies these. On Streamlit Community Cloud there is no .env
    file - secrets live in st.secrets, entered through the app's settings page.
    Environment wins, so a local .env can override a deployed secret during
    debugging.

    Streamlit is imported lazily and defensively: config.py is imported by the
    CLI scripts too (fetch_history, backtest, ...), which must not require
    streamlit to be installed or a script context to exist.
    """
    value = os.getenv(name)
    if value:
        return value

    try:
        import streamlit as st  # noqa: PLC0415 - deliberately lazy

        # Accessing st.secrets with no secrets.toml raises; treat that as absent.
        return str(st.secrets[name])
    except Exception:  # noqa: BLE001 - any failure just means "not set there"
        return default


CONDUIT_API_KEY = _secret("CONDUIT_API_KEY")
CONDUIT_EMAIL = _secret("CONDUIT_EMAIL")
AT_USERNAME = _secret("AT_USERNAME", "sandbox")
AT_API_KEY = _secret("AT_API_KEY")

# Default to dry-run so a misconfigured demo never sends real SMS by accident.
SMS_DRY_RUN = _secret("SMS_DRY_RUN", "true").strip().lower() in {"1", "true", "yes"}

# --------------------------------------------------------------------------
# APIs
# --------------------------------------------------------------------------

CONDUIT_URL = "https://conduit.jhubafrica.com/data.php"
CONDUIT_TIMEOUT_SECONDS = 30  # Station endpoint is slow on wide date ranges.

# Identify ourselves honestly so the JHUB operators can see who is polling and
# contact the team if we are hammering the station.
USER_AGENT = (
    "ShambaPulse/0.1 (Hack The Weather 2026; JKUAT Conduit client; "
    "+https://github.com/hack-the-weather/shamba-pulse)"
)

# Politeness settings for bulk history download (analysis/fetch_history.py).
CONDUIT_REQUEST_DELAY_SECONDS = 1.0  # Pause between chunk requests.
CONDUIT_MAX_RETRIES = 3              # Per chunk, with exponential backoff.
CONDUIT_CHUNK_DAYS = 7               # Preferred chunk size; shrinks to 1 on failure.

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_TIMEOUT_SECONDS = 20
FORECAST_DAYS = 7  # Judging asks for a 7-day outlook.

# --------------------------------------------------------------------------
# Late blight - Hutton criteria
# --------------------------------------------------------------------------
# The Hutton Criteria (James Hutton Institute, the UK successor to the Smith
# Period) is the standard operational warning for potato/tomato late blight,
# Phytophthora infestans. A "Hutton day" needs BOTH of:
#   * minimum air temperature >= 10 degC over the day, and
#   * at least 6 hours with relative humidity >= 90%.
# TWO CONSECUTIVE Hutton days = HIGH risk: that is long enough for spores to
# germinate, infect the leaf and sporulate again.

HUTTON_MIN_TEMP_C = 10.0        # Below ~10 degC the pathogen's growth stalls.
HUTTON_RH_THRESHOLD_PCT = 90.0  # Leaf wetness proxy - we have no leaf-wetness sensor.
HUTTON_MIN_HUMID_HOURS = 6      # Hours at/above the RH threshold needed in one day.
HUTTON_CONSECUTIVE_DAYS = 2     # Two in a row is the official HIGH-risk trigger.

# --------------------------------------------------------------------------
# Late blight - "humid hours" fallback risk
# --------------------------------------------------------------------------
# Hutton needs a FULL day of data. During an ongoing day, or when the station has
# gaps, we still owe the farmer an answer. So we grade the last 24 hours purely
# on how many hours sat at RH >= 90%. Bands are set just below / at / above the
# Hutton hour count so the two measures tell a consistent story.

HUMID_HOURS_WINDOW_HOURS = 24    # Look back one full day.
HUMID_HOURS_MODERATE = 4         # >= 4 h humid: conditions building, stay alert.
HUMID_HOURS_HIGH = 6             # >= 6 h humid: meets the Hutton hour bar.

# Temperature gate for the fallback risk: if it never got warm enough for the
# pathogen, humid hours alone do not justify a warning.
HUMID_HOURS_MIN_TEMP_C = 10.0

# --------------------------------------------------------------------------
# Spray windows
# --------------------------------------------------------------------------
# A spray window is a stretch of forecast hours when fungicide will actually stay
# on the leaf and land where it is aimed.

# Rain washes fungicide off before it is rain-fast. Contact fungicides need
# roughly 4-6 h dry; we require 6 h to be safe on the farmer's money.
SPRAY_DRY_HOURS_REQUIRED = 6

# Any hour with more than a trace counts as "wet". 0.2 mm is about the smallest
# increment a tipping-bucket gauge resolves, so below that is noise.
SPRAY_RAIN_TRACE_MM = 0.2

# Wind band. Below ~1 m/s the air is too still: droplets hang and drift
# unpredictably, and inversions trap spray. Above ~4 m/s too much blows off
# target. 1-4 m/s is the standard agronomic advice.
SPRAY_WIND_MIN_MS = 1.0
SPRAY_WIND_MAX_MS = 4.0

# Minimum length of a usable window, AFTER daylight clipping and the wet-leaf
# veto. A farmer needs time to mix the tank and walk the rows; under 2 h is not
# worth the trip.
SPRAY_MIN_WINDOW_HOURS = 2

# Spraying happens in daylight only. Juja sits at latitude -1.09, essentially on
# the equator, so day length barely changes through the year - sunrise stays
# within a few minutes of 06:30 and sunset of 18:40 in every month. We stop at
# 18:30 so a window never runs into dusk.
# NOTE: this limits when SPRAYING may happen. The 6-hour rain-free requirement
# after spraying is still checked against the full forecast, night included.
SPRAY_DAYLIGHT_START = time(6, 30)
SPRAY_DAYLIGHT_END = time(18, 30)

# Wet-leaf veto. Dew or fog sitting on the leaf dilutes the fungicide and makes
# it run off instead of sticking. This is deliberately the SAME threshold as the
# Hutton humidity criterion - if the air is humid enough to count toward blight
# risk, it is humid enough to wet the leaf. Referenced, never duplicated, so the
# two can not drift apart. In practice this pushes morning windows to start
# after the dew has burned off.
SPRAY_MAX_HUMIDITY_PCT = HUTTON_RH_THRESHOLD_PCT

# How far ahead to look for windows.
SPRAY_LOOKAHEAD_HOURS = 72

# Spraying in the heat of the day wastes product to evaporation, and midday is
# when pollinators are most active. Prefer early morning / late afternoon.
SPRAY_PREFERRED_HOURS = tuple(range(6, 11)) + tuple(range(15, 19))  # 06:00-10:59, 15:00-18:59

# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------

SMS_MAX_CHARS = 160  # One GSM-7 segment. Longer costs the farmer more.

# --------------------------------------------------------------------------
# SMS send policy - what gets TEXTED, as opposed to what gets shown
# --------------------------------------------------------------------------
# The dashboard always shows every day's risk level. This policy decides only
# which days are worth spending a farmer's attention (and an SMS) on.
# A service that texts every morning gets ignored, and an ignored alert is
# worth nothing on the day it finally matters.

# Levels that are eligible to send at all. LOW and UNKNOWN never text: there is
# no action to take, and "no news" is the same information.
SMS_ALWAYS_SEND_LEVELS = ("HIGH",)

# Levels that text only when the risk has RISEN into them, not while they sit
# there. A week of "risk rising" messages says nothing new after the first.
SMS_ESCALATION_ONLY_LEVELS = ("MODERATE",)

# Minimum days between two texts at the SAME level. Stops a level that flaps
# LOW -> MODERATE -> LOW from texting every other morning.
SMS_COOLDOWN_DAYS = 3

# An escalation INTO this level ignores the cooldown entirely. Two consecutive
# Hutton days is the moment the whole service exists for; it must never be
# suppressed by a timer.
SMS_COOLDOWN_OVERRIDE_LEVEL = "HIGH"

# Risk levels, ordered low -> high. Used for sorting and colour-coding.
RISK_LEVELS = ("LOW", "MODERATE", "HIGH")

# --------------------------------------------------------------------------
# External corroboration
# --------------------------------------------------------------------------
# The Kenya Meteorological Department issued a heavy-rainfall advisory for
# 23-30 October 2025 naming Kiambu among the affected counties, and expected it
# to mark the onset of the short rains.
#
# Our engine independently flagged HIGH blight risk on 2025-10-29 to 11-01 from
# station humidity and temperature alone. The advisory is shown on the backtest
# timeline as CONTEXT: it corroborates that the weather was genuinely unusual
# in that window. It is NOT evidence that blight occurred - nobody surveyed the
# fields - and must never be presented as validation of the disease model.
# The canonical backtest window quoted in the README, Devpost and demo script:
# the calendar short-rains season, exactly October-December 2025. We do NOT
# extend it into January to capture the tail of the final HIGH episode - moving
# a boundary to improve a result is how numbers stop being trustworthy.
BACKTEST_WINDOW = {"start": "2025-10-01", "end": "2025-12-31", "label": "OND 2025"}

KMD_ADVISORY = {
    "start": "2025-10-23",
    "end": "2025-10-30",
    "label": "KMD heavy-rainfall advisory (Kiambu named)",
    "source": "https://allafrica.com/stories/202510230054.html",
}

RISK_COLORS = {
    "LOW": "#2e7d32",       # green
    "MODERATE": "#f9a825",  # amber
    "HIGH": "#c62828",      # red
    "UNKNOWN": "#757575",   # grey - not enough data
}
