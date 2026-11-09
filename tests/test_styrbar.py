"""Tests for STYRBAR remote event parsing."""

from datetime import UTC, datetime, timedelta

from custom_components.ulstruplund.styrbar import StyrbarParser

T0 = datetime(2026, 11, 9, 20, 0, tzinfo=UTC)


def test_simple_buttons():
    parser = StyrbarParser()
    assert parser.parse("on", [], T0) == "on"
    assert parser.parse("off", [], T0) == "off"
    assert parser.parse("press", [257, 13, 0], T0) == "left"
    assert parser.parse("press", [256, 13, 0], T0) == "right"
    assert parser.parse("move_with_on_off", [0, 83], T0) == "brightness_up_hold"
    assert parser.parse("stop", [], T0) == "brightness_down_release"


def test_hold_release():
    parser = StyrbarParser()
    assert parser.parse("hold", [3329, 0], T0) == "left_hold"
    assert parser.parse("release", [], T0 + timedelta(seconds=2)) == "left_release"
    assert parser.parse("hold", [3328, 0], T0) == "right_hold"
    assert parser.parse("release", [], T0 + timedelta(seconds=2)) == "right_release"


def test_on_after_short_release_is_swallowed():
    parser = StyrbarParser()
    assert parser.parse("release", [], T0) is None
    assert parser.parse("on", [], T0 + timedelta(milliseconds=100)) is None
    assert parser.parse("on", [], T0 + timedelta(milliseconds=200)) == "on"


def test_release_state_times_out():
    parser = StyrbarParser()
    assert parser.parse("release", [], T0) is None
    assert parser.parse("on", [], T0 + timedelta(seconds=1)) == "on"
