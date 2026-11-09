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
    REVERSE_FLOW = "reverse_flow"
    BELOW_SETPOINT = "below_setpoint"
    ABOVE_SETPOINT = "above_setpoint"
    WITHIN_HYSTERESIS = "within_hysteresis"


@dataclass(frozen=True)
class Decision:
    command: ValveCommand
    reason: Reason


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
) -> Decision:
    """Decide what to do with the valve.

    The valve is opened when the floor is at least cold_tolerance below the
    setpoint, and closed when it is more than hot_tolerance above. In between,
    the valve is left alone.

    If outflow is warmer than inflow by more than reverse_flow_margin, the
    water is pulling heat out of the floor, so the valve is closed. This check
    is skipped if inflow or outflow temperature is unknown.

    If the floor temperature is unknown, the valve is closed.
    """
    if not enabled:
        return Decision(ValveCommand.CLOSE, Reason.OFF)
    if floor_temp is None:
        return Decision(ValveCommand.CLOSE, Reason.SENSOR_UNAVAILABLE)
    if (
        inflow_temp is not None
        and outflow_temp is not None
        and (inflow_temp - outflow_temp) < -reverse_flow_margin
    ):
        return Decision(ValveCommand.CLOSE, Reason.REVERSE_FLOW)
    if (setpoint - floor_temp) >= cold_tolerance:
        return Decision(ValveCommand.OPEN, Reason.BELOW_SETPOINT)
    if (floor_temp - setpoint) > hot_tolerance:
        return Decision(ValveCommand.CLOSE, Reason.ABOVE_SETPOINT)
    return Decision(ValveCommand.KEEP, Reason.WITHIN_HYSTERESIS)
