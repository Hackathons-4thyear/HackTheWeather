"""SMS message templates in English and Kiswahili.

All farmer-facing wording lives here, deliberately separated from the logic in
alerts.py, so a native Kiswahili speaker can review and correct the translations
without reading any Python beyond these strings.

=============================================================================
KISWAHILI NEEDS NATIVE REVIEW BEFORE THE DEMO
=============================================================================
The Kiswahili below was written by a non-native speaker and is UNREVIEWED. It
aims for the plain Kenyan agricultural register a smallholder around Juja would
actually use, not textbook Kiswahili sanifu. Terms a reviewer should check hardest
are marked with a [REVIEW] comment. See REVIEW_NOTES at the bottom of this file
for the specific questions to put to the reviewer.
=============================================================================

Every rendered message must fit SMS_MAX_CHARS (160) so it stays one GSM-7
segment and costs the farmer one message. Templates therefore come in pairs:
a `full` version and a `short` fallback used when the full one overflows.
"""

from __future__ import annotations

import pandas as pd


LANGUAGES: tuple[str, ...] = ("en", "sw")

LANGUAGE_NAMES = {"en": "English", "sw": "Kiswahili"}

# Flip to True once a native speaker has signed off. The dashboard reads this
# to decide whether to show the "translation unreviewed" badge.
SW_TRANSLATION_REVIEWED = False

# Short sender tag. Kept ASCII and brand-short to save characters.
BRAND = "Shamba Pulse"


# --------------------------------------------------------------------------
# Day naming - relative where possible, because it is shorter AND clearer
# --------------------------------------------------------------------------

_DAY_WORDS = {
    "en": {"today": "today", "tomorrow": "tomorrow"},
    # [REVIEW] "leo" = today, "kesho" = tomorrow. Standard and unambiguous.
    "sw": {"today": "leo", "tomorrow": "kesho"},
}

# Weekday names, used only beyond tomorrow (today/tomorrow are shorter AND
# clearer). Kiswahili uses the FULL forms - the 3-letter abbreviations we tried
# first are not something a farmer would recognise at a glance.
_WEEKDAYS = {
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "sw": ["Jumatatu", "Jumanne", "Jumatano", "Alhamisi", "Ijumaa",
           "Jumamosi", "Jumapili"],
}


# --------------------------------------------------------------------------
# Kiswahili time
# --------------------------------------------------------------------------
# East African Kiswahili counts hours from DAWN, not from midnight. The day
# starts at 06:00, which is "saa 12", and 07:00 is "saa 1". So a farmer told
# "saa 3" understands 09:00, and being told "09:00" in a Kiswahili sentence
# reads as a foreign convention.
#
#   clock 06:00 -> saa 12 asubuhi        clock 15:00 -> saa 9 mchana
#   clock 07:00 -> saa 1 asubuhi         clock 17:00 -> saa 11 jioni
#   clock 12:00 -> saa 6 mchana          clock 00:00 -> saa 6 usiku
#
# Period words: asubuhi (morning), mchana (midday/afternoon), jioni (evening),
# usiku (night).

# [REVIEW] Period boundaries. These are the common Kenyan split, but the
# asubuhi/mchana and mchana/jioni edges vary by speaker.
_SW_PERIODS = (
    (6, 12, "asubuhi"),    # 06:00-11:59
    (12, 16, "mchana"),    # 12:00-15:59
    (16, 19, "jioni"),     # 16:00-18:59
)
_SW_NIGHT = "usiku"        # 19:00-05:59


def swahili_period(hour: int) -> str:
    """The Kiswahili word for the part of the day a clock hour falls in."""
    for lo, hi, word in _SW_PERIODS:
        if lo <= hour < hi:
            return word
    return _SW_NIGHT


def to_swahili_time(hour: int, minute: int = 0) -> str:
    """Convert a 24-hour clock time to spoken Kiswahili time.

    >>> to_swahili_time(9, 0)
    'saa 3 asubuhi'
    >>> to_swahili_time(17, 0)
    'saa 11 jioni'
    >>> to_swahili_time(6, 30)
    'saa 12 na nusu asubuhi'
    """
    if not 0 <= hour <= 23:
        raise ValueError(f"hour must be 0-23, got {hour}")
    if not 0 <= minute <= 59:
        raise ValueError(f"minute must be 0-59, got {minute}")

    # Count from 06:00. The 12 o'clock slot is written 12, not 0.
    swahili_hour = (hour - 6) % 12 or 12

    if minute == 0:
        mins = ""
    elif minute == 30:
        mins = " na nusu"            # [REVIEW] "and a half" - standard
    else:
        mins = f" na dakika {minute}"

    return f"saa {swahili_hour}{mins} {swahili_period(hour)}"


