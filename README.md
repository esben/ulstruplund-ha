# Ulstruplund Home Assistant integration

Custom integration replacing the pyscript automations in
`ulstruplund-ha-pyscript`. Logic lives here; configuration lives in
Home Assistant's `configuration.yaml`.

## Floor heating

Each entry becomes a `climate` entity that opens/closes a valve to keep a
floor at the setpoint. The setpoint and on/off mode can be changed from the UI
and are restored across restarts.

```yaml
ulstruplund:
  floor_heating:
    - id: floor_thermostat_1
      name: Office floor loop 1
      floor_temp: sensor.office_rtd_floor_temperature_1
      valve: valve.office_floor_heating_valves_floor_loop_1
      inflow_temp: sensor.office_rtd_floor_inflow_temperature
      outflow_temp: sensor.office_rtd_floor_outflow_temperature
      setpoint: 32
    - id: floor_thermostat_2
      name: Office floor loop 2
      floor_temp: sensor.office_rtd_floor_temperature_2
      valve: valve.office_floor_heating_valves_floor_loop_2
      inflow_temp: sensor.office_rtd_floor_inflow_temperature
      outflow_temp: sensor.office_rtd_floor_outflow_temperature
      setpoint: 32
```

| Option | Default | |
|---|---|---|
| `id` | required | Stable identifier, used for the entity's unique_id |
| `name` | `id` | Display name |
| `floor_temp` | required | Floor temperature sensor |
| `valve` | required | On/off valve |
| `inflow_temp`, `outflow_temp` | none | Both or neither; enables reverse-flow protection |
| `setpoint` | 32.0 | Initial setpoint (restored value wins after first start) |
| `min_temp` / `max_temp` | 15.0 / 35.0 | Setpoint range in the UI |
| `cold_tolerance` | 0.5 | Open when floor is this much below setpoint |
| `hot_tolerance` | 0.0 | Close when floor is more than this above setpoint |
| `reverse_flow_margin` | 0.5 | Close when outflow exceeds inflow by more than this |

The valve is closed when the thermostat is off or the floor sensor is
unavailable. The `control_reason` attribute shows why the last decision was
made.

## Lights

Each configured light gets a controller that turns it on/off in response to
remote buttons, motion sensors and timeouts:

- Turned on manually (remote button, or externally from the UI, an
  automation, or a wall switch powering the bulb back on): stays on until
  turned off, or `manual_timeout` has passed. Motion doesn't affect it.
- Turned on by motion: dimmed to `motion_brightness`, and turned off when none
  of its motion sensors detect motion anymore, or after `motion_timeout`.

A `sensor.<light>_control` entity shows the mode (`off`, `manual`, `motion`),
with the pending timeout as an attribute. Mode and timeout survive restarts.

```yaml
ulstruplund:
  lights:
    - light: light.office_wall_lamp_light
    - light: light.office_fireplace_lamp_light
      motion_brightness: 80
      motion_timeout: "00:15:00"

  remotes:
    - type: styrbar
      ieee: "04:0d:84:ff:fe:98:77:81"
      groups:
        - [light.office_wall_lamp_light]
        - [light.office_fireplace_lamp_light]

  motion_sensors:
    - sensor: binary_sensor.motion_sensor_1_motion
      lights: [light.office_fireplace_lamp_light]
```

Lights:

| Option | Default | |
|---|---|---|
| `light` | required | Light entity to control |
| `name` | light's object id | Name of the control sensor |
| `on_brightness` | 255 | Brightness when turned on by button |
| `motion_brightness` | 50 | Brightness when turned on by motion |
| `manual_timeout` | 3 hours | Turn off a manually turned on light after this |
| `motion_timeout` | 30 minutes | Turn off a motion-triggered light after this |

Remotes (IKEA STYRBAR via ZHA): left/right selects the previous/next group of
lights and flashes it; on/off turns the selected group on/off. Long presses
and the dim buttons are not used.

| Option | Default | |
|---|---|---|
| `type` | required | `styrbar` |
| `ieee` | required | IEEE address of the remote (from the ZHA device page) |
| `groups` | required | List of light groups, each a list of configured lights |

Motion sensors:

| Option | Default | |
|---|---|---|
| `sensor` | required | Motion binary sensor |
| `lights` | required | Configured lights to turn on |
| `only_when_dark` | true | Only turn lights on between sunset and sunrise |
| `sunset_offset` | -00:20:00 | Dark starts at sunset plus this |
| `sunrise_offset` | 00:20:00 | Dark ends at sunrise plus this |

Motion ending is always reported, so lights turned on by motion are still
turned off after it gets light.

## Deployment

Install via [HACS](https://hacs.xyz/):

1. HACS → ⋮ → Custom repositories → add
   `https://github.com/esben/ulstruplund-ha` with type "Integration".
2. Search for "Ulstruplund" in HACS and download it.
3. Add the `ulstruplund:` configuration to `configuration.yaml` and restart
   Home Assistant.

Updates appear in HACS like for any other integration; restart Home
Assistant after installing one.

Remove the corresponding pyscript code (`heat.py`, `light.py`) at the same
time, so the two don't fight over the same valves and lights.

## Development

```sh
uv sync
uv run pytest
```

`floor_control.py`, `light_control.py` and `styrbar.py` contain the control
logic with no Home Assistant imports. `climate.py`, `lights.py` and
`sensor.py` wire it to entity states, events and services. Tests in
`tests/test_climate.py` and `tests/test_lights.py` run against a real Home
Assistant core via `pytest-homeassistant-custom-component`.
