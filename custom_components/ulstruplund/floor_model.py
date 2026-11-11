"""Floor heating model used to forecast the floor temperature.

Kept free of Home Assistant imports so it can be unit tested directly.

The floor is modelled as: the valve signal is delayed by dead_time and
smoothed by a first-order lag, and the floor temperature rises with
heating_rate times that signal, minus a constant cooling_rate:

    dT/dt = heating_rate * x(t) - cooling_rate
    x = lag(valve(t - dead_time))
"""

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
import math

STEP = timedelta(minutes=1)


@dataclass(frozen=True)
class FloorModel:
    dead_time: timedelta
    lag: timedelta
    heating_rate: float  # °C/h with the valve fully open
    cooling_rate: float  # °C/h with the valve closed

    @property
    def horizon(self) -> timedelta:
        """How far ahead a valve change still affects the temperature noticeably."""
        return self.dead_time + 3 * self.lag

    @property
    def memory(self) -> timedelta:
        """How far back the valve history matters."""
        return self.dead_time + 6 * self.lag


class ValveHistory:
    """Times at which the valve was opened or closed."""

    def __init__(self, changes: list[tuple[datetime, bool]] | None = None) -> None:
        self._times: list[datetime] = []
        self._states: list[bool] = []
        for when, is_open in changes or []:
            self.add(when, is_open)

    def add(self, when: datetime, is_open: bool) -> None:
        if self._states and self._states[-1] == is_open:
            return
        if self._times and when < self._times[-1]:
            when = self._times[-1]
        self._times.append(when)
        self._states.append(is_open)

    def state_at(self, when: datetime) -> bool | None:
        """Valve state at a time; before the first entry, assume the first state."""
        if not self._states:
            return None
        i = bisect_right(self._times, when)
        return self._states[max(i - 1, 0)]

    @property
    def last_change(self) -> datetime | None:
        return self._times[-1] if self._times else None

    def prune(self, before: datetime) -> None:
        """Forget changes before a time, keeping the state at that time."""
        i = bisect_right(self._times, before) - 1
        if i > 0:
            del self._times[:i]
            del self._states[:i]

    def as_list(self) -> list[tuple[datetime, bool]]:
        return list(zip(self._times, self._states))


def lagged_signal(
    model: FloorModel,
    history: ValveHistory,
    start: datetime,
    end: datetime,
    assume_open: bool | None = None,
    now: datetime | None = None,
) -> list[float]:
    """Return the delayed and smoothed valve signal x for each STEP in [start, end).

    Valve states after now are taken to be assume_open.
    """
    alpha = 1 - math.exp(-STEP / model.lag) if model.lag > timedelta(0) else 1.0

    def valve(when: datetime) -> float:
        if now is not None and assume_open is not None and when >= now:
            return 1.0 if assume_open else 0.0
        return 1.0 if history.state_at(when) else 0.0

    # Let the lag settle before start
    t = start - model.dead_time - 6 * model.lag
    x = valve(t)
    while t < start - model.dead_time:
        x += alpha * (valve(t) - x)
        t += STEP
    out = []
    while t < end - model.dead_time:
        x += alpha * (valve(t) - x)
        out.append(x)
        t += STEP
    return out


def forecast(
    model: FloorModel,
    history: ValveHistory,
    now: datetime,
    floor_temp: float,
    assume_open: bool,
) -> tuple[float, float]:
    """Return (min, max) of the floor temperature from now until model.horizon.

    Assumes the valve is open/closed from now on, and accounts for the heat
    already on its way due to earlier valve states.
    """
    signal = lagged_signal(model, history, now, now + model.horizon, assume_open, now)
    step_h = STEP / timedelta(hours=1)
    temp = low = high = floor_temp
    for x in signal:
        temp += (model.heating_rate * x - model.cooling_rate) * step_h
        low = min(low, temp)
        high = max(high, temp)
    return low, high