def relative_day(ts: pd.Timestamp, now: pd.Timestamp, lang: str) -> str:
    """'today' / 'tomorrow' / 'Wed' - whichever is shortest and clearest."""
    words = _DAY_WORDS.get(lang, _DAY_WORDS["en"])
    delta_days = (ts.normalize() - now.normalize()).days
    if delta_days == 0:
        return words["today"]
    if delta_days == 1:
        return words["tomorrow"]
    return _WEEKDAYS.get(lang, _WEEKDAYS["en"])[ts.weekday()]


def format_window(start: pd.Timestamp, end: pd.Timestamp,
                  now: pd.Timestamp, lang: str) -> str:
    """A time range a farmer can act on, in that language's own convention.

    English: 'tomorrow 06:30-09:00'
    Kiswahili: 'kesho saa 12 na nusu asubuhi-saa 3 asubuhi'
    """
    day = relative_day(start, now, lang)
    if lang == "sw":
        return (f"{day} {to_swahili_time(start.hour, start.minute)}"
                f"-{to_swahili_time(end.hour, end.minute)}")
    return f"{day} {start:%H:%M}-{end:%H:%M}"


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------
# Placeholders:
#   {brand}  {hours}  {window}  {crop}  {days}
#
# Each entry has a `full` and a `short`. alerts.py tries `full` first and falls
# back to `short` when the rendered text exceeds SMS_MAX_CHARS.

TEMPLATES: dict[str, dict[str, dict[str, str]]] = {

    # ---- HIGH blight risk: two consecutive Hutton days -------------------
    "blight_high": {
        "en": {
            "full": ("{brand}: HIGH blight risk for {crop}. Humidity stayed "
                     "above {rh}% for {hours}h, {days} days running. Spray "
                     "{window} if you can."),
            "short": ("{brand}: HIGH blight risk, {crop}. {hours}h humid. "
                      "Spray {window}."),
        },
        "sw": {
            # [REVIEW] "baka chelewa" = late blight. "baka" alone is too vague
            # (it can mean any spot or blemish), so we name the disease fully.
            # [REVIEW] Durations use "masaa" and clock times use "saa", so
            # "masaa 11" (for 11 hours) cannot be misread as "saa 11" (17:00).
            "full": ("{brand}: HATARI KUBWA ya baka chelewa, {crop}. Unyevu "
                     "juu ya {rh}% masaa {hours}, siku {days}. "
                     "Nyunyiza {window}."),
            "short": ("{brand}: Baka chelewa, HATARI KUBWA, {crop}. "
                      "Nyunyiza {window}."),
        },
    },

    # ---- HIGH risk but no spray window available -------------------------
    "blight_high_no_window": {
        "en": {
            "full": ("{brand}: HIGH blight risk for {crop}. Humidity above "
                     "{rh}% for {hours}h. No safe spray window yet - rain or "
                     "wind. We will alert you when one opens."),
            "short": ("{brand}: HIGH blight risk, {crop}. No safe spray window "
                      "yet. Will alert you."),
        },
        "sw": {
            # [REVIEW] "hakuna muda mzuri wa kunyunyiza" = no good time to spray.
            "full": ("{brand}: HATARI KUBWA ya baka chelewa, {crop}. Unyevu "
                     "juu ya {rh}% masaa {hours}. Hakuna muda mzuri wa "
                     "kunyunyiza bado - mvua au upepo. Tutakujulisha."),
            "short": ("{brand}: HATARI KUBWA ya baka chelewa, {crop}. Hakuna "
                      "muda wa kunyunyiza bado."),
        },
    },

    # ---- MODERATE risk: conditions building ------------------------------
    "blight_moderate": {
        "en": {
            "full": ("{brand}: Blight risk rising for {crop}. Humidity above "
                     "{rh}% for {hours}h. Check your crop. Good spraying "
                     "{window}."),
            "short": ("{brand}: Blight risk rising, {crop}. Check crop. Spray "
                      "{window}."),
        },
        "sw": {
            # [REVIEW] "inaongezeka" = is increasing. "Kagua" = inspect/check.
            "full": ("{brand}: Baka chelewa inaongezeka, {crop}. Unyevu juu ya "
                     "{rh}% masaa {hours}. Kagua shamba. Nyunyiza {window}."),
            "short": ("{brand}: Baka chelewa inaongezeka, {crop}. "
                      "Nyunyiza {window}."),
        },
    },

    "blight_moderate_no_window": {
        "en": {
            "full": ("{brand}: Blight risk rising for {crop}. Humidity above "
                     "{rh}% for {hours}h. Check your crop. No safe spray "
                     "window yet."),
            "short": ("{brand}: Blight risk rising, {crop}. Check crop. No "
                      "spray window yet."),
        },
        "sw": {
            "full": ("{brand}: Baka chelewa inaongezeka, {crop}. Unyevu juu ya "
                     "{rh}% masaa {hours}. Kagua shamba lako. Hakuna muda wa "
                     "kunyunyiza bado."),
            "short": ("{brand}: Baka chelewa inaongezeka, {crop}. Kagua "
                      "shamba lako."),
        },
    },

    # ---- LOW risk: all clear ---------------------------------------------
    "blight_low": {
        "en": {
            "full": ("{brand}: Blight risk LOW for {crop}. Conditions have "
                     "been too dry for the disease. No spray needed today."),
            "short": ("{brand}: Blight risk LOW, {crop}. No spray needed "
                      "today."),
        },
        "sw": {
            # [REVIEW] "ndogo" = small/low. "Hakuna haja ya kunyunyiza" = no
            # need to spray.
            "full": ("{brand}: Hatari ya baka chelewa ni NDOGO, {crop}. Hali "
                     "ni kavu mno kwa ugonjwa. Hakuna haja ya kunyunyiza leo."),
            "short": ("{brand}: Baka chelewa hatari NDOGO, {crop}. Hakuna haja "
                      "ya kunyunyiza leo."),
        },
    },

    # ---- Spray window only (no elevated disease risk) --------------------
    "spray_window": {
        "en": {
            "full": ("{brand}: Good spraying conditions {window}. Dry, wind "
                     "{wind} m/s, leaves dry. Best chance in the next 3 days."),
            "short": ("{brand}: Good spraying {window}. Dry, wind {wind} m/s."),
        },
        "sw": {
            # [REVIEW] "majani makavu" = dry leaves. "upepo" = wind.
            "full": ("{brand}: Hali nzuri ya kunyunyiza {window}. Hakuna mvua, "
                     "upepo {wind} m/s, majani makavu."),
            "short": ("{brand}: Nyunyiza {window}. Upepo {wind} m/s."),
        },
    },

    # ---- Not enough data to advise ---------------------------------------
    "insufficient_data": {
        "en": {
            "full": ("{brand}: Not enough weather data to judge blight risk "
                     "right now. The station has gaps. We will alert you when "
                     "readings return."),
            "short": ("{brand}: Not enough station data to judge blight risk "
                      "now."),
        },
        "sw": {
            # [REVIEW] "takwimu" = data. This may be too formal for farmers -
            # ask whether a plainer phrasing would land better.
            "full": ("{brand}: Hakuna takwimu za kutosha kupima hatari ya baka "
                     "chelewa sasa. Kituo cha hali ya hewa kina mapengo. "
                     "Tutakujulisha."),
            "short": ("{brand}: Hakuna takwimu za kutosha kupima hatari ya "
                      "baka chelewa sasa."),
        },
    },
}


