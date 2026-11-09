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

## Deployment

Install via [HACS](https://hacs.xyz/):

1. HACS → ⋮ → Custom repositories → add
   `https://github.com/esben/ulstruplund-ha` with type "Integration".
2. Search for "Ulstruplund" in HACS and download it.
3. Add the `ulstruplund:` configuration to `configuration.yaml` and restart
   Home Assistant.

Updates appear in HACS like for any other integration; restart Home
Assistant after installing one.

Remove the corresponding pyscript code (`heat.py`) at the same time, so the
two don't fight over the same valves.

## Development

```sh
uv sync
uv run pytest
```

`floor_control.py` contains the control decision as a pure function with no
Home Assistant imports. `climate.py` wires it to entity states and services.
Tests in `tests/test_climate.py` run against a real Home Assistant core via
`pytest-homeassistant-custom-component`.
