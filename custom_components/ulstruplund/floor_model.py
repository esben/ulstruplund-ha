"""Floor heating model used to forecast the floor temperature.

Kept free of Home Assistant imports so it can be unit tested directly.

The floor is modelled as: the valve signal is delayed by dead_time and
smoothed by a first-order lag, and the floor temperature rises with
heating_rate times that signal, minus a constant cooling_rate:

    dT/dt = heating_rate * x(t) - cooling_rate
    x = lag(valve(t - dead_time))
"""

from bisect import bisect_right
from dataclasses import dataclass, field, replace
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


class History[T]:
    """A value changing over time, e.g. valve open/closed or a temperature."""

    def __init__(self, changes: list[tuple[datetime, T]] | None = None) -> None:
        self._times: list[datetime] = []
        self._states: list[T] = []
        for when, value in changes or []:
            self.add(when, value)

    def add(self, when: datetime, value: T) -> None:
        if self._states and self._states[-1] == value:
            return
        if self._times and when < self._times[-1]:
            when = self._times[-1]
        self._times.append(when)
        self._states.append(value)

    def state_at(self, when: datetime) -> T | None:
        """Value at a time; before the first entry, assume the first value."""
        if not self._states:
            return None
        i = bisect_right(self._times, when)
        return self._states[max(i - 1, 0)]

    @property
    def first_change(self) -> datetime | None:
        return self._times[0] if self._times else None

    def values_between(self, start: datetime, end: datetime) -> list[T]:
        """Values in effect at some point in [start, end]."""
        i = max(bisect_right(self._times, start) - 1, 0)
        j = bisect_right(self._times, end)
        return self._states[i:j]

    @property
    def last_change(self) -> datetime | None:
        return self._times[-1] if self._times else None

    def prune(self, before: datetime) -> None:
        """Forget changes before a time, keeping the state at that time."""
        i = bisect_right(self._times, before) - 1
        if i > 0:
            del self._times[:i]
            del self._states[:i]

    def as_list(self) -> list[tuple[datetime, T]]:
        return list(zip(self._times, self._states))


type ValveHistory = History[bool]


def lagged_signal(
    model: FloorModel,
    history: History[bool],
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
    history: History[bool],
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


@dataclass
class RateEstimator:
    """Online estimate of a model's heating and cooling rates.

    Every sample compares the floor temperature change over a window with the
    heat that reached the floor in it according to the model (valve signal
    delayed and lagged):

        delta_temp = heating_rate * heat_hours - cooling_rate * hours

    and updates the rates with a Kalman filter. The rates drift slowly (e.g.
    with the weather), modelled as a random walk. Estimates are kept within
    0.5-2 times the initial (configured) rates.
    """

    initial: FloorModel
    heating_rate: float = field(init=False)
    cooling_rate: float = field(init=False)
    # Covariance of (heating_rate, cooling_rate)
    p: list[list[float]] = field(init=False)

    # Window and interval between samples
    WINDOW = timedelta(hours=2)
    INTERVAL = timedelta(minutes=30)
    # Initial uncertainty, drift per sample, and sample noise (std, °C/h, °C)
    INITIAL_STD = 0.1
    DRIFT_STD = 0.005
    NOISE_STD = 0.1

    def __post_init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.heating_rate = self.initial.heating_rate
        self.cooling_rate = self.initial.cooling_rate
        self.p = [[self.INITIAL_STD**2, 0.0], [0.0, self.INITIAL_STD**2]]

    @property
    def model(self) -> FloorModel:
        return replace(
            self.initial, heating_rate=self.heating_rate, cooling_rate=self.cooling_rate
        )

    def sample(
        self,
        valves: History[bool],
        temps: History[float | None],
        now: datetime,
    ) -> bool:
        """Update from the window ending now; return whether there was enough data."""
        start = now - self.WINDOW
        first_temp, first_valve = temps.first_change, valves.first_change
        if first_temp is None or first_temp > start:
            return False
        if first_valve is None or first_valve > start - self.initial.memory:
            return False
        window = temps.values_between(start, now)
        if not window or any(t is None for t in window):
            return False
        step_h = STEP / timedelta(hours=1)
        heat_hours = sum(lagged_signal(self.initial, valves, start, now)) * step_h
        delta = temps.state_at(now) - temps.state_at(start)  # type: ignore[operator]
        self.update(delta, heat_hours, self.WINDOW / timedelta(hours=1))
        return True

    def update(self, delta_temp: float, heat_hours: float, hours: float) -> None:
        """Kalman filter update with delta_temp = G * heat_hours - L * hours."""
        p = self.p
        q = self.DRIFT_STD**2
        p = [[p[0][0] + q, p[0][1]], [p[1][0], p[1][1] + q]]
        h = (heat_hours, -hours)
        ph = (p[0][0] * h[0] + p[0][1] * h[1], p[1][0] * h[0] + p[1][1] * h[1])
        s = h[0] * ph[0] + h[1] * ph[1] + self.NOISE_STD**2
        k = (ph[0] / s, ph[1] / s)
        error = delta_temp - (self.heating_rate * heat_hours - self.cooling_rate * hours)
        self.heating_rate = _clamp(
            self.heating_rate + k[0] * error, self.initial.heating_rate
        )
        self.cooling_rate = _clamp(
            self.cooling_rate + k[1] * error, self.initial.cooling_rate
        )
        self.p = [
            [p[0][0] - k[0] * ph[0], p[0][1] - k[0] * ph[1]],
            [p[1][0] - k[1] * ph[0], p[1][1] - k[1] * ph[1]],
        ]

    def as_dict(self) -> dict:
        return {
            "initial": [self.initial.heating_rate, self.initial.cooling_rate],
            "rates": [self.heating_rate, self.cooling_rate],
            "p": self.p,
        }

    def restore(self, data: dict) -> None:
        """Restore saved state, unless the configured rates have changed since."""
        if data.get("initial") != [self.initial.heating_rate, self.initial.cooling_rate]:
            return
        self.heating_rate, self.cooling_rate = data["rates"]
        self.p = data["p"]


def _clamp(value: float, initial: float) -> float:
    return min(max(value, initial / 2), initial * 2)
