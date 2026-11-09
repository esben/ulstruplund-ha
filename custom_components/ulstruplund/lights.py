"""Light controls, remotes and motion sensors wired to Home Assistant."""

from collections import deque
from datetime import datetime
import logging

from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_FLASH, ATTR_TRANSITION
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_NAME,
    EVENT_HOMEASSISTANT_STOP,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    SUN_EVENT_SUNRISE,
    SUN_EVENT_SUNSET,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Context,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers.event import (
    async_call_later,
    async_track_point_in_utc_time,
    async_track_state_change_event,
)
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import (
    CONF_IEEE,
    CONF_LIGHT,
    CONF_MANUAL_TIMEOUT,
    CONF_MOTION_BRIGHTNESS,
    CONF_MOTION_TIMEOUT,
    CONF_ON_BRIGHTNESS,
    CONF_ONLY_WHEN_DARK,
    CONF_SENSOR,
    CONF_SUNRISE_OFFSET,
    CONF_SUNSET_OFFSET,
)
from .light_control import Command, LightController, TurnOff, TurnOn, is_dark
from .styrbar import StyrbarParser

_LOGGER = logging.getLogger(__name__)

ZHA_EVENT = "zha_event"


def _is_on(state: State | None) -> bool | None:
    if state is None or state.state not in (STATE_ON, STATE_OFF):
        return None
    return state.state == STATE_ON


