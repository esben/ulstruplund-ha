"""Floor heating valve control logic.

Kept free of Home Assistant imports so it can be unit tested directly.
"""

from dataclasses import dataclass
from enum import StrEnum


class ValveCommand(StrEnum):
    """What to do with the valve."""

    OPEN = "open"
    CLOSE = "close"
    KEEP = "keep"


class Reason(StrEnum):
    """Why a command was chosen (exposed as an attribute for debugging)."""

    OFF = "off"
    SENSOR_UNAVAILABLE = "sensor_unavailable"
    SENSOR_UNAVAILABLE_WAITING = "sensor_unavailable_waiting"
    REVERSE_FLOW = "reverse_flow"
    BELOW_SETPOINT = "below_setpoint"
    ABOVE_SETPOINT = "above_setpoint"
    WITHIN_HYSTERESIS = "within_hysteresis"
    FORECAST_BELOW_SETPOINT = "forecast_below_setpoint"
    FORECAST_ABOVE_SETPOINT = "forecast_above_setpoint"
    FORECAST_WITHIN_TOLERANCE = "forecast_within_tolerance"
    MIN_SWITCH_INTERVAL = "min_switch_interval"


@dataclass(frozen=True)
class Decision:
    command: ValveCommand
    reason: Reason


@dataclass(frozen=True)
class Forecast:
    """Forecast floor temperature, for predictive control."""

    valve_open: bool
    # Highest temperature reached if the valve is closed now
    max_if_closed: float
    # Lowest temperature reached if the valve is opened now
    min_if_open: float
    # Whether the minimum time since the last valve change has passed
    can_switch: bool = True


def _diff(a: float, b: float) -> float:
    """Return a - b without floating point noise (32 - 31.8 is 0.1999...)."""
    return round(a - b, 6)


def decide(
    *,
    enabled: bool,
    setpoint: float,
    floor_temp: float | None,
    inflow_temp: float | None = None,
    outflow_temp: float | None = None,
    cold_tolerance: float = 0.5,
    hot_tolerance: float = 0.0,
    reverse_flow_margin: float = 0.5,
    forecast: Forecast | None = None,
    wait_for_sensor: bool = False,
) -> Decision:
    """Decide what to do with the valve.

    The valve is opened when the floor is at least cold_tolerance below the
    setpoint, and closed when it is more than hot_tolerance above. In between,
    the valve is left alone.

    If outflow is warmer than inflow by more than reverse_flow_margin, the
    water is pulling heat out of the floor, so the valve is closed. This check
    is skipped if inflow or outflow temperature is unknown.

    If the floor temperature is unknown, the valve is closed, unless
    wait_for_sensor (e.g. a sensor briefly unavailable after a restart).

    With a forecast (predictive control), the open valve is closed when the
    temperature would rise to more than hot_tolerance above the setpoint even
    if it is closed now, and the closed valve is opened when the temperature
    would drop to cold_tolerance below the setpoint even if it is opened now.
    The valve is not switched again until can_switch, except for the safety
    reasons above.
    """
    if not enabled:
        return Decision(ValveCommand.CLOSE, Reason.OFF)
    if floor_temp is None:
        if wait_for_sensor:
            return Decision(ValveCommand.KEEP, Reason.SENSOR_UNAVAILABLE_WAITING)
        return Decision(ValveCommand.CLOSE, Reason.SENSOR_UNAVAILABLE)
    if (
        inflow_temp is not None
        and outflow_temp is not None
        and _diff(inflow_temp, outflow_temp) < -reverse_flow_margin
    ):
        return Decision(ValveCommand.CLOSE, Reason.REVERSE_FLOW)
    if forecast is not None:
        return _decide_forecast(setpoint, cold_tolerance, hot_tolerance, forecast)
    if _diff(setpoint, floor_temp) >= cold_tolerance:
        return Decision(ValveCommand.OPEN, Reason.BELOW_SETPOINT)
    if _diff(floor_temp, setpoint) > hot_tolerance:
        return Decision(ValveCommand.CLOSE, Reason.ABOVE_SETPOINT)
    return Decision(ValveCommand.KEEP, Reason.WITHIN_HYSTERESIS)


def _decide_forecast(
    setpoint: float, cold_tolerance: float, hot_tolerance: float, forecast: Forecast
) -> Decision:
    if forecast.valve_open:
        if _diff(forecast.max_if_closed, setpoint) >= hot_tolerance:
            command, reason = ValveCommand.CLOSE, Reason.FORECAST_ABOVE_SETPOINT
        else:
            return Decision(ValveCommand.KEEP, Reason.FORECAST_WITHIN_TOLERANCE)
    elif _diff(setpoint, forecast.min_if_open) >= cold_tolerance:
        command, reason = ValveCommand.OPEN, Reason.FORECAST_BELOW_SETPOINT
    else:
        return Decision(ValveCommand.KEEP, Reason.FORECAST_WITHIN_TOLERANCE)
    if not forecast.can_switch:
        return Decision(ValveCommand.KEEP, Reason.MIN_SWITCH_INTERVAL)
    return Decision(command, reason)
