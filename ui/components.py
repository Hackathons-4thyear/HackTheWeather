"""HTML components for the Shamba Pulse dashboard.

Every function here is pure: it takes plain values and returns an HTML string.
Nothing imports Streamlit, nothing touches the engine, and nothing computes a
number - app.py passes in values the services already produced. That keeps the
components unit-testable and keeps presentation strictly separate from logic.

SAFETY: every caller-supplied string goes through esc() before it reaches the
markup. The dashboard renders reasons, station detail and SMS text that
ultimately originate in station data and message templates, so none of it is
assumed safe.
"""

from __future__ import annotations

import html
from pathlib import Path

CSS_PATH = Path(__file__).with_name("styles.css")

# Risk palette. Mirrors config.RISK_COLORS conceptually but is the *bold*
# identity used by this UI; app.py passes the level and we resolve it here so
# every component agrees on what MODERATE looks like.
RISK_BG = {
    "HIGH": "#B3261E",
    "MODERATE": "#E8A317",
    "LOW": "#2E7D4F",
    "UNKNOWN": "#5F6B73",
}

# MODERATE amber only reaches 2.17:1 against white, so it takes ink instead
# (6.89:1). Checked against WCAG AA.
RISK_FG = {
    "HIGH": "#FFFFFF",
    "MODERATE": "#1C2A24",
    "LOW": "#FFFFFF",
    "UNKNOWN": "#FFFFFF",
}

RISK_GLYPH = {"HIGH": "!", "MODERATE": "!", "LOW": "✓", "UNKNOWN": "?"}


def esc(value) -> str:
    """HTML-escape any value, including None and numbers."""
    return html.escape("" if value is None else str(value), quote=True)


def risk_bg(level: str) -> str:
    return RISK_BG.get(level, RISK_BG["UNKNOWN"])


def risk_fg(level: str) -> str:
    return RISK_FG.get(level, RISK_FG["UNKNOWN"])


def load_css() -> str:
    """The stylesheet wrapped in a <style> tag, ready for st.markdown."""
    return f"<style>{CSS_PATH.read_text(encoding='utf-8')}</style>"


# --------------------------------------------------------------------------
# 1. Hero
# --------------------------------------------------------------------------

def _leaf_pattern() -> str:
    """Low-opacity contour/leaf motif behind the hero. Inline so it needs no
    network and cannot fail to load."""
    return (
        '<svg class="sp-hero-bg" viewBox="0 0 800 260" preserveAspectRatio="none" '
        'aria-hidden="true">'
        '<defs><pattern id="spleaf" width="120" height="120" '
        'patternUnits="userSpaceOnUse" patternTransform="rotate(18)">'
        '<path d="M60 18c22 14 30 40 22 62-8 22-30 34-52 26 2-26 12-70 30-88z" '
        'fill="none" stroke="#FFFFFF" stroke-width="1.6"/>'
        '<path d="M60 18c-6 30-10 62-8 88" fill="none" stroke="#FFFFFF" '
        'stroke-width="1.1"/></pattern></defs>'
        '<rect width="800" height="260" fill="url(#spleaf)"/>'
        '<path d="M0 210 C 160 170, 300 235, 460 195 S 720 150, 800 185" '
        'fill="none" stroke="#FFFFFF" stroke-width="1.4" opacity=".7"/>'
        '<path d="M0 240 C 180 205, 320 260, 500 225 S 730 190, 800 220" '
        'fill="none" stroke="#FFFFFF" stroke-width="1.1" opacity=".5"/>'
        "</svg>"
    )


