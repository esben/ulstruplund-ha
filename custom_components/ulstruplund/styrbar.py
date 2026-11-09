"""IKEA STYRBAR (E2001/E2002) remote event parsing.

Kept free of Home Assistant imports so it can be unit tested directly.
"""

from datetime import datetime, timedelta


class StyrbarParser:
    """Turn ZHA commands from a STYRBAR remote into button events.

    Returns one of "on", "off", "left", "right", "left_hold", "left_release",
    "right_hold", "right_release", "brightness_up_hold",
    "brightness_up_release", "brightness_down_hold", "brightness_down_release",
    or None for commands that are not a button event on their own.

    A short "release" (not following a hold) is followed by a spurious "on",
    which is swallowed.
    """

    def __init__(self) -> None:
        self._state: str | None = None
        self._state_timeout: datetime | None = None

    def parse(self, command: str, args: list, now: datetime) -> str | None:
        if self._state_timeout and now > self._state_timeout:
            self._state = None
            self._state_timeout = None

        if command == "on":
            if self._state is None:
                return "on"
            if self._state == "release":
                self._state = None
                self._state_timeout = None
                return None
        if command == "off":
            return "off"
        if command == "move_with_on_off":
            return "brightness_up_hold"
        if command == "stop_with_on_off":
            return "brightness_up_release"
        if command == "move":
            return "brightness_down_hold"
        if command == "stop":
            return "brightness_down_release"
        if command == "press" and args == [257, 13, 0]:
            return "left"
        if command == "press" and args == [256, 13, 0]:
            return "right"
        if command == "hold" and args == [3329, 0]:
            self._set_state("left_hold", now, timedelta(seconds=30))
            return "left_hold"
        if command == "hold" and args == [3328, 0]:
            self._set_state("right_hold", now, timedelta(seconds=30))
            return "right_hold"
        if command == "release":
            if self._state is None:
                self._set_state("release", now, timedelta(milliseconds=600))
                return None
            if self._state == "left_hold":
                self._state = None
                return "left_release"
            if self._state == "right_hold":
                self._state = None
                return "right_release"
        return None

    def _set_state(self, state: str, now: datetime, timeout: timedelta) -> None:
        self._state = state
        self._state_timeout = now + timeout
