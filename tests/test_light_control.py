"""Tests for the pure light control logic."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.ulstruplund.light_control import (
    LightController,
    Mode,
    TurnOff,
    TurnOn,
    is_dark,
)

NOW = datetime(2026, 11, 9, 20, 0, tzinfo=UTC)


def controller(light_on: bool = False) -> LightController:
    ctrl = LightController()
    ctrl.light_changed(light_on, external=False, now=NOW)
    return ctrl


def test_button_on_off():
    ctrl = controller()
    assert ctrl.button("on", NOW) == TurnOn(255)
    assert ctrl.manual
    assert ctrl.timeout == NOW + timedelta(hours=3)

    ctrl.light_changed(True, external=False, now=NOW)
    assert ctrl.mode == Mode.MANUAL
    assert ctrl.button("on", NOW) is None
    assert ctrl.button("off", NOW) == TurnOff()
    assert not ctrl.manual
    assert ctrl.timeout is None


def test_button_ignored_when_light_unavailable():
    ctrl = LightController()
    assert ctrl.button("on", NOW) is None


def test_motion_on_off():
    ctrl = controller()
    assert ctrl.motion("pir", True, NOW) == TurnOn(50)
    assert ctrl.timeout == NOW + timedelta(minutes=30)

    ctrl.light_changed(True, external=False, now=NOW)
    assert ctrl.mode == Mode.MOTION
    assert ctrl.motion("pir", True, NOW) is None
    assert ctrl.motion("pir", False, NOW) == TurnOff()
    assert ctrl.timeout is None


def test_motion_waits_for_all_sensors():
    ctrl = controller()
    ctrl.motion("pir1", True, NOW)
    ctrl.light_changed(True, external=False, now=NOW)
    assert ctrl.motion("pir2", True, NOW) is None
    assert ctrl.motion("pir1", False, NOW) is None
    assert ctrl.motion("pir2", False, NOW) == TurnOff()


def test_motion_does_not_touch_manual_light():
    ctrl = controller()
    ctrl.button("on", NOW)
    ctrl.light_changed(True, external=False, now=NOW)
    assert ctrl.motion("pir", True, NOW) is None
    assert ctrl.motion("pir", False, NOW) is None


def test_external_on_is_manual():
    ctrl = controller()
    ctrl.light_changed(True, external=True, now=NOW)
    assert ctrl.mode == Mode.MANUAL
    assert ctrl.timeout == NOW + timedelta(hours=3)
    assert ctrl.motion("pir", True, NOW) is None
    assert ctrl.motion("pir", False, NOW) is None

    ctrl.light_changed(False, external=True, now=NOW)
    assert not ctrl.manual
    assert ctrl.timeout is None


def test_timeout_turns_off():
    ctrl = controller()
    ctrl.button("on", NOW)
    ctrl.light_changed(True, external=False, now=NOW)
    assert ctrl.check_timeout(NOW + timedelta(hours=2)) is None
    assert ctrl.check_timeout(NOW + timedelta(hours=3)) == TurnOff()
    assert not ctrl.manual
    assert ctrl.timeout is None


def test_timeout_while_off_just_clears():
    ctrl = controller()
    ctrl.timeout = NOW
    assert ctrl.check_timeout(NOW) is None
    assert ctrl.timeout is None


SUNRISE = datetime(2026, 11, 9, 7, 0, tzinfo=UTC)
SUNSET = datetime(2026, 11, 9, 16, 0, tzinfo=UTC)
OFFSETS = {"sunrise_offset": timedelta(minutes=20), "sunset_offset": timedelta(minutes=-20)}


@pytest.mark.parametrize(
    ("time", "dark"),
    [
        ("05:00", True),
        ("07:19", True),
        ("07:20", False),
        ("12:00", False),
        ("15:39", False),
        ("15:40", True),
        ("23:00", True),
    ],
)
def test_is_dark(time, dark):
    hour, minute = map(int, time.split(":"))
    now = SUNRISE.replace(hour=hour, minute=minute)
    assert is_dark(now, SUNRISE, SUNSET, **OFFSETS) == dark


def test_is_dark_without_sun_events():
    assert is_dark(NOW, None, SUNSET, **OFFSETS)