def risk_ring(level: str, *, size: int = 168) -> str:
    """Circular SVG gauge. The level word and glyph sit in the middle, so
    colour is never the only signal. Pulses only at HIGH, and the CSS disables
    that under prefers-reduced-motion."""
    colour = risk_bg(level)
    label = esc(level)
    glyph = RISK_GLYPH.get(level, "?")
    r = size / 2 - 14
    cx = cy = size / 2
    circumference = 2 * 3.14159265 * r
    # Fraction of the ring drawn, by severity. Presentation only.
    frac = {"HIGH": 1.0, "MODERATE": 0.66, "LOW": 0.33}.get(level, 0.12)
    dash = f"{circumference * frac:.1f} {circumference:.1f}"
    pulse = (f'<circle class="sp-pulse" cx="{cx}" cy="{cy}" r="{r}" fill="none" '
             f'stroke="{colour}" stroke-width="10"/>') if level == "HIGH" else ""
    # Fit the word inside the ring: at ~0.62em per character, the widest
    # label (MODERATE, 8 chars) needs to stay under the inner diameter.
    inner = (r * 2) - 18
    word_size = max(13, min(26, int(inner / (len(level) * 0.62)))) if level else 22
    return (
        f'<div class="sp-ring-wrap"><svg width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}" role="img" '
        f'aria-label="Blight risk level: {label}">'
        f'{pulse}'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="rgba(255,255,255,.10)" '
        f'stroke="rgba(255,255,255,.22)" stroke-width="12"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{colour}" '
        f'stroke-width="12" stroke-linecap="round" stroke-dasharray="{dash}" '
        f'transform="rotate(-90 {cx} {cy})"/>'
        f'<text x="{cx}" y="{cy - 12}" text-anchor="middle" fill="#fff" '
        f'font-size="26" font-weight="700">{glyph}</text>'
        f'<text x="{cx}" y="{cy + 20}" text-anchor="middle" fill="#fff" '
        f'class="sp-ring-label" font-size="{word_size}">{label}</text>'
        f"</svg></div>"
    )


def hero(level: str, pills: list[tuple[str, str]]) -> str:
    """Full-width banner. `pills` is a list of (text, dot-colour) pairs; pass
    an empty colour string for no dot."""
    pill_html = "".join(
        f'<span class="sp-pill">'
        f'{f"<span class=\'sp-pill-dot\' style=\'background:{esc(colour)}\'></span>" if colour else ""}'
        f"{esc(text)}</span>"
        for text, colour in pills
    )
    return (
        f'<div class="sp-hero">{_leaf_pattern()}'
        f'<div class="sp-hero-inner"><div class="sp-hero-left">'
        f'<div class="sp-title">Shamba Pulse</div>'
        f'<div class="sp-sub">Blight risk &amp; spray windows for Juja/Kiambu '
        f'farmers, powered by the JKUAT Conduit station.</div>'
        f'<div class="sp-pills">{pill_html}</div></div>'
        f"{risk_ring(level)}</div></div>"
    )


# --------------------------------------------------------------------------
# 2. Action card
# --------------------------------------------------------------------------

_ICONS = {
    "droplet": ('<svg width="15" height="15" viewBox="0 0 24 24" fill="none" '
                'stroke="#2E7D4F" stroke-width="2" stroke-linecap="round" '
                'stroke-linejoin="round"><path d="M12 2.7S5.5 10 5.5 14.5a6.5 '
                '6.5 0 0 0 13 0C18.5 10 12 2.7 12 2.7z"/></svg>'),
    "thermometer": ('<svg width="15" height="15" viewBox="0 0 24 24" fill="none" '
                    'stroke="#B3261E" stroke-width="2" stroke-linecap="round">'
                    '<path d="M14 14.8V4a2 2 0 1 0-4 0v10.8a4 4 0 1 0 4 0z"/></svg>'),
    "calendar": ('<svg width="15" height="15" viewBox="0 0 24 24" fill="none" '
                 'stroke="#7A4E2D" stroke-width="2" stroke-linecap="round">'
                 '<rect x="3" y="5" width="18" height="16" rx="2"/>'
                 '<path d="M3 10h18M8 3v4M16 3v4"/></svg>'),
    "wind": ('<svg width="15" height="15" viewBox="0 0 24 24" fill="none" '
             'stroke="#5F6B73" stroke-width="2" stroke-linecap="round">'
             '<path d="M3 8h11a3 3 0 1 0-3-3M3 13h15a3 3 0 1 1-3 3"/></svg>'),
    "rain": ('<svg width="15" height="15" viewBox="0 0 24 24" fill="none" '
             'stroke="#22405F" stroke-width="2" stroke-linecap="round">'
             '<path d="M6 14a4 4 0 0 1 .8-7.9 5.5 5.5 0 0 1 10.6 1.5A3.5 3.5 0 '
             '0 1 17.5 14z"/><path d="M8 18l-1 2M12 18l-1 2M16 18l-1 2"/></svg>'),
    "clock": ('<svg width="15" height="15" viewBox="0 0 24 24" fill="none" '
              'stroke="#2E7D4F" stroke-width="2" stroke-linecap="round">'
              '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>'),
}


def icon(name: str) -> str:
    return _ICONS.get(name, _ICONS["droplet"])


