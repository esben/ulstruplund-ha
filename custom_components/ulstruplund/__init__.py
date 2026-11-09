"""Ulstruplund home automation."""

import voluptuous as vol

from homeassistant.const import CONF_ID, CONF_NAME, CONF_TYPE, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_COLD_TOLERANCE,
    CONF_FLOOR_HEATING,
    CONF_FLOOR_TEMP,
    CONF_GROUPS,
    CONF_HOT_TOLERANCE,
    CONF_IEEE,
    CONF_INFLOW_TEMP,
    CONF_LIGHT,
    CONF_LIGHTS,
    CONF_MANUAL_TIMEOUT,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_MOTION_BRIGHTNESS,
    CONF_MOTION_SENSORS,
    CONF_MOTION_TIMEOUT,
    CONF_ON_BRIGHTNESS,
    CONF_ONLY_WHEN_DARK,
    CONF_OUTFLOW_TEMP,
    CONF_REMOTES,
    CONF_REVERSE_FLOW_MARGIN,
    CONF_SENSOR,
    CONF_SETPOINT,
    CONF_SUNRISE_OFFSET,
    CONF_SUNSET_OFFSET,
    CONF_VALVE,
    DEFAULT_COLD_TOLERANCE,
    DEFAULT_HOT_TOLERANCE,
    DEFAULT_MANUAL_TIMEOUT,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_MOTION_BRIGHTNESS,
    DEFAULT_MOTION_TIMEOUT,
    DEFAULT_ON_BRIGHTNESS,
    DEFAULT_REVERSE_FLOW_MARGIN,
    DEFAULT_SETPOINT,
    DEFAULT_SUNRISE_OFFSET,
    DEFAULT_SUNSET_OFFSET,
    DOMAIN,
    REMOTE_TYPE_STYRBAR,
)
from .lights import LightControl, MotionSensor, StyrbarRemote

FLOOR_HEATING_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ID): cv.slug,
        vol.Optional(CONF_NAME): cv.string,
        vol.Required(CONF_FLOOR_TEMP): cv.entity_domain("sensor"),
        vol.Required(CONF_VALVE): cv.entity_domain("valve"),
        vol.Inclusive(CONF_INFLOW_TEMP, "flow"): cv.entity_domain("sensor"),
        vol.Inclusive(CONF_OUTFLOW_TEMP, "flow"): cv.entity_domain("sensor"),
        vol.Optional(CONF_SETPOINT, default=DEFAULT_SETPOINT): vol.Coerce(float),
        vol.Optional(CONF_MIN_TEMP, default=DEFAULT_MIN_TEMP): vol.Coerce(float),
        vol.Optional(CONF_MAX_TEMP, default=DEFAULT_MAX_TEMP): vol.Coerce(float),
        vol.Optional(
            CONF_COLD_TOLERANCE, default=DEFAULT_COLD_TOLERANCE
        ): vol.Coerce(float),
        vol.Optional(
            CONF_HOT_TOLERANCE, default=DEFAULT_HOT_TOLERANCE
        ): vol.Coerce(float),
        vol.Optional(
            CONF_REVERSE_FLOW_MARGIN, default=DEFAULT_REVERSE_FLOW_MARGIN
        ): vol.Coerce(float),
    }
)

BRIGHTNESS = vol.All(vol.Coerce(int), vol.Range(min=1, max=255))

LIGHT_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_LIGHT): cv.entity_domain("light"),
        vol.Optional(CONF_NAME): cv.string,
        vol.Optional(CONF_ON_BRIGHTNESS, default=DEFAULT_ON_BRIGHTNESS): BRIGHTNESS,
        vol.Optional(
            CONF_MOTION_BRIGHTNESS, default=DEFAULT_MOTION_BRIGHTNESS
        ): BRIGHTNESS,
        vol.Optional(
            CONF_MANUAL_TIMEOUT, default=DEFAULT_MANUAL_TIMEOUT
        ): cv.positive_time_period,
        vol.Optional(
            CONF_MOTION_TIMEOUT, default=DEFAULT_MOTION_TIMEOUT
        ): cv.positive_time_period,
    }
)

