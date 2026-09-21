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

# --------------------------------------------------------------------------
# Credentials (from .env only - see .env.example)
# --------------------------------------------------------------------------

CONDUIT_API_KEY = os.getenv("CONDUIT_API_KEY", "")
CONDUIT_EMAIL = os.getenv("CONDUIT_EMAIL", "")
AT_USERNAME = os.getenv("AT_USERNAME", "sandbox")
AT_API_KEY = os.getenv("AT_API_KEY", "")

# Default to dry-run so a misconfigured demo never sends real SMS by accident.
SMS_DRY_RUN = os.getenv("SMS_DRY_RUN", "true").strip().lower() in {"1", "true", "yes"}

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

# Risk levels, ordered low -> high. Used for sorting and colour-coding.
RISK_LEVELS = ("LOW", "MODERATE", "HIGH")

RISK_COLORS = {
    "LOW": "#2e7d32",       # green
    "MODERATE": "#f9a825",  # amber
    "HIGH": "#c62828",      # red
    "UNKNOWN": "#757575",   # grey - not enough data
}