def _reason_icon(text: str) -> str:
    """Pick an icon from what the reason actually talks about."""
    low = text.lower()
    if "humid" in low:
        return "droplet"
    if "temperature" in low or "degc" in low or "°c" in low:
        return "thermometer"
    if "hutton day" in low or "days in a row" in low or "consecutive" in low:
        return "calendar"
    if "rain" in low:
        return "rain"
    return "clock"


def action_card(headline: str, when: str | None, reasons: list[str]) -> str:
    """One imperative sentence, then up to three reasons as icon rows."""
    when_html = (f' <span class="sp-action-when">Best time to spray: '
                 f"{esc(when)}.</span>") if when else ""
    rows = "".join(
        f'<div class="sp-why-row"><span class="sp-why-ico">'
        f"{icon(_reason_icon(r))}</span><span>{esc(r)}</span></div>"
        for r in reasons[:3]
    )
    why = f'<div class="sp-why-h">Why</div>{rows}' if rows else ""
    return (f'<div class="sp-card sp-action"><div class="sp-action-head">'
            f"{esc(headline)}{when_html}</div>{why}</div>")


# --------------------------------------------------------------------------
# 3. Metric tiles
# --------------------------------------------------------------------------

def tile(icon_name: str, label: str, value: str, unit: str = "",
         note: str = "") -> str:
    unit_html = f'<span class="sp-tile-unit">{esc(unit)}</span>' if unit else ""
    note_html = f'<div class="sp-tile-note">{esc(note)}</div>' if note else ""
    return (f'<div class="sp-tile"><div class="sp-tile-top">{icon(icon_name)}'
            f'<div class="sp-tile-label">{esc(label)}</div></div>'
            f'<div class="sp-tile-val">{esc(value)}{unit_html}</div>'
            f"{note_html}</div>")


def tiles(items: list[tuple[str, str, str, str, str]]) -> str:
    """items = [(icon, label, value, unit, note), ...]"""
    return f'<div class="sp-tiles">{"".join(tile(*i) for i in items)}</div>'


# --------------------------------------------------------------------------
# 4. Seven-day risk strip
# --------------------------------------------------------------------------

def week_strip(days: list[dict]) -> str:
    """days = [{name, date, level, humid_hours}] - already computed by app.py.

    An empty list renders placeholder tiles rather than an error, because a
    missing forecast is a normal state, not a failure of the page.
    """
    if not days:
        cells = "".join(
            '<div class="sp-day"><div class="sp-day-name">&mdash;</div>'
            '<div class="sp-day-date">&nbsp;</div>'
            '<div class="sp-day-bar" style="background:#E4DCCD"></div>'
            '<div class="sp-day-val">&mdash;</div>'
            '<div class="sp-day-sub">no forecast</div></div>'
            for _ in range(7)
        )
        return f'<div class="sp-week">{cells}</div>'

    cells = ""
    for d in days[:7]:
        level = d.get("level", "UNKNOWN")
        cells += (
            f'<div class="sp-day"><div class="sp-day-name">{esc(d.get("name"))}</div>'
            f'<div class="sp-day-date">{esc(d.get("date"))}</div>'
            f'<div class="sp-day-bar" style="background:{risk_bg(level)}"></div>'
            f'<div class="sp-day-val">{esc(d.get("humid_hours"))}h</div>'
            f'<div class="sp-day-sub">humid</div></div>'
        )
    return f'<div class="sp-week">{cells}</div>'


# --------------------------------------------------------------------------
# 5. Spray daylight timeline
# --------------------------------------------------------------------------

def spray_timeline(rows: list[dict], day_start: str, day_end: str) -> str:
    """rows = [{day, segments:[{left_pct, width_pct, quality, title}]}].

    Positions are computed by app.py from the real windows; this only draws.
    """
    if not rows:
        return ""
    key = (
        '<div class="sp-tl-key">'
        '<span><i style="background:#2E7D4F"></i>Good window</span>'
        '<span><i style="background:#8FBF9F"></i>Fair window</span>'
        '<span><i style="background:#F2EDE3;border:1px solid #E4DCCD"></i>'
        "Ruled out</span></div>"
    )
    body = ""
    for row in rows:
        segs = "".join(
            f'<div class="sp-tl-seg '
            f'{"sp-tl-good" if s.get("quality") == "GOOD" else "sp-tl-fair"}" '
            f'style="left:{float(s.get("left_pct", 0)):.2f}%;'
            f'width:{float(s.get("width_pct", 0)):.2f}%" '
            f'title="{esc(s.get("title"))}"></div>'
            for s in row.get("segments", [])
        )
        body += (f'<div class="sp-tl-row"><div class="sp-tl-day">'
                 f'{esc(row.get("day"))}</div>'
                 f'<div class="sp-tl-track">{segs}</div></div>')
    axis = (f'<div class="sp-tl-axis"><span>{esc(day_start)}</span>'
            f"<span>midday</span><span>{esc(day_end)}</span></div>")
    return f"{key}{body}{axis}"


