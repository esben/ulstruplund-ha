"""Ulstruplund home automation."""

import voluptuous as vol

from homeassistant.const import CONF_ID, CONF_NAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_COLD_TOLERANCE,
    CONF_FLOOR_HEATING,
    CONF_FLOOR_TEMP,
    CONF_HOT_TOLERANCE,
    CONF_INFLOW_TEMP,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_OUTFLOW_TEMP,
    CONF_REVERSE_FLOW_MARGIN,
    CONF_SETPOINT,
    CONF_VALVE,
    DEFAULT_COLD_TOLERANCE,
    DEFAULT_HOT_TOLERANCE,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_REVERSE_FLOW_MARGIN,
    DEFAULT_SETPOINT,
    DOMAIN,
)

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

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_FLOOR_HEATING, default=[]): vol.All(
                    cv.ensure_list, [FLOOR_HEATING_SCHEMA]
                ),
            }
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
    return True
