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

import config

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

# Abbreviated weekday names, used only beyond tomorrow.
_WEEKDAYS = {
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    # [REVIEW] Kiswahili weekdays abbreviated to 3 letters to fit an SMS.
    # Full forms: Jumatatu, Jumanne, Jumatano, Alhamisi, Ijumaa, Jumamosi,
    # Jumapili. Ask the reviewer whether these short forms read naturally or
    # whether a date like "23/9" would be clearer to a farmer.
    "sw": ["Jtt", "Jnn", "Jtn", "Alh", "Ijm", "Jms", "Jpl"],
}


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
    """'tomorrow 06:30-09:00' - a time range a farmer can act on."""
    day = relative_day(start, now, lang)
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
            # [REVIEW] "baka" is the common Kenyan term for blight on tomato
            # and potato. Alternative: "ukungu" (fungus/mildew), or farmers may
            # simply say "blight". Ask which the Juja/Kiambu area uses.
            # [REVIEW] "unyevu" = humidity/dampness. "Hatari kubwa" = big danger.
            "full": ("{brand}: Hatari KUBWA ya baka kwa {crop}. Unyevu ulikaa "
                     "juu ya {rh}% kwa saa {hours}, siku {days} mfululizo. "
                     "Nyunyiza dawa {window}."),
            "short": ("{brand}: Hatari KUBWA ya baka, {crop}. Saa {hours} za "
                      "unyevu. Nyunyiza {window}."),
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
            "full": ("{brand}: Hatari KUBWA ya baka kwa {crop}. Unyevu juu ya "
                     "{rh}% kwa saa {hours}. Hakuna muda mzuri wa kunyunyiza "
                     "bado - mvua au upepo. Tutakujulisha."),
            "short": ("{brand}: Hatari KUBWA ya baka, {crop}. Hakuna muda wa "
                      "kunyunyiza bado. Tutakujulisha."),
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
            "full": ("{brand}: Hatari ya baka inaongezeka kwa {crop}. Unyevu "
                     "juu ya {rh}% kwa saa {hours}. Kagua shamba lako. Muda "
                     "mzuri wa kunyunyiza {window}."),
            "short": ("{brand}: Hatari ya baka inaongezeka, {crop}. Kagua "
                      "shamba. Nyunyiza {window}."),
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
            "full": ("{brand}: Hatari ya baka inaongezeka kwa {crop}. Unyevu "
                     "juu ya {rh}% kwa saa {hours}. Kagua shamba. Hakuna muda "
                     "wa kunyunyiza bado."),
            "short": ("{brand}: Hatari ya baka inaongezeka, {crop}. Kagua "
                      "shamba. Hakuna muda wa kunyunyiza."),
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
            "full": ("{brand}: Hatari ya baka ni NDOGO kwa {crop}. Hali ni "
                     "kavu mno kwa ugonjwa. Hakuna haja ya kunyunyiza leo."),
            "short": ("{brand}: Hatari ya baka NDOGO, {crop}. Hakuna haja ya "
                      "kunyunyiza leo."),
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
                     "upepo {wind} m/s, majani makavu. Nafasi bora kwa siku 3 "
                     "zijazo."),
            "short": ("{brand}: Hali nzuri ya kunyunyiza {window}. Upepo "
                      "{wind} m/s."),
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
            # [REVIEW] "takwimu" = data. "kituo cha hali ya hewa" = weather
            # station. This may be too formal - a simpler phrasing may land
            # better with farmers.
            "full": ("{brand}: Hakuna takwimu za kutosha kupima hatari ya baka "
                     "sasa. Kituo cha hali ya hewa kina mapengo. Tutakujulisha."),
            "short": ("{brand}: Hakuna takwimu za kutosha kupima hatari ya "
                      "baka sasa."),
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
1. "baka" for late blight - is this what farmers around Juja/Kiambu actually
   say? Alternatives heard elsewhere: "ukungu", or the English "blight".
2. "viazi" for potato - does it read as Irish potato here, or will farmers
   read sweet potato? Is "viazi mviringo" worth the extra 9 characters?
3. Weekday abbreviations Jtt/Jnn/Jtn/Alh/Ijm/Jms/Jpl - natural, or would a
   numeric date like "23/9" be clearer?
4. "Nyunyiza dawa" vs "piga dawa" for spraying - which is more common locally?
5. "Hatari KUBWA" in caps for emphasis - does shouting read as urgent or rude?
6. Register overall: is this the plain spoken Kiswahili a smallholder uses, or
   has it drifted into Kiswahili sanifu that sounds like a government notice?
"""
