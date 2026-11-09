"""Tests for light controls, remotes and motion sensors."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.core import HomeAssistant, ServiceCall, State
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
    mock_restore_cache,
)

from custom_components.ulstruplund.const import DOMAIN

WALL = "light.wall_light"
SPOT = "light.spot_light"
PIR = "binary_sensor.pir_motion"
DAY_PIR = "binary_sensor.day_pir_motion"
IEEE = "04:0d:84:ff:fe:98:77:81"

CONFIG = {
    DOMAIN: {
        "lights": [{"light": WALL}, {"light": SPOT, "name": "Spot"}],
        "remotes": [{"type": "styrbar", "ieee": IEEE, "groups": [[WALL], [SPOT]]}],
        "motion_sensors": [
            {"sensor": PIR, "lights": [WALL], "only_when_dark": False},
            {"sensor": DAY_PIR, "lights": [SPOT]},
        ],
    }
}

# Test instance is in US/Pacific, San Diego
NOON = "2026-11-09 12:00:00-08:00"
NIGHT = "2026-11-09 22:00:00-08:00"


class Lights:
    """Mocked light services, applying their effect to the light's state."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.on = async_mock_service(hass, "light", "turn_on")
        self.off = async_mock_service(hass, "light", "turn_off")

    def apply(self, call: ServiceCall) -> None:
        state = "on" if call.service == "turn_on" else "off"
        self.hass.states.async_set(call.data["entity_id"], state, context=call.context)


async def setup(hass: HomeAssistant, freezer: FrozenDateTimeFactory, time=NIGHT,
                config=CONFIG) -> Lights:
    freezer.move_to(time)
    for light in (WALL, SPOT):
        hass.states.async_set(light, "off")
    hass.states.async_set(PIR, "off")
    hass.states.async_set(DAY_PIR, "off")
    lights = Lights(hass)
    assert await async_setup_component(hass, DOMAIN, config)
    await hass.async_block_till_done()
    return lights


async def press(hass: HomeAssistant, command: str, args=None) -> None:
    hass.bus.async_fire(
        "zha_event", {"device_ieee": IEEE, "command": command, "args": args or []}
    )
    await hass.async_block_till_done()


async def test_sensor_entities(hass, freezer):
    await setup(hass, freezer)
    state = hass.states.get("sensor.wall_light_control")
    assert state.state == "off"
    assert state.attributes["light"] == WALL
    assert hass.states.get("sensor.spot_control").state == "off"


async def test_remote_on_off(hass, freezer):
    lights = await setup(hass, freezer)

    await press(hass, "on")
    assert len(lights.on) == 1
    assert lights.on[0].data == {"entity_id": WALL, "brightness": 255}
    lights.apply(lights.on[0])
    await hass.async_block_till_done()
    assert hass.states.get("sensor.wall_light_control").state == "manual"

    await press(hass, "off")
    assert len(lights.off) == 1
    lights.apply(lights.off[0])
    await hass.async_block_till_done()
    assert hass.states.get("sensor.wall_light_control").state == "off"