class LightControl:
    """Runs a LightController for one light entity."""

    def __init__(self, hass: HomeAssistant, conf: ConfigType) -> None:
        self.hass = hass
        self.light_entity: str = conf[CONF_LIGHT]
        self.name: str = conf.get(CONF_NAME, self.light_entity.split(".", 1)[1])
        self.controller = LightController(
            on_brightness=conf[CONF_ON_BRIGHTNESS],
            motion_brightness=conf[CONF_MOTION_BRIGHTNESS],
            manual_timeout=conf[CONF_MANUAL_TIMEOUT],
            motion_timeout=conf[CONF_MOTION_TIMEOUT],
        )
        self._started = False
        self._own_contexts: deque[str] = deque(maxlen=20)
        self._cancel_timeout: CALLBACK_TYPE | None = None
        self._cancel_flash: CALLBACK_TYPE | None = None
        self._update_listeners: list[CALLBACK_TYPE] = []

    @callback
    def async_add_update_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call listener whenever the controller state changes."""
        self._update_listeners.append(listener)
        return lambda: self._update_listeners.remove(listener)

    @callback
    def async_restore(self, manual: bool, timeout: datetime | None) -> None:
        """Restore state saved before a restart."""
        self.controller.manual = manual
        self.controller.timeout = timeout
        if self._started:
            self._async_process(self.controller.check_timeout(dt_util.utcnow()))

    @callback
    def async_start(self) -> None:
        self.controller.light_changed(
            _is_on(self.hass.states.get(self.light_entity)),
            external=False,
            now=dt_util.utcnow(),
        )
        async_track_state_change_event(
            self.hass, [self.light_entity], self._async_light_changed
        )
        self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._async_stop)
        self._started = True
        self._async_process(self.controller.check_timeout(dt_util.utcnow()))

    @callback
    def _async_stop(self, _event: Event) -> None:
        for cancel in (self._cancel_timeout, self._cancel_flash):
            if cancel is not None:
                cancel()
        self._cancel_timeout = self._cancel_flash = None

    @callback
    def async_button(self, event: str) -> None:
        _LOGGER.info("%s: button %s", self.light_entity, event)
        self._async_process(self.controller.button(event, dt_util.utcnow()))

    @callback
    def async_motion(self, sensor: str, detected: bool) -> None:
        self._async_process(self.controller.motion(sensor, detected, dt_util.utcnow()))

    @callback
    def async_flash(self) -> None:
        """Flash the light briefly, leaving it in its current on/off state."""
        self._async_call(SERVICE_TURN_ON, {ATTR_TRANSITION: 0, ATTR_FLASH: "short"})
        if not self.controller.light_on:
            self._cancel_flash = async_call_later(self.hass, 1, self._async_flash_off)

    @callback
    def _async_flash_off(self, _now: datetime) -> None:
        self._cancel_flash = None
        self._async_call(SERVICE_TURN_OFF, {ATTR_TRANSITION: 0})

    @callback
    def _async_light_changed(self, event: Event[EventStateChangedData]) -> None:
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]
        if old_state is not None and new_state is not None:
            if old_state.state == new_state.state:
                return
        on = _is_on(new_state)
        # A light coming back on after being unavailable is most likely powered
        # on by a wall switch, so it counts as operated manually. Coming back
        # off doesn't.
        external = (
            on is not None
            and (on or _is_on(old_state) is not None)
            and new_state is not None
            and new_state.context.id not in self._own_contexts
            and new_state.context.parent_id not in self._own_contexts
        )
        if external:
            _LOGGER.info("%s: turned %s externally", self.light_entity, "on" if on else "off")
        self.controller.light_changed(on, external=external, now=dt_util.utcnow())
        self._async_process(None)

    @callback
    def _async_timeout(self, now: datetime) -> None:
        self._cancel_timeout = None
        self._async_process(self.controller.check_timeout(now))

    @callback
    def _async_process(self, command: Command | None) -> None:
        if isinstance(command, TurnOn):
            _LOGGER.info("%s: turn on (%s)", self.light_entity, command.brightness)
            self._async_call(SERVICE_TURN_ON, {ATTR_BRIGHTNESS: command.brightness})
        elif isinstance(command, TurnOff):
            _LOGGER.info("%s: turn off", self.light_entity)
            self._async_call(SERVICE_TURN_OFF, {})

        if self._cancel_timeout is not None:
            self._cancel_timeout()
            self._cancel_timeout = None
        if self.controller.timeout is not None:
            self._cancel_timeout = async_track_point_in_utc_time(
                self.hass, self._async_timeout, self.controller.timeout
            )

        for listener in self._update_listeners:
            listener()

    @callback
    def _async_call(self, service: str, data: dict) -> None:
        context = Context()
        self._own_contexts.append(context.id)
        self.hass.async_create_task(
            self.hass.services.async_call(
                "light",
                service,
                {ATTR_ENTITY_ID: self.light_entity, **data},
                context=context,
            )
        )


class StyrbarRemote:
    """IKEA STYRBAR remote cycling through groups of lights.

    Left/right selects the previous/next group and flashes it; on/off is sent
    to the lights of the selected group.
    """

    def __init__(
        self, hass: HomeAssistant, conf: ConfigType, groups: list[list[LightControl]]
    ) -> None:
        self.hass = hass
        self.ieee: str = conf[CONF_IEEE]
        self.groups = groups
        self.current = 0
        self._parser = StyrbarParser()

    @callback
    def async_start(self) -> None:
        self.hass.bus.async_listen(
            ZHA_EVENT, self._async_zha_event, event_filter=self._event_filter
        )

    @callback
    def _event_filter(self, event_data: dict) -> bool:
        return event_data.get("device_ieee") == self.ieee

    @callback
    def _async_zha_event(self, event: Event) -> None:
        button = self._parser.parse(
            event.data.get("command"), event.data.get("args"), event.time_fired
        )
        _LOGGER.debug("%s: %s -> %s", self.ieee, event.data, button)
        if button == "right":
            self.current = (self.current + 1) % len(self.groups)
            self._async_flash()
        elif button == "left":
            self.current = (self.current - 1) % len(self.groups)
            self._async_flash()
        elif button in ("on", "off"):
            for light in self.groups[self.current]:
                light.async_button(button)

    @callback
    def _async_flash(self) -> None:
        for light in self.groups[self.current]:
            light.async_flash()


class MotionSensor:
    """Binary sensor reporting motion to light controls."""

    def __init__(
        self, hass: HomeAssistant, conf: ConfigType, lights: list[LightControl]
    ) -> None:
        self.hass = hass
        self.sensor: str = conf[CONF_SENSOR]
        self.lights = lights
        self.only_when_dark: bool = conf[CONF_ONLY_WHEN_DARK]
        self.sunrise_offset = conf[CONF_SUNRISE_OFFSET]
        self.sunset_offset = conf[CONF_SUNSET_OFFSET]

    @callback
    def async_start(self) -> None:
        async_track_state_change_event(
            self.hass, [self.sensor], self._async_sensor_changed
        )
        self._async_report(self.hass.states.get(self.sensor))

    @callback
    def _async_sensor_changed(self, event: Event[EventStateChangedData]) -> None:
        self._async_report(event.data["new_state"])

    @callback
    def _async_report(self, state: State | None) -> None:
        detected = _is_on(state)
        if detected is None:
            return
        if detected and self.only_when_dark and not self._is_dark():
            _LOGGER.debug("%s: ignoring motion in daylight", self.sensor)
            return
        for light in self.lights:
            light.async_motion(self.sensor, detected)

    def _is_dark(self) -> bool:
        now = dt_util.utcnow()
        today = dt_util.as_local(now).date()
        return is_dark(
            now,
            get_astral_event_date(self.hass, SUN_EVENT_SUNRISE, today),
            get_astral_event_date(self.hass, SUN_EVENT_SUNSET, today),
            sunrise_offset=self.sunrise_offset,
            sunset_offset=self.sunset_offset,
        )
