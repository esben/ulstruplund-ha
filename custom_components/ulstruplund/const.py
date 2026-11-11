"""Constants for the Ulstruplund integration."""

from datetime import timedelta

DOMAIN = "ulstruplund"

CONF_FLOOR_HEATING = "floor_heating"
CONF_FLOOR_TEMP = "floor_temp"
CONF_VALVE = "valve"
CONF_INFLOW_TEMP = "inflow_temp"
CONF_OUTFLOW_TEMP = "outflow_temp"
CONF_SETPOINT = "setpoint"
CONF_MIN_TEMP = "min_temp"
CONF_MAX_TEMP = "max_temp"
CONF_COLD_TOLERANCE = "cold_tolerance"
CONF_HOT_TOLERANCE = "hot_tolerance"
CONF_REVERSE_FLOW_MARGIN = "reverse_flow_margin"
CONF_CONTROL = "control"
CONF_DEAD_TIME = "dead_time"
CONF_LAG = "lag"
CONF_HEATING_RATE = "heating_rate"
CONF_COOLING_RATE = "cooling_rate"
CONF_MIN_SWITCH_INTERVAL = "min_switch_interval"
CONF_ADAPTIVE = "adaptive"
CONTROL_HYSTERESIS = "hysteresis"
CONTROL_PREDICTIVE = "predictive"

DEFAULT_SETPOINT = 32.0
DEFAULT_MIN_TEMP = 15.0
DEFAULT_MAX_TEMP = 35.0
DEFAULT_COLD_TOLERANCE = 0.5
DEFAULT_HOT_TOLERANCE = 0.0
DEFAULT_REVERSE_FLOW_MARGIN = 0.5
DEFAULT_MIN_SWITCH_INTERVAL = timedelta(minutes=30)

CONF_LIGHTS = "lights"
CONF_LIGHT = "light"
CONF_ON_BRIGHTNESS = "on_brightness"
CONF_MOTION_BRIGHTNESS = "motion_brightness"
CONF_MANUAL_TIMEOUT = "manual_timeout"
CONF_MOTION_TIMEOUT = "motion_timeout"

CONF_REMOTES = "remotes"
CONF_IEEE = "ieee"
CONF_GROUPS = "groups"
REMOTE_TYPE_STYRBAR = "styrbar"

CONF_MOTION_SENSORS = "motion_sensors"
CONF_SENSOR = "sensor"
CONF_ONLY_WHEN_DARK = "only_when_dark"
CONF_SUNRISE_OFFSET = "sunrise_offset"
CONF_SUNSET_OFFSET = "sunset_offset"

DEFAULT_ON_BRIGHTNESS = 255
DEFAULT_MOTION_BRIGHTNESS = 50
DEFAULT_MANUAL_TIMEOUT = timedelta(hours=3)
DEFAULT_MOTION_TIMEOUT = timedelta(minutes=30)
DEFAULT_SUNRISE_OFFSET = timedelta(minutes=20)
DEFAULT_SUNSET_OFFSET = timedelta(minutes=-20)
