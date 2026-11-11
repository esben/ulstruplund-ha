"""Tests for the pure floor heating control logic."""

import pytest

from custom_components.ulstruplund.floor_control import Reason, ValveCommand, decide


@pytest.mark.parametrize(
    ("floor_temp", "command", "reason"),
    [
        (31.5, ValveCommand.OPEN, Reason.BELOW_SETPOINT),
        (30.0, ValveCommand.OPEN, Reason.BELOW_SETPOINT),
        (31.6, ValveCommand.KEEP, Reason.WITHIN_HYSTERESIS),
        (32.0, ValveCommand.KEEP, Reason.WITHIN_HYSTERESIS),
        (32.1, ValveCommand.CLOSE, Reason.ABOVE_SETPOINT),
        (None, ValveCommand.CLOSE, Reason.SENSOR_UNAVAILABLE),
    ],
)
def test_setpoint(floor_temp, command, reason):
    decision = decide(enabled=True, setpoint=32.0, floor_temp=floor_temp)
    assert (decision.command, decision.reason) == (command, reason)


def test_off_closes():
    decision = decide(enabled=False, setpoint=32.0, floor_temp=20.0)
    assert (decision.command, decision.reason) == (ValveCommand.CLOSE, Reason.OFF)


@pytest.mark.parametrize(
    ("inflow", "outflow", "command"),
    [
        (30.0, 30.6, ValveCommand.CLOSE),
        (30.0, 30.5, ValveCommand.OPEN),
        (35.0, 30.0, ValveCommand.OPEN),
        (None, 40.0, ValveCommand.OPEN),
        (30.0, None, ValveCommand.OPEN),
    ],
)
def test_reverse_flow(inflow, outflow, command):
    decision = decide(
        enabled=True,
        setpoint=32.0,
        floor_temp=20.0,
        inflow_temp=inflow,
        outflow_temp=outflow,
    )
    assert decision.command == command


@pytest.mark.parametrize(
    ("floor_temp", "command"),
    [
        (31.9, ValveCommand.KEEP),
        (31.8, ValveCommand.OPEN),
        (32.1, ValveCommand.KEEP),
        (32.2, ValveCommand.CLOSE),
    ],
)
def test_tolerances_are_exact(floor_temp, command):
    decision = decide(
        enabled=True,
        setpoint=32.0,
        floor_temp=floor_temp,
        cold_tolerance=0.2,
        hot_tolerance=0.1,
    )
    assert decision.command == command


def test_reverse_flow_margin_is_exact():
    decision = decide(
        enabled=True,
        setpoint=32.0,
        floor_temp=20.0,
        inflow_temp=30.1,
        outflow_temp=30.4,
        reverse_flow_margin=0.3,
    )
    assert decision.command == ValveCommand.OPEN
