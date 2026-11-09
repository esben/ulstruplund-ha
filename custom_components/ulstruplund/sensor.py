"""Sensors showing (and persisting) the state of light controls."""

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .light_control import Mode
from .lights import LightControl

ATTR_MANUAL = "manual"
ATTR_TIMEOUT = "timeout"


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up light control sensors from the integration's YAML config."""
    if discovery_info is None:
        return
    async_add_entities(LightControlSensor(control) for control in discovery_info)


class LightControlSensor(SensorEntity, RestoreEntity):
    """Shows whether a light is off, manually on or on due to motion."""

    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [mode.value for mode in Mode]

    def __init__(self, control: LightControl) -> None:
        self._control = control
        object_id = control.light_entity.split(".", 1)[1]
        self._attr_unique_id = f"{DOMAIN}_light_{object_id}"
        self._attr_name = f"{control.name} control"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self._control.async_add_update_listener(self.async_write_ha_state)
        )
        if (last_state := await self.async_get_last_state()) is not None:
            timeout = last_state.attributes.get(ATTR_TIMEOUT)
            self._control.async_restore(
                manual=bool(last_state.attributes.get(ATTR_MANUAL)),
                timeout=dt_util.parse_datetime(timeout) if timeout else None,
            )

    @property
    def native_value(self) -> str:
        return self._control.controller.mode

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        controller = self._control.controller
        return {
            "light": self._control.light_entity,
            ATTR_MANUAL: controller.manual,
            ATTR_TIMEOUT: controller.timeout.isoformat() if controller.timeout else None,
            "motion_detected": controller.motion_detected,
        }
