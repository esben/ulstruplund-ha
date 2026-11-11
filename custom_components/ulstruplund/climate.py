"""Floor heating thermostats driving an on/off valve."""

from datetime import datetime
import logging
from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.valve import ValveState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    CONF_ID,
    CONF_NAME,
    SERVICE_CLOSE_VALVE,
    SERVICE_OPEN_VALVE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import (
    ExtraStoredData,
    RestoredExtraData,
    RestoreEntity,
)
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import dt as dt_util

from .const import (
    CONF_COLD_TOLERANCE,
    CONF_CONTROL,
    CONF_COOLING_RATE,
    CONF_DEAD_TIME,
    CONF_FLOOR_TEMP,
    CONF_HEATING_RATE,
    CONF_HOT_TOLERANCE,
    CONF_INFLOW_TEMP,
    CONF_LAG,
    CONF_MAX_TEMP,
    CONF_MIN_SWITCH_INTERVAL,
    CONF_MIN_TEMP,
    CONF_OUTFLOW_TEMP,
    CONF_REVERSE_FLOW_MARGIN,
    CONF_SETPOINT,
    CONF_VALVE,
    CONTROL_PREDICTIVE,
    DOMAIN,
)
from .floor_control import Decision, Forecast, ValveCommand, decide
from .floor_model import STEP, FloorModel, ValveHistory, forecast

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up floor thermostats from the integration's YAML config."""
    if discovery_info is None:
        return
    async_add_entities(FloorThermostat(conf) for conf in discovery_info)