# --------------------------------------------------------------------------
# 6. Phone mockup
# --------------------------------------------------------------------------

def phone(message: str, *, carrier: str = "Safaricom", clock: str = "08:30",
          header: str = "Shamba Pulse") -> str:
    return (
        f'<div class="sp-phone"><div class="sp-phone-screen">'
        f'<div class="sp-notch"></div>'
        f'<div class="sp-status"><span>{esc(carrier)}</span>'
        f"<span>{esc(clock)}</span></div>"
        f'<div class="sp-msg-head">{esc(header)}</div>'
        f'<div class="sp-msg-body"><div class="sp-bubble">{esc(message)}</div>'
        f'<div class="sp-bubble-time">{esc(clock)}</div></div>'
        f"</div></div>"
    )


def badges(items: list[tuple[str, str]]) -> str:
    """items = [(text, kind)] where kind is warn | mute | live."""
    return ('<div class="sp-badges">' + "".join(
        f'<span class="sp-badge sp-badge-{esc(kind)}">{esc(text)}</span>'
        for text, kind in items) + "</div>")


# --------------------------------------------------------------------------
# 7 / 8. Bands, findings, station chips, footer
# --------------------------------------------------------------------------

def stat_band(items: list[tuple[str, str]]) -> str:
    """items = [(big number, label)]"""
    return ('<div class="sp-band">' + "".join(
        f'<div><div class="sp-band-n">{esc(n)}</div>'
        f'<div class="sp-band-l">{esc(label)}</div></div>'
        for n, label in items) + "</div>")


def finding_cards(items: list[tuple[str, str, str]]) -> str:
    """items = [(icon, title, body)]"""
    return ('<div class="sp-finds">' + "".join(
        f'<div class="sp-find"><div class="sp-find-h">{icon(ic)}'
        f'<span>{esc(title)}</span></div>'
        f'<div class="sp-find-b">{esc(body)}</div></div>'
        for ic, title, body in items) + "</div>")


def chips(items: list[str]) -> str:
    return ('<div class="sp-chiprow">' + "".join(
        f'<span class="sp-chip">{esc(c)}</span>' for c in items) + "</div>")


def section(title: str, sub: str = "") -> str:
    sub_html = f'<div class="sp-h-sub">{esc(sub)}</div>' if sub else ""
    return f'<div class="sp-h">{esc(title)}</div>{sub_html}'


def freshness(kind: str, text_html: str, glyph: str) -> str:
    """`text_html` is pre-built markup from app.py; callers escape their own
    dynamic parts before passing it in."""
    return (f'<div class="sp-fresh sp-fresh-{esc(kind)}"><div>{glyph}</div>'
            f"<div>{text_html}</div></div>")


def footer(repo_url: str) -> str:
    url = esc(repo_url)
    return (
        f'<div class="sp-footer"><div class="sp-foot-grid">'
        f"<div><h4>About</h4><p><b>Farmers get the SMS</b> &mdash; they do not "
        f"open a dashboard during planting season. <b>This dashboard is for "
        f"extension officers, agrovets and cooperative field teams</b> who "
        f"advise many farmers and need the reasoning behind every alert.</p></div>"
        f"<div><h4>Data</h4><ul>"
        f"<li>JKUAT Conduit station &mdash; JHUB Africa / JKUAT</li>"
        f"<li>Open-Meteo &mdash; 7-day forecast</li>"
        f"<li>ERA5 reanalysis &mdash; rainfall cross-check</li>"
        f"<li>Kenya Meteorological Department &mdash; Oct 2025 advisory</li>"
        f"</ul></div>"
        f'<div><h4>Project</h4><p><a href="{url}">Source on GitHub</a><br>'
        f'<a href="{url}#readme">README</a><br>'
        f'<a href="{url}/blob/main/docs/DATA_QUALITY.md">Data quality report</a>'
        f"</p></div></div>"
        f'<div class="sp-foot-rule">Built for Hack The Weather 2026 &middot; '
        f"JHUB Africa / JKUAT. Risk follows the Hutton criteria for potato and "
        f"tomato late blight. SMS runs in dry-run: nothing is actually sent."
        f"</div></div>"
    )
