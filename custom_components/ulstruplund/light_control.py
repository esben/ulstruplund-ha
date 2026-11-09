"""Light control logic driven by buttons, motion sensors and timeouts.

Kept free of Home Assistant imports so it can be unit tested directly.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


@dataclass(frozen=True)
class TurnOn:
    brightness: int


@dataclass(frozen=True)
class TurnOff:
    pass


type Command = TurnOn | TurnOff


class Mode(StrEnum):
    """Why the light is on (exposed as the control sensor's state)."""

    OFF = "off"
    MANUAL = "manual"
    MOTION = "motion"


class LightController:
    """State machine for one light.

    A light turned on manually (by button or externally, e.g. from the UI or
    a wall switch) stays on until turned off, or until manual_timeout.

    A light turned on by motion is turned off when no motion sensor detects
    motion anymore, or at the latest after motion_timeout. Motion never turns
    on a light that is manually on, or which is waiting for a timeout.
    """

    def __init__(
        self,
        *,
        on_brightness: int = 255,
        motion_brightness: int = 50,
        manual_timeout: timedelta = timedelta(hours=3),
        motion_timeout: timedelta = timedelta(minutes=30),
    ) -> None:
        self.on_brightness = on_brightness
        self.motion_brightness = motion_brightness
        self.manual_timeout = manual_timeout
        self.motion_timeout = motion_timeout
        self.light_on: bool | None = None
        self.manual = False
        self.timeout: datetime | None = None
        self._motion: dict[str, bool] = {}

    @property
    def mode(self) -> Mode:
        if not self.light_on:
            return Mode.OFF
        if self.manual:
            return Mode.MANUAL
        return Mode.MOTION

    @property
    def motion_detected(self) -> bool:
        return any(self._motion.values())

    def light_changed(self, on: bool | None, *, external: bool, now: datetime) -> None:
        """Track the light's actual state.

        External changes (not caused by this controller) are treated as manual
        operation.
        """
        self.light_on = on
        if not external or on is None:
            return
        if on:
            self.manual = True
            self.timeout = now + self.manual_timeout
        else:
            self.manual = False
            self.timeout = None

    def button(self, event: str, now: datetime) -> Command | None:
        if event == "on" and self.light_on is False:
            self.manual = True
            self.timeout = now + self.manual_timeout
            return TurnOn(self.on_brightness)
        if event == "off" and self.light_on is True:
            self.manual = False
            self.timeout = None
            return TurnOff()
        return None

    def motion(self, sensor: str, detected: bool, now: datetime) -> Command | None:
        old = self._motion.get(sensor, False)
        self._motion[sensor] = detected
        if detected == old:
            return None
        if detected:
            if self.light_on is False and not self.manual and self.timeout is None:
                self.timeout = now + self.motion_timeout
                return TurnOn(self.motion_brightness)
        elif self.light_on is True and not self.manual and not self.motion_detected:
            self.timeout = None
            return TurnOff()
        return None

    def check_timeout(self, now: datetime) -> Command | None:
        if self.timeout is None or now < self.timeout:
            return None
        self.timeout = None
        if self.light_on is True:
            self.manual = False
            return TurnOff()
        return None


def is_dark(
    now: datetime,
    sunrise: datetime | None,
    sunset: datetime | None,
    *,
    sunrise_offset: timedelta,
    sunset_offset: timedelta,
) -> bool:
    """Return whether now is before (sunrise + offset) or after (sunset + offset).

    sunrise and sunset are for the current day. If either is missing (polar
    day/night), it is considered dark.
    """
    if sunrise is None or sunset is None:
        return True
    return now < sunrise + sunrise_offset or now >= sunset + sunset_offset
