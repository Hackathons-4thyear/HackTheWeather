"""Tests for the HTML components in ui/components.py.

These components inject dynamic text straight into markup that Streamlit
renders with unsafe_allow_html=True. That text ultimately comes from station
data, message templates and engine reasons, so none of it is assumed safe: the
first thing every test checks is that a hostile string comes back escaped.

They also assert the components stay *pure* - no Streamlit, no engine imports,
no numbers computed here - so presentation can be changed without any risk to
the published figures.
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ui import components as C  # noqa: E402

XSS = '<script>alert("x")</script>'
QUOTES = 'He said "hi" & <b>bye</b>'


class _Balanced(HTMLParser):
    """Minimal well-formedness check: every non-void tag closes, in order."""

    VOID = {"br", "hr", "img", "input", "meta", "link", "path", "circle",
            "rect", "use", "stop", "text"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack:
            self.errors.append(f"</{tag}> with nothing open")
        elif self.stack[-1] != tag:
            self.errors.append(f"</{tag}> closed while <{self.stack[-1]}> open")
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
        else:
            self.stack.pop()


def assert_well_formed(markup: str) -> None:
    p = _Balanced()
    p.feed(markup)
    p.close()
    assert not p.errors, f"malformed HTML: {p.errors[:3]}"
    assert not p.stack, f"unclosed tags: {p.stack[:3]}"


def all_components() -> dict[str, str]:
    """Every component rendered with hostile input."""
    return {
        "hero": C.hero("HIGH", [(XSS, "#fff"), (QUOTES, "")]),
        "risk_ring": C.risk_ring(XSS),
        "action_card": C.action_card(XSS, XSS, [XSS, QUOTES, XSS, XSS]),
        "tile": C.tile("droplet", XSS, XSS, XSS, XSS),
        "tiles": C.tiles([("rain", XSS, XSS, XSS, XSS)] * 4),
        "week_strip": C.week_strip(
            [{"name": XSS, "date": QUOTES, "level": "LOW", "humid_hours": XSS}]),
        "week_empty": C.week_strip([]),
        "spray_timeline": C.spray_timeline(
            [{"day": XSS, "segments": [
                {"left_pct": 10, "width_pct": 20, "quality": "GOOD",
                 "title": XSS}]}], XSS, QUOTES),
        "phone": C.phone(XSS, carrier=XSS, clock=XSS, header=XSS),
        "badges": C.badges([(XSS, "warn"), (QUOTES, "mute")]),
        "stat_band": C.stat_band([(XSS, QUOTES)] * 4),
        "finding_cards": C.finding_cards([("rain", XSS, QUOTES)] * 4),
        "chips": C.chips([XSS, QUOTES]),
        "section": C.section(XSS, QUOTES),
        "footer": C.footer("https://example.test/repo"),
    }


# --------------------------------------------------------------------------
# Escaping - the reason these tests exist
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(all_components()))
def test_no_component_emits_unescaped_script(name):
    markup = all_components()[name]
    assert "<script>" not in markup
    assert "</script>" not in markup
    assert 'alert("x")' not in markup


@pytest.mark.parametrize("name", sorted(all_components()))
def test_components_escape_ampersands_and_quotes(name):
    """A raw `"` inside an attribute would let text escape its attribute."""
    markup = all_components()[name]
    for attr in re.findall(r'(?:title|style|aria-label)="([^"]*)"', markup):
        assert "<" not in attr, f"{name}: unescaped < inside an attribute"


def test_esc_handles_none_and_numbers():
    assert C.esc(None) == ""
    assert C.esc(42) == "42"
    assert C.esc(3.5) == "3.5"
    assert C.esc("<b>") == "&lt;b&gt;"
    assert C.esc('"') == "&quot;"


# --------------------------------------------------------------------------
# Well-formedness
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(all_components()))
def test_components_render_balanced_html(name):
    assert_well_formed(all_components()[name])


@pytest.mark.parametrize("name", sorted(all_components()))
def test_components_return_non_empty_markup(name):
    markup = all_components()[name]
    assert markup.strip().startswith("<")
    assert len(markup) > 40


# --------------------------------------------------------------------------
# Risk palette and accessibility
# --------------------------------------------------------------------------

def _luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    ch = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    ch = [(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
          for c in ch]
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]


def contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("level", ["HIGH", "MODERATE", "LOW", "UNKNOWN"])
def test_every_risk_level_pairs_with_readable_text(level):
    """WCAG AA. The amber MODERATE only reaches 2.17:1 against white, which is
    why it is paired with ink instead."""
    ratio = contrast(C.risk_bg(level), C.risk_fg(level))
    assert ratio >= 4.5, f"{level}: {ratio:.2f}:1 is below AA"


def test_unknown_level_falls_back_rather_than_raising():
    assert C.risk_bg("NOT_A_LEVEL") == C.RISK_BG["UNKNOWN"]
    assert C.risk_fg("NOT_A_LEVEL") == C.RISK_FG["UNKNOWN"]


@pytest.mark.parametrize("level", ["HIGH", "MODERATE", "LOW", "UNKNOWN"])
def test_risk_ring_states_the_level_in_words_not_only_colour(level):
    """Colour must never be the sole signal."""
    markup = C.risk_ring(level)
    assert level in markup
    assert 'role="img"' in markup and "aria-label" in markup


def test_only_high_risk_animates():
    """A pulsing ring is an alarm; it should not run at LOW."""
    assert "sp-pulse" in C.risk_ring("HIGH")
    for level in ("MODERATE", "LOW", "UNKNOWN"):
        assert "sp-pulse" not in C.risk_ring(level)


def test_reduced_motion_is_respected_in_the_stylesheet():
    css = C.CSS_PATH.read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in css


# --------------------------------------------------------------------------
# Graceful degradation
# --------------------------------------------------------------------------

def test_empty_week_strip_shows_placeholders_not_an_error():
    markup = C.week_strip([])
    assert "no forecast" in markup
    assert markup.count("sp-day") >= 7


def test_empty_spray_timeline_returns_empty_string():
    assert C.spray_timeline([], "06:30", "18:30") == ""


def test_action_card_without_a_window_omits_the_spray_line():
    markup = C.action_card("Check your crop.", None, ["a reason"])
    assert "Best time to spray" not in markup
    assert "Check your crop." in markup


def test_action_card_shows_at_most_three_reasons():
    markup = C.action_card("x", None, [f"reason {i}" for i in range(8)])
    assert markup.count("sp-why-row") == 3


# --------------------------------------------------------------------------
# Purity - presentation must not reach into logic
# --------------------------------------------------------------------------

def test_components_module_imports_no_streamlit_or_engine():
    src = Path(C.__file__).read_text(encoding="utf-8")
    for forbidden in ("import streamlit", "from services", "import config",
                      "import pandas"):
        assert forbidden not in src, f"components.py must not {forbidden}"


def test_stylesheet_loads_and_is_wrapped_in_a_style_tag():
    css = C.load_css()
    assert css.startswith("<style>") and css.endswith("</style>")
    assert "--forest" in css and "--leaf" in css