# Crop names used inside the templates.
CROPS = {
    "en": {"tomato": "tomato", "potato": "potato", "both": "tomato/potato"},
    # [REVIEW] nyanya = tomato, viazi = potato. "viazi" alone can mean sweet
    # potato in some areas; "viazi mviringo" is the unambiguous Irish potato,
    # but it costs 9 characters. Ask the reviewer if "viazi" is safe here.
    "sw": {"tomato": "nyanya", "potato": "viazi", "both": "nyanya/viazi"},
}


REVIEW_NOTES = """
Questions for the native Kiswahili reviewer
-------------------------------------------
1. KISWAHILI TIME is used throughout, counted from dawn: 06:00 = "saa 12",
   07:00 = "saa 1", 09:00 = "saa 3 asubuhi", 17:00 = "saa 11 jioni". Please
   check (a) the hour arithmetic, (b) the period words, and (c) the period
   BOUNDARIES we chose: asubuhi 06:00-11:59, mchana 12:00-15:59,
   jioni 16:00-18:59, usiku 19:00-05:59. The mchana/jioni edge especially -
   some speakers put it at 17:00 rather than 16:00.
2. To avoid "saa 11" (17:00) being misread as "11 hours", DURATIONS are written
   "masaa 11" and clock times "saa 11". Does that distinction actually work in
   speech, or does it need rewording entirely (e.g. "kwa muda wa masaa 11")?
3. "baka chelewa" for late blight - is this what farmers around Juja/Kiambu
   actually say? Alternatives heard elsewhere: "ukungu", or the English
   "blight". We moved off bare "baka" because it can mean any spot or blemish.
4. "viazi" for potato. WE ASSUME THIS READS AS IRISH POTATO, the crop late
   blight affects, not sweet potato (viazi vitamu). If that assumption is wrong
   the alerts are aimed at the wrong crop - "viazi mviringo" is unambiguous but
   costs 9 characters. Please confirm which is safe.
5. Weekday names are now the full forms (Jumatatu, Jumanne, Jumatano, Alhamisi,
   Ijumaa, Jumamosi, Jumapili). "leo"/"kesho" are used for today/tomorrow.
6. "Nyunyiza dawa" vs "piga dawa" for spraying - which is more common locally?
7. "HATARI KUBWA" in caps for emphasis - does shouting read as urgent or rude?
8. Register overall: is this the plain spoken Kiswahili a smallholder uses, or
   has it drifted into Kiswahili sanifu that sounds like a government notice?
"""
