"""Tests for the floor thermostat climate entity."""

from datetime import timedelta

from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
    mock_restore_cache,
)

from custom_components.ulstruplund.const import DOMAIN

ENTITY = "climate.office_floor_loop_1"
FLOOR = "sensor.floor_1"
VALVE = "valve.loop_1"
INFLOW = "sensor.inflow"
OUTFLOW = "sensor.outflow"

CONFIG = {
    DOMAIN: {
        "floor_heating": [
            {
                "id": "loop_1",
                "name": "Office floor loop 1",
                "floor_temp": FLOOR,
                "valve": VALVE,
                "inflow_temp": INFLOW,
                "outflow_temp": OUTFLOW,
                "setpoint": 32.0,
            }
        ]
    }
}


async def setup(hass: HomeAssistant, *, floor="30.0", valve="closed",
                inflow="35.0", outflow="30.0"):
    hass.states.async_set(FLOOR, floor)
    hass.states.async_set(VALVE, valve)
    if inflow is not None:
        hass.states.async_set(INFLOW, inflow)
    if outflow is not None:
        hass.states.async_set(OUTFLOW, outflow)
    open_calls = async_mock_service(hass, "valve", "open_valve")
    close_calls = async_mock_service(hass, "valve", "close_valve")
    assert await async_setup_component(hass, DOMAIN, CONFIG)
    await hass.async_block_till_done()
    return open_calls, close_calls


async def test_opens_when_cold(hass: HomeAssistant):
    open_calls, close_calls = await setup(hass, floor="30.0", valve="closed")
    assert len(open_calls) == 1
    assert open_calls[0].data[ATTR_ENTITY_ID] == VALVE
    assert not close_calls

    state = hass.states.get(ENTITY)
    assert state.state == HVACMode.HEAT
    assert state.attributes[ATTR_TEMPERATURE] == 32.0
    assert state.attributes["current_temperature"] == 30.0


async def test_closes_when_warm(hass: HomeAssistant):
    open_calls, close_calls = await setup(hass, floor="30.0", valve="open")
    assert not open_calls

    hass.states.async_set(FLOOR, "32.5")
    await hass.async_block_till_done()
    assert len(close_calls) == 1


async def test_no_call_when_valve_already_right(hass: HomeAssistant):
    open_calls, close_calls = await setup(hass, floor="30.0", valve="open")
    assert not open_calls
    assert not close_calls
    assert hass.states.get(ENTITY).attributes["hvac_action"] == HVACAction.HEATING


async def test_closes_on_reverse_flow(hass: HomeAssistant):
    open_calls, close_calls = await setup(hass, floor="30.0", valve="open")

    hass.states.async_set(OUTFLOW, "36.0")
    await hass.async_block_till_done()
    assert len(close_calls) == 1
    assert hass.states.get(ENTITY).attributes["control_reason"] == "reverse_flow"


async def tick(hass: HomeAssistant, freezer, minutes: int) -> None:
    for _ in range(minutes):
        freezer.tick(timedelta(minutes=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


async def test_closes_when_floor_sensor_unavailable(hass: HomeAssistant, freezer):
    open_calls, close_calls = await setup(hass, floor="30.0", valve="open")

    hass.states.async_set(FLOOR, STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    assert not close_calls
    state = hass.states.get(ENTITY)
    assert state.attributes["control_reason"] == "sensor_unavailable_waiting"

    await tick(hass, freezer, 4)
    assert not close_calls
    await tick(hass, freezer, 2)
    assert close_calls


async def test_floor_sensor_unavailable_at_startup(hass: HomeAssistant, freezer):
    # After a restart, the sensor is briefly unavailable: leave the valve alone
    open_calls, close_calls = await setup(
        hass, floor=STATE_UNAVAILABLE, valve="open", inflow=None, outflow=None
    )
    await tick(hass, freezer, 1)
    hass.states.async_set(FLOOR, "31.0")
    await tick(hass, freezer, 10)
    assert not close_calls


async def test_set_temperature(hass: HomeAssistant):
    open_calls, close_calls = await setup(hass, floor="30.0", valve="open")

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: ENTITY, ATTR_TEMPERATURE: 25.0},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert len(close_calls) == 1
    assert hass.states.get(ENTITY).attributes[ATTR_TEMPERATURE] == 25.0


async def test_turn_off_closes(hass: HomeAssistant):
    open_calls, close_calls = await setup(hass, floor="30.0", valve="open")

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: ENTITY, "hvac_mode": HVACMode.OFF},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert len(close_calls) == 1
    state = hass.states.get(ENTITY)
    assert state.state == HVACMode.OFF
    assert state.attributes["hvac_action"] == HVACAction.OFF


async def test_does_not_command_unavailable_valve(hass: HomeAssistant):
    open_calls, close_calls = await setup(hass, floor="30.0", valve=STATE_UNAVAILABLE)
    assert not open_calls
    assert not close_calls

    hass.states.async_set(VALVE, "closed")
    await hass.async_block_till_done()
    assert len(open_calls) == 1


async def test_restores_setpoint_and_mode(hass: HomeAssistant):
    mock_restore_cache(
        hass, [State(ENTITY, HVACMode.OFF, {ATTR_TEMPERATURE: 28.0})]
    )
    open_calls, close_calls = await setup(hass, floor="20.0", valve="closed")
    assert not open_calls

    state = hass.states.get(ENTITY)
    assert state.state == HVACMode.OFF
    assert state.attributes[ATTR_TEMPERATURE] == 28.0
