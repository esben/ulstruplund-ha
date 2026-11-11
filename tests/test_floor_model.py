"""Tests for the floor model forecast and predictive control."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.ulstruplund.floor_control import (
    Forecast,
    Reason,
    ValveCommand,
    decide,
)
from custom_components.ulstruplund.floor_model import (
    FloorModel,
    History,
    History as ValveHistory,
    RateEstimator,
    forecast,
)

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
MODEL = FloorModel(
    dead_time=timedelta(minutes=45),
    lag=timedelta(minutes=30),
    heating_rate=0.55,
    cooling_rate=0.38,
)


def history(*changes: tuple[int, bool]) -> ValveHistory:
    """Valve history from (minutes ago, is_open) pairs."""
    return ValveHistory([(NOW - timedelta(minutes=m), o) for m, o in changes])


def test_valve_history():
    h = history((120, True), (60, False), (30, True))
    assert h.state_at(NOW - timedelta(minutes=200)) is True  # before first: first state
    assert h.state_at(NOW - timedelta(minutes=90)) is True
    assert h.state_at(NOW - timedelta(minutes=45)) is False
    assert h.state_at(NOW) is True
    assert h.last_change == NOW - timedelta(minutes=30)

    h.add(NOW, True)  # no change
    assert h.last_change == NOW - timedelta(minutes=30)

    h.prune(NOW - timedelta(minutes=45))
    assert h.as_list() == [(NOW - timedelta(minutes=60), False), (NOW - timedelta(minutes=30), True)]


def test_forecast_long_closed():
    # Closed for long: keeps cooling if kept closed
    low, high = forecast(MODEL, history((600, False)), NOW, 32.0, assume_open=False)
    assert high == 32.0
    hours = MODEL.horizon / timedelta(hours=1)
    assert low == pytest.approx(32.0 - 0.38 * hours, abs=0.01)

    # Opening now: keeps cooling for the dead time, and until the lagged
    # heating exceeds the cooling (x = 0.38 / 0.55 after 35.2 min):
    # 0.38 * 45/60 + (-0.17 * 35.2 + 0.55 * 30 * 0.691) / 60 = 0.375
    low, high = forecast(MODEL, history((600, False)), NOW, 32.0, assume_open=True)
    assert low == pytest.approx(31.625, abs=0.01)


def test_forecast_long_open():
    # Open for long: heat already on its way makes it rise after closing, for
    # the dead time and until the lagged heating drops below the cooling:
    # 0.17 * 45/60 + (0.55 * 30 * 0.309 - 0.38 * 11.09) / 60 = 0.142
    low, high = forecast(MODEL, history((600, True)), NOW, 32.0, assume_open=False)
    assert high == pytest.approx(32.142, abs=0.01)


def test_decide_with_forecast():
    def run(**kw):
        args = {"valve_open": False, "max_if_closed": 32.0, "min_if_open": 31.95} | kw
        return decide(
            enabled=True,
            setpoint=32.0,
            floor_temp=32.0,
            cold_tolerance=0.1,
            hot_tolerance=0.1,
            forecast=Forecast(**args),
        )

    assert run().command == ValveCommand.KEEP
    assert run(min_if_open=31.9).reason == Reason.FORECAST_BELOW_SETPOINT
    assert run(min_if_open=31.9, can_switch=False).reason == Reason.MIN_SWITCH_INTERVAL
    assert run(valve_open=True, max_if_closed=32.05).command == ValveCommand.KEEP
    assert run(valve_open=True, max_if_closed=32.1).reason == Reason.FORECAST_ABOVE_SETPOINT


def test_forecast_safety_ignores_min_switch_interval():
    decision = decide(
        enabled=True,
        setpoint=32.0,
        floor_temp=None,
        forecast=Forecast(
            valve_open=True, max_if_closed=32.0, min_if_open=32.0, can_switch=False
        ),
    )
    assert decision.reason == Reason.SENSOR_UNAVAILABLE


def simulate(
    model: FloorModel,
    plant: FloorModel,
    days: int = 2,
    estimator: RateEstimator | None = None,
    settle: int = 720,
):
    """Closed loop: plant with 0.1 °C sensor, predictive control every minute."""
    step_h = 1 / 60
    t = NOW
    valves = ValveHistory([(t - timedelta(days=1), False)])
    sensor_history: History[float | None] = History()
    temp, x = 31.5, 0.0
    pipe = [0.0] * int(plant.dead_time / timedelta(minutes=1))
    alpha = 1 - 2.718281828 ** (-1 / (plant.lag / timedelta(minutes=1)))
    temps, switches = [], 0
    for i in range(days * 1440):
        sensor = round(temp, 1)
        sensor_history.add(t, sensor)
        if estimator is not None:
            if i % 30 == 0:
                estimator.sample(valves, sensor_history, t)
            model = estimator.model
        is_open = bool(valves.state_at(t))
        last = valves.last_change or t
        decision = decide(
            enabled=True,
            setpoint=32.0,
            floor_temp=sensor,
            cold_tolerance=0.1,
            hot_tolerance=0.1,
            forecast=Forecast(
                valve_open=is_open,
                max_if_closed=forecast(model, valves, t, sensor, False)[1],
                min_if_open=forecast(model, valves, t, sensor, True)[0],
                can_switch=t - last >= timedelta(minutes=30),
            ),
        )
        if decision.command == ValveCommand.OPEN:
            valves.add(t, True)
            switches += 1
        elif decision.command == ValveCommand.CLOSE:
            valves.add(t, False)
            switches += 1
        valves.prune(t - model.memory - RateEstimator.WINDOW)
        pipe.append(1.0 if valves.state_at(t) else 0.0)
        x += alpha * (pipe.pop(0) - x)
        temp += (plant.heating_rate * x - plant.cooling_rate) * step_h
        if i >= settle:
            temps.append(temp)
        t += timedelta(minutes=1)
    return min(temps), max(temps), switches / days, sum(abs(x - 32) for x in temps) / len(temps)


def test_closed_loop_simulation():
    low, high, switches_per_day, _ = simulate(MODEL, MODEL)
    assert 31.8 < low and high < 32.25
    assert switches_per_day < 30


def test_closed_loop_simulation_with_wrong_model():
    plant = FloorModel(
        dead_time=timedelta(minutes=60),
        lag=timedelta(minutes=40),
        heating_rate=0.6,
        cooling_rate=0.3,
    )
    low, high, _, _ = simulate(MODEL, plant)
    assert 31.7 < low and high < 32.35


def test_estimator_learns_rates():
    plant = FloorModel(
        dead_time=MODEL.dead_time, lag=MODEL.lag, heating_rate=0.65, cooling_rate=0.48
    )
    estimator = RateEstimator(MODEL)
    simulate(MODEL, plant, days=3, estimator=estimator)
    assert estimator.heating_rate == pytest.approx(0.65, rel=0.1)
    assert estimator.cooling_rate == pytest.approx(0.48, rel=0.1)


def test_estimator_improves_control():
    # With these rates, the configured model keeps the floor too warm
    plant = FloorModel(
        dead_time=MODEL.dead_time, lag=MODEL.lag, heating_rate=0.8, cooling_rate=0.25
    )
    *_, fixed_error = simulate(MODEL, plant, days=4, settle=2 * 1440)
    *_, error = simulate(
        MODEL, plant, days=4, settle=2 * 1440, estimator=RateEstimator(MODEL)
    )
    assert error < 0.7 * fixed_error


def test_estimator_needs_history():
    estimator = RateEstimator(MODEL)
    valves = ValveHistory([(NOW - timedelta(hours=1), True)])
    temps: History[float | None] = History([(NOW - timedelta(hours=5), 32.0)])
    assert not estimator.sample(valves, temps, NOW)

    valves = ValveHistory([(NOW - timedelta(hours=8), True)])
    temps = History([(NOW - timedelta(hours=5), 32.0), (NOW - timedelta(hours=1), None)])
    assert not estimator.sample(valves, temps, NOW)

    temps = History([(NOW - timedelta(hours=5), 32.0), (NOW - timedelta(hours=1), 32.1)])
    assert estimator.sample(valves, temps, NOW)


def test_estimator_bounds_and_restore():
    estimator = RateEstimator(MODEL)
    for _ in range(200):
        estimator.update(delta_temp=-2.0, heat_hours=2.0, hours=2.0)
    assert estimator.heating_rate >= MODEL.heating_rate / 2
    assert estimator.cooling_rate <= MODEL.cooling_rate * 2

    restored = RateEstimator(MODEL)
    restored.restore(estimator.as_dict())
    assert restored.as_dict() == estimator.as_dict()

    # Changed configuration: start over from it
    other = RateEstimator(FloorModel(MODEL.dead_time, MODEL.lag, 0.6, 0.4))
    other.restore(estimator.as_dict())
    assert (other.heating_rate, other.cooling_rate) == (0.6, 0.4)
