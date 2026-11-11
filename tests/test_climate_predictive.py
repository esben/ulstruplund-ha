"""Tests for the floor thermostat with predictive control."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory

from homeassistant.core import HomeAssistant, State
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
    mock_restore_cache_with_extra_data,
)

from custom_components.ulstruplund.const import DOMAIN

ENTITY = "climate.office_floor_loop_2"
FLOOR = "sensor.floor_2"
VALVE = "valve.loop_2"

CONFIG = {
    DOMAIN: {
        "floor_heating": [
            {
                "id": "loop_2",
                "name": "Office floor loop 2",
                "floor_temp": FLOOR,
                "valve": VALVE,
                "setpoint": 32.0,
                "cold_tolerance": 0.1,
                "hot_tolerance": 0.1,
                "control": "predictive",
                "dead_time": "00:45:00",
                "lag": "00:30:00",
                "heating_rate": 0.55,
                "cooling_rate": 0.38,
            }
        ]
    }
}


async def setup(hass: HomeAssistant, freezer: FrozenDateTimeFactory, *, floor: str,
                valve: str, valve_since: timedelta = timedelta(hours=6)):
    """Set up with the valve in a state for valve_since."""
    freezer.move_to(dt_util.utcnow() - valve_since)
    hass.states.async_set(VALVE, valve)
    freezer.move_to(dt_util.utcnow() + valve_since)
    hass.states.async_set(FLOOR, floor)
    open_calls = async_mock_service(hass, "valve", "open_valve")
    close_calls = async_mock_service(hass, "valve", "close_valve")
    assert await async_setup_component(hass, DOMAIN, CONFIG)
    await hass.async_block_till_done()
    return open_calls, close_calls


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, minutes: int = 1):
    for _ in range(minutes):
        freezer.tick(timedelta(minutes=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


async def test_opens_before_floor_gets_cold(hass, freezer):
    # Hysteresis would wait until 31.9, but the floor will cool for another
    # hour after opening, so open already at 32.1
    open_calls, close_calls = await setup(hass, freezer, floor="32.1", valve="closed")
    assert len(open_calls) == 1
    state = hass.states.get(ENTITY)
    assert state.attributes["control_reason"] == "forecast_below_setpoint"
    assert state.attributes["forecast_min_if_open"] < 31.9


async def test_closes_before_floor_gets_warm(hass, freezer):
    open_calls, close_calls = await setup(hass, freezer, floor="32.0", valve="open")
    assert len(close_calls) == 1
    assert hass.states.get(ENTITY).attributes["forecast_max_if_closed"] >= 32.1


async def test_keeps_valve_when_forecast_within_tolerance(hass, freezer):
    open_calls, close_calls = await setup(hass, freezer, floor="31.9", valve="open")
    assert not open_calls and not close_calls
    state = hass.states.get(ENTITY)
    assert state.attributes["control_reason"] == "forecast_within_tolerance"


async def test_min_switch_interval(hass, freezer):
    open_calls, close_calls = await setup(
        hass, freezer, floor="31.9", valve="open", valve_since=timedelta(minutes=5)
    )
    # Valve opened 5 min ago; the floor jumping up makes closing desirable
    hass.states.async_set(FLOOR, "32.3")
    await hass.async_block_till_done()
    assert not close_calls
    assert hass.states.get(ENTITY).attributes["control_reason"] == "min_switch_interval"

    await tick(hass, freezer, 24)
    assert not close_calls
    await tick(hass, freezer, 2)
    assert close_calls


async def test_safety_ignores_min_switch_interval(hass, freezer):
    open_calls, close_calls = await setup(
        hass, freezer, floor="31.9", valve="open", valve_since=timedelta(minutes=5)
    )
    hass.states.async_set(FLOOR, "unavailable")
    await hass.async_block_till_done()
    assert len(close_calls) == 1


async def test_valve_history_restored(hass, freezer):
    # Valve was opened 20 min ago, before the restart: the heat is still on
    # its way, so closing is not allowed yet, and the forecast includes it
    opened = dt_util.utcnow() - timedelta(minutes=20)
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(ENTITY, "heat", {"temperature": 32.0}),
                {
                    "valve_history": [
                        ((opened - timedelta(hours=5)).isoformat(), False),
                        (opened.isoformat(), True),
                    ]
                },
            )
        ],
    )
    open_calls, close_calls = await setup(
        hass, freezer, floor="32.3", valve="open", valve_since=timedelta(minutes=1)
    )
    assert not close_calls
    assert hass.states.get(ENTITY).attributes["control_reason"] == "min_switch_interval"
    await tick(hass, freezer, 8)
    assert not close_calls
    await tick(hass, freezer, 2)
    assert close_calls


async def test_predictive_requires_model(hass):
    config = {DOMAIN: {"floor_heating": [dict(CONFIG[DOMAIN]["floor_heating"][0])]}}
    del config[DOMAIN]["floor_heating"][0]["lag"]
    assert not await async_setup_component(hass, DOMAIN, config)
