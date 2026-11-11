"""Tests for the floor thermostat with predictive control."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.restore_state import async_get as async_get_restore_state
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
    await tick(hass, freezer, 6)
    # Still within the 30 min since the valve opened
    assert close_calls


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


async def test_rates_estimated(hass, freezer, hass_storage):
    open_calls, close_calls = await setup(hass, freezer, floor="31.9", valve="closed")
    state = hass.states.get(ENTITY)
    assert state.attributes["estimated_heating_rate"] == 0.55
    assert state.attributes["estimated_cooling_rate"] == 0.38

    # Valve stays closed (mocked) while the floor cools faster than modelled:
    # 0.6 °C/h instead of 0.38
    temp = 31.9
    for _ in range(4 * 6):
        await tick(hass, freezer, 10)
        temp -= 0.1
        hass.states.async_set(FLOOR, f"{temp:.1f}")
        await hass.async_block_till_done()
    assert hass.states.get(ENTITY).attributes["estimated_cooling_rate"] > 0.45

    # The state saved for restarts is JSON serializable and has it all
    await async_get_restore_state(hass).async_dump_states()
    saved = next(
        entry["extra_data"]
        for entry in hass_storage["core.restore_state"]["data"]
        if entry["state"]["entity_id"] == ENTITY
    )
    assert saved["estimator"]["rates"][1] > 0.45
    assert saved["valve_history"][-1][1] is False
    assert saved["floor_temp_history"][-1][1] == pytest.approx(29.5)


async def test_estimator_restored(hass, freezer):
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(ENTITY, "heat", {"temperature": 32.0}),
                {
                    "valve_history": [],
                    "floor_temp_history": [],
                    "estimator": {
                        "initial": [0.55, 0.38],
                        "rates": [0.6, 0.42],
                        "p": [[0.001, 0.0], [0.0, 0.001]],
                    },
                },
            )
        ],
    )
    await setup(hass, freezer, floor="32.0", valve="closed")
    state = hass.states.get(ENTITY)
    assert state.attributes["estimated_heating_rate"] == 0.6
    assert state.attributes["estimated_cooling_rate"] == 0.42


async def test_not_adaptive(hass, freezer):
    conf = dict(CONFIG[DOMAIN]["floor_heating"][0], adaptive=False)
    hass.states.async_set(VALVE, "closed")
    hass.states.async_set(FLOOR, "32.0")
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: {"floor_heating": [conf]}})
    await hass.async_block_till_done()
    assert "estimated_heating_rate" not in hass.states.get(ENTITY).attributes