LIGHT_LIST = vol.All(cv.ensure_list, vol.Length(min=1), [cv.entity_domain("light")])

REMOTE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TYPE): vol.In([REMOTE_TYPE_STYRBAR]),
        vol.Required(CONF_IEEE): cv.string,
        vol.Required(CONF_GROUPS): vol.All(
            cv.ensure_list, vol.Length(min=1), [LIGHT_LIST]
        ),
    }
)

MOTION_SENSOR_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SENSOR): cv.entity_domain("binary_sensor"),
        vol.Required(CONF_LIGHTS): LIGHT_LIST,
        vol.Optional(CONF_ONLY_WHEN_DARK, default=True): cv.boolean,
        vol.Optional(
            CONF_SUNRISE_OFFSET, default=DEFAULT_SUNRISE_OFFSET
        ): cv.time_period,
        vol.Optional(CONF_SUNSET_OFFSET, default=DEFAULT_SUNSET_OFFSET): cv.time_period,
    }
)


def _validate_light_references(conf: ConfigType) -> ConfigType:
    """Check that remotes and motion sensors only refer to configured lights."""
    lights = {light[CONF_LIGHT] for light in conf[CONF_LIGHTS]}
    referenced = [
        light
        for remote in conf[CONF_REMOTES]
        for group in remote[CONF_GROUPS]
        for light in group
    ] + [
        light for motion in conf[CONF_MOTION_SENSORS] for light in motion[CONF_LIGHTS]
    ]
    if unknown := sorted(set(referenced) - lights):
        raise vol.Invalid(f"lights not configured under '{CONF_LIGHTS}': {unknown}")
    return conf


CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.All(
            vol.Schema(
                {
                    vol.Optional(CONF_FLOOR_HEATING, default=[]): vol.All(
                        cv.ensure_list, [FLOOR_HEATING_SCHEMA]
                    ),
                    vol.Optional(CONF_LIGHTS, default=[]): vol.All(
                        cv.ensure_list, [LIGHT_SCHEMA]
                    ),
                    vol.Optional(CONF_REMOTES, default=[]): vol.All(
                        cv.ensure_list, [REMOTE_SCHEMA]
                    ),
                    vol.Optional(CONF_MOTION_SENSORS, default=[]): vol.All(
                        cv.ensure_list, [MOTION_SENSOR_SCHEMA]
                    ),
                }
            ),
            _validate_light_references,
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration from YAML."""
    conf = config[DOMAIN]
    if conf[CONF_FLOOR_HEATING]:
        hass.async_create_task(
            async_load_platform(
                hass, Platform.CLIMATE, DOMAIN, conf[CONF_FLOOR_HEATING], config
            )
        )

    lights = {
        light[CONF_LIGHT]: LightControl(hass, light) for light in conf[CONF_LIGHTS]
    }
    remotes = [
        StyrbarRemote(
            hass,
            remote,
            [[lights[light] for light in group] for group in remote[CONF_GROUPS]],
        )
        for remote in conf[CONF_REMOTES]
    ]
    motion_sensors = [
        MotionSensor(hass, motion, [lights[light] for light in motion[CONF_LIGHTS]])
        for motion in conf[CONF_MOTION_SENSORS]
    ]
    if lights:
        hass.async_create_task(
            async_load_platform(
                hass, Platform.SENSOR, DOMAIN, list(lights.values()), config
            )
        )

    @callback
    def _async_start(hass: HomeAssistant) -> None:
        # Start after all entities exist and have restored their state
        for light in lights.values():
            light.async_start()
        for remote in remotes:
            remote.async_start()
        for motion in motion_sensors:
            motion.async_start()

    async_at_started(hass, _async_start)
    return True