class FloorThermostat(ClimateEntity, RestoreEntity):
    """Thermostat keeping a floor at a setpoint by opening/closing a valve."""

    _attr_should_poll = False
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5

    def __init__(self, conf: ConfigType) -> None:
        self._attr_unique_id = f"{DOMAIN}_floor_{conf[CONF_ID]}"
        self._attr_name = conf.get(CONF_NAME, conf[CONF_ID])
        self._floor_temp_entity: str = conf[CONF_FLOOR_TEMP]
        self._valve_entity: str = conf[CONF_VALVE]
        self._inflow_entity: str | None = conf.get(CONF_INFLOW_TEMP)
        self._outflow_entity: str | None = conf.get(CONF_OUTFLOW_TEMP)
        self._cold_tolerance: float = conf[CONF_COLD_TOLERANCE]
        self._hot_tolerance: float = conf[CONF_HOT_TOLERANCE]
        self._reverse_flow_margin: float = conf[CONF_REVERSE_FLOW_MARGIN]
        self._attr_min_temp = conf[CONF_MIN_TEMP]
        self._attr_max_temp = conf[CONF_MAX_TEMP]
        self._attr_target_temperature = conf[CONF_SETPOINT]
        self._attr_hvac_mode = HVACMode.HEAT
        self._decision: Decision | None = None
        self._forecast: Forecast | None = None
        self._model: FloorModel | None = None
        if conf[CONF_CONTROL] == CONTROL_PREDICTIVE:
            self._model = FloorModel(
                dead_time=conf[CONF_DEAD_TIME],
                lag=conf[CONF_LAG],
                heating_rate=conf[CONF_HEATING_RATE],
                cooling_rate=conf[CONF_COOLING_RATE],
            )
        self._min_switch_interval = conf[CONF_MIN_SWITCH_INTERVAL]
        self._valves = ValveHistory()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        if (last_state := await self.async_get_last_state()) is not None:
            if (temp := last_state.attributes.get(ATTR_TEMPERATURE)) is not None:
                self._attr_target_temperature = float(temp)
            if last_state.state in self._attr_hvac_modes:
                self._attr_hvac_mode = HVACMode(last_state.state)
        if (extra := await self.async_get_last_extra_data()) is not None:
            self._valves = ValveHistory(
                [
                    (dt, is_open)
                    for when, is_open in extra.as_dict().get("valve_history", [])
                    if (dt := dt_util.parse_datetime(when)) is not None
                ]
            )
        if (state := self.hass.states.get(self._valve_entity)) is not None:
            self._record_valve(state.state, state.last_changed)

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self._watched_entities, self._async_state_changed
            )
        )
        if self._model is not None:
            # The forecast changes with time, not only with sensor updates
            self.async_on_remove(
                async_track_time_interval(
                    self.hass, self._async_tick, STEP, cancel_on_shutdown=True
                )
            )
        await self._async_control()

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        return RestoredExtraData(
            {
                "valve_history": [
                    (when.isoformat(), is_open)
                    for when, is_open in self._valves.as_list()
                ]
            }
        )

    @property
    def _watched_entities(self) -> list[str]:
        return [
            entity
            for entity in (
                self._floor_temp_entity,
                self._valve_entity,
                self._inflow_entity,
                self._outflow_entity,
            )
            if entity is not None
        ]

    @property
    def current_temperature(self) -> float | None:
        return self._float_state(self._floor_temp_entity)

    @property
    def hvac_action(self) -> HVACAction:
        if self._attr_hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if self._valve_state() == ValveState.OPEN:
            return HVACAction.HEATING
        return HVACAction.IDLE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "valve": self._valve_entity,
            "inflow_temperature": self._float_state(self._inflow_entity),
            "outflow_temperature": self._float_state(self._outflow_entity),
            "control_reason": self._decision.reason if self._decision else None,
            **(
                {
                    "forecast_max_if_closed": round(self._forecast.max_if_closed, 2),
                    "forecast_min_if_open": round(self._forecast.min_if_open, 2),
                }
                if self._forecast
                else {}
            ),
        }

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (hvac_mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            self._attr_hvac_mode = HVACMode(hvac_mode)
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            self._attr_target_temperature = float(temp)
        await self._async_control()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._attr_hvac_mode = hvac_mode
        await self._async_control()

    @callback
    def _async_state_changed(self, event: Event[EventStateChangedData]) -> None:
        if event.data["entity_id"] == self._valve_entity and (
            new_state := event.data["new_state"]
        ):
            self._record_valve(new_state.state, new_state.last_changed)
        self.hass.async_create_task(self._async_control())

    @callback
    def _async_tick(self, _now: datetime) -> None:
        self.hass.async_create_task(self._async_control())

    def _record_valve(self, state: str, when: datetime) -> None:
        if state in (ValveState.OPEN, ValveState.OPENING):
            self._valves.add(when, True)
        elif state in (ValveState.CLOSED, ValveState.CLOSING):
            self._valves.add(when, False)

    def _make_forecast(self, floor_temp: float | None) -> Forecast | None:
        if self._model is None or floor_temp is None:
            return None
        now = dt_util.utcnow()
        self._valves.prune(now - self._model.memory - self._min_switch_interval)
        valve_open = self._valves.state_at(now)
        if valve_open is None:
            return None
        last_change = self._valves.last_change
        return Forecast(
            valve_open=valve_open,
            max_if_closed=forecast(self._model, self._valves, now, floor_temp, False)[1],
            min_if_open=forecast(self._model, self._valves, now, floor_temp, True)[0],
            can_switch=last_change is None
            or now - last_change >= self._min_switch_interval,
        )

    async def _async_control(self) -> None:
        self._forecast = self._make_forecast(self.current_temperature)
        decision = decide(
            enabled=self._attr_hvac_mode == HVACMode.HEAT,
            setpoint=self._attr_target_temperature,
            floor_temp=self.current_temperature,
            inflow_temp=self._float_state(self._inflow_entity),
            outflow_temp=self._float_state(self._outflow_entity),
            cold_tolerance=self._cold_tolerance,
            hot_tolerance=self._hot_tolerance,
            reverse_flow_margin=self._reverse_flow_margin,
            forecast=self._forecast,
        )
        if decision != self._decision:
            _LOGGER.info("%s: %s (%s)", self.entity_id, decision.command, decision.reason)
        self._decision = decision
        self.async_write_ha_state()

        valve_state = self._valve_state()
        if valve_state is None:
            _LOGGER.debug("%s: %s unavailable", self.entity_id, self._valve_entity)
            return
        if decision.command == ValveCommand.OPEN and valve_state not in (
            ValveState.OPEN,
            ValveState.OPENING,
        ):
            await self._async_call_valve(SERVICE_OPEN_VALVE)
        elif decision.command == ValveCommand.CLOSE and valve_state not in (
            ValveState.CLOSED,
            ValveState.CLOSING,
        ):
            await self._async_call_valve(SERVICE_CLOSE_VALVE)

    async def _async_call_valve(self, service: str) -> None:
        await self.hass.services.async_call(
            "valve",
            service,
            {ATTR_ENTITY_ID: self._valve_entity},
            blocking=False,
            context=self._context,
        )

    def _valve_state(self) -> str | None:
        state = self.hass.states.get(self._valve_entity)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        return state.state

    def _float_state(self, entity_id: str | None) -> float | None:
        if entity_id is None:
            return None
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        try:
            return float(state.state)
        except ValueError:
            return None
