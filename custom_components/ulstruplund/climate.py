"""Floor heating thermostats driving an on/off valve."""

from datetime import datetime, timedelta
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
    CONF_ADAPTIVE,
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
from .floor_model import STEP, FloorModel, History, RateEstimator, forecast

_LOGGER = logging.getLogger(__name__)

# How long the floor sensor may be unavailable before closing the valve
SENSOR_GRACE = timedelta(minutes=5)


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
        self._valves: History[bool] = History()
        self._estimator: RateEstimator | None = None
        if self._model is not None and conf[CONF_ADAPTIVE]:
            self._estimator = RateEstimator(self._model)
        self._temps: History[float | None] = History()
        self._last_sample: datetime | None = None
        self._floor_unavailable_since: datetime | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        if (last_state := await self.async_get_last_state()) is not None:
            if (temp := last_state.attributes.get(ATTR_TEMPERATURE)) is not None:
                self._attr_target_temperature = float(temp)
            if last_state.state in self._attr_hvac_modes:
                self._attr_hvac_mode = HVACMode(last_state.state)
        if (extra := await self.async_get_last_extra_data()) is not None:
            data = extra.as_dict()
            self._valves = _parse_history(data.get("valve_history", []))
            self._temps = _parse_history(data.get("floor_temp_history", []))
            if self._estimator is not None and "estimator" in data:
                self._estimator.restore(data["estimator"])
        if (state := self.hass.states.get(self._valve_entity)) is not None:
            self._record_valve(state.state, state.last_changed)
        if (state := self.hass.states.get(self._floor_temp_entity)) is not None:
            self._temps.add(state.last_changed, self.current_temperature)

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self._watched_entities, self._async_state_changed
            )
        )
        # The forecast changes with time, not only with sensor updates, and an
        # unavailable sensor needs to be acted on after SENSOR_GRACE
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._async_tick, STEP, cancel_on_shutdown=True
            )
        )
        await self._async_control()

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        data: dict[str, Any] = {
            "valve_history": _dump_history(self._valves),
            "floor_temp_history": _dump_history(self._temps),
        }
        if self._estimator is not None:
            data["estimator"] = self._estimator.as_dict()
        return RestoredExtraData(data)

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
            **(
                {
                    "estimated_heating_rate": round(self._estimator.heating_rate, 3),
                    "estimated_cooling_rate": round(self._estimator.cooling_rate, 3),
                }
                if self._estimator
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
        if event.data["entity_id"] == self._floor_temp_entity and (
            new_state := event.data["new_state"]
        ):
            self._temps.add(new_state.last_changed, self.current_temperature)
        self.hass.async_create_task(self._async_control())

    @callback
    def _async_tick(self, now: datetime) -> None:
        if self._estimator is not None and (
            self._last_sample is None
            or now - self._last_sample >= RateEstimator.INTERVAL
        ):
            self._last_sample = now
            if self._estimator.sample(self._valves, self._temps, now):
                _LOGGER.debug(
                    "%s: estimated heating %.3f, cooling %.3f °C/h",
                    self.entity_id,
                    self._estimator.heating_rate,
                    self._estimator.cooling_rate,
                )
        self.hass.async_create_task(self._async_control())

    def _record_valve(self, state: str, when: datetime) -> None:
        if state in (ValveState.OPEN, ValveState.OPENING):
            self._valves.add(when, True)
        elif state in (ValveState.CLOSED, ValveState.CLOSING):
            self._valves.add(when, False)

    def _make_forecast(self, floor_temp: float | None) -> Forecast | None:
        if self._model is None or floor_temp is None:
            return None
        model = self._estimator.model if self._estimator else self._model
        now = dt_util.utcnow()
        keep = max(self._min_switch_interval, RateEstimator.WINDOW)
        self._valves.prune(now - model.memory - keep)
        self._temps.prune(now - RateEstimator.WINDOW)
        valve_open = self._valves.state_at(now)
        if valve_open is None:
            return None
        last_change = self._valves.last_change
        return Forecast(
            valve_open=valve_open,
            max_if_closed=forecast(model, self._valves, now, floor_temp, False)[1],
            min_if_open=forecast(model, self._valves, now, floor_temp, True)[0],
            can_switch=last_change is None
            or now - last_change >= self._min_switch_interval,
        )

    async def _async_control(self) -> None:
        now = dt_util.utcnow()
        if self.current_temperature is None:
            if self._floor_unavailable_since is None:
                self._floor_unavailable_since = now
        else:
            self._floor_unavailable_since = None
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
            wait_for_sensor=self._floor_unavailable_since is not None
            and now - self._floor_unavailable_since < SENSOR_GRACE,
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


def _parse_history[T](data: list) -> History[T]:
    return History(
        [
            (when, value)
            for timestamp, value in data
            if (when := dt_util.parse_datetime(timestamp)) is not None
        ]
    )


def _dump_history(history: History) -> list:
    return [(when.isoformat(), value) for when, value in history.as_list()]