async def test_remote_selects_group(hass, freezer):
    lights = await setup(hass, freezer)

    await press(hass, "press", [256, 13, 0])  # right
    assert lights.on[-1].data["entity_id"] == SPOT
    assert lights.on[-1].data["flash"] == "short"
    lights.apply(lights.on[-1])
    freezer.tick(timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert lights.off[-1].data["entity_id"] == SPOT
    lights.apply(lights.off[-1])
    await hass.async_block_till_done()
    assert hass.states.get("sensor.spot_control").state == "off"

    await press(hass, "on")
    assert lights.on[-1].data == {"entity_id": SPOT, "brightness": 255}

    await press(hass, "press", [257, 13, 0])  # left, back to wall
    assert lights.on[-1].data["entity_id"] == WALL


async def test_motion(hass, freezer):
    lights = await setup(hass, freezer)

    hass.states.async_set(PIR, "on")
    await hass.async_block_till_done()
    assert lights.on[-1].data == {"entity_id": WALL, "brightness": 50}
    lights.apply(lights.on[-1])
    await hass.async_block_till_done()
    assert hass.states.get("sensor.wall_light_control").state == "motion"

    hass.states.async_set(PIR, "off")
    await hass.async_block_till_done()
    assert lights.off[-1].data == {"entity_id": WALL}


async def test_motion_timeout(hass, freezer):
    lights = await setup(hass, freezer)

    hass.states.async_set(PIR, "on")
    await hass.async_block_till_done()
    lights.apply(lights.on[-1])
    await hass.async_block_till_done()

    freezer.tick(timedelta(minutes=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert lights.off[-1].data == {"entity_id": WALL}


async def test_external_on_is_manual(hass, freezer):
    lights = await setup(hass, freezer)

    hass.states.async_set(WALL, "on")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.wall_light_control").state == "manual"

    hass.states.async_set(PIR, "on")
    await hass.async_block_till_done()
    hass.states.async_set(PIR, "off")
    await hass.async_block_till_done()
    assert not lights.off

    freezer.tick(timedelta(hours=3, minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert lights.off[-1].data == {"entity_id": WALL}


async def test_light_powered_on_by_wall_switch_is_manual(hass, freezer):
    lights = await setup(hass, freezer)
    hass.states.async_set(WALL, "unavailable")
    await hass.async_block_till_done()
    hass.states.async_set(WALL, "on")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.wall_light_control").state == "manual"

    # Motion ending must not turn it off
    hass.states.async_set(PIR, "on")
    await hass.async_block_till_done()
    hass.states.async_set(PIR, "off")
    await hass.async_block_till_done()
    assert not lights.off

    freezer.tick(timedelta(hours=3, minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert lights.off[-1].data == {"entity_id": WALL}


async def test_light_coming_back_off_is_not_manual(hass, freezer):
    await setup(hass, freezer)
    hass.states.async_set(WALL, "unavailable")
    await hass.async_block_till_done()
    hass.states.async_set(WALL, "off")
    await hass.async_block_till_done()
    state = hass.states.get("sensor.wall_light_control")
    assert state.state == "off"
    assert state.attributes["timeout"] is None


@pytest.mark.parametrize(("time", "turns_on"), [(NOON, False), (NIGHT, True)])
async def test_motion_only_when_dark(hass, freezer, time, turns_on):
    lights = await setup(hass, freezer, time=time)

    hass.states.async_set(DAY_PIR, "on")
    await hass.async_block_till_done()
    assert bool(lights.on) == turns_on


async def test_restore_expired_timeout(hass, freezer):
    freezer.move_to(NIGHT)
    expired = (dt_util.utcnow() - timedelta(minutes=5)).isoformat()
    mock_restore_cache(
        hass,
        [State("sensor.wall_light_control", "manual", {"manual": True, "timeout": expired})],
    )
    hass.states.async_set(WALL, "on")
    lights = Lights(hass)
    hass.states.async_set(SPOT, "off")
    assert await async_setup_component(hass, DOMAIN, CONFIG)
    await hass.async_block_till_done()
    assert lights.off[-1].data == {"entity_id": WALL}


async def test_restore_pending_timeout(hass, freezer):
    freezer.move_to(NIGHT)
    pending = (dt_util.utcnow() + timedelta(hours=1)).isoformat()
    mock_restore_cache(
        hass,
        [State("sensor.wall_light_control", "manual", {"manual": True, "timeout": pending})],
    )
    hass.states.async_set(WALL, "on")
    lights = Lights(hass)
    assert await async_setup_component(hass, DOMAIN, CONFIG)
    await hass.async_block_till_done()
    assert not lights.off
    assert hass.states.get("sensor.wall_light_control").state == "manual"

    freezer.tick(timedelta(hours=1, minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert lights.off[-1].data == {"entity_id": WALL}


async def test_unknown_light_reference(hass, freezer):
    config = {DOMAIN: {"motion_sensors": [{"sensor": PIR, "lights": [WALL]}]}}
    assert not await async_setup_component(hass, DOMAIN, config)
