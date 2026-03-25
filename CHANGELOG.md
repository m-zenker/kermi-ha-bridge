# Changelog

All notable changes to kermi-ha-bridge are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

Versions align with the kermi_bridge subsystem releases in `ha-energy-manager`.

## [0.8.0] — 2026-03-25

### Added
- `LICENSE` — MIT licence file
- `README.md` — tagline, shields.io badges (version, licence, tests, AppDaemon), table of contents, polished intro paragraph, Licence section

---

## [0.4.0] — 2026-03-25

### Added
- `mqtt_mixin.py` — new `MQTTMixin` shared base class providing MQTT Discovery helpers (sensor/binary_sensor/button/number/select/switch discovery, state/attribute/availability publishing, command subscriptions, legacy cleanup); opt-in via `mqtt_discovery: true` in `apps.yaml`
- `kermi_bridge.py` — 6 MQTT control entities when MQTT Discovery is enabled: per-circuit energy mode selects (mk1/mk2/hk), DHW setpoint number (0–85°C), heating curve shift numbers per configured circuit, quiet mode switch, DHW oneshot button, refresh button; replaces AppDaemon services for HA-native entity control
- `kermi_client.py` / `kermi_bridge.py` — added `sensor.kermi_electricity_heating_kwh` and `sensor.kermi_electricity_dhw_kwh`; split electricity counters (heating-only / DHW-only) are now polled and published with `device_class: energy`, `state_class: total_increasing` so both can be added as individual device consumption entries in the HA Energy Dashboard

### Fixed
- `kermi_bridge.py` — `kermi_bridge_status` was published twice during MQTT Discovery init (once from `_SENSOR_DISCOVERY` list without attrs_topic, then again manually with attrs_topic); removed the duplicate entry from the list
- `kermi_bridge.py` — `import asyncio` moved from 6 inline handler sites to module level
- `kermi_bridge.py` — added `unit_of_measurement` to all `_ENTITY_ATTRS` entries that carry a `device_class`; HA 2026.3 rejects `set_state` calls for `power`, `temperature`, and `energy` device-class sensors that omit the unit, resulting in 400 errors on every poll
- `kermi_bridge.py` — added `state_class: measurement` to `sensor.kermi_compressor_power_kw` and `sensor.kermi_heating_output_kw`; HA 2026.3 also requires `state_class` for `device_class: power` sensors or the set_state call returns 400
- `kermi_bridge.py` — state values in `_publish_sensors` are now cast to `str` before calling `set_state`; HA 2026.3 rejects a JSON numeric `0` (but not the string `"0.0"`) for `device_class: power` + `state_class: measurement` sensors, causing persistent 400 errors whenever compressor or heating output power is zero
- `kermi_client.py` — `aiohttp.ClientSession` now created with `CookieJar(unsafe=True)`; aiohttp's default jar silently drops cookies from IP-address hosts (RFC compliance), so the `.AspNetCore.Cookie` set by the Kermi login response was never stored, causing every subsequent request to return 401 and halting polling

## [0.3.0] — 2026-03-17

### Added
- `kermi_client.py`: 6 new GUIDs in `_DP` and `_CIRCUIT_TO_CURVE_DP` for DHW and heating curve control
- `kermi_client.py`: `set_dhw_setpoint(temp)` — writes float °C to DHW setpoint datapoint
- `kermi_client.py`: `trigger_dhw_oneshot()` — writes bool True to DHW oneshot trigger
- `kermi_client.py`: `set_quiet_mode(enabled)` — enables/disables compressor quiet mode
- `kermi_client.py`: `set_heating_curve_shift(shift, circuits)` — parallel-shifts heating curve for MK1/MK2/HK
- `kermi_bridge.py`: all 4 previously stubbed service handlers now call the real client methods
- New tests: 14 `test_kermi_client.py` tests for the 4 new methods; 15 `test_kermi_bridge.py` tests replacing WARNING stubs

### Fixed
- `kermi_client.py`: `ExceptionData: null` in WriteValues error responses no longer causes `AttributeError` (all 5 error-format sites patched)

## [0.2.0] — 2026-03-17

### Fixed
- `kermi_bridge.py`: `terminate()` no longer crashes if `initialize()` failed before `_client` was set
- `kermi_client.py`: `_connected` flag now restored to `True` after inline 401 re-auth
- `kermi_bridge.py`: `sensor.kermi_setpoint_mk1` (polled but unpublished) is now included in `_ALL_SENSOR_ENTITIES` and published by `_publish_sensors()`
- `kermi_bridge.py`: `_mark_all_unavailable()` now passes static entity attributes so `device_class`/`state_class` are preserved on "unavailable" state
- `kermi_client.py`: `evu_status` parsing replaced double `_get()` call with new `_bool()` helper
- `kermi_bridge.py`: `run_every()` return value stored in `self._poll_handle`
- `kermi_bridge.py`: `_svc_set_energy_mode` now explicitly rejects an empty `circuits=[]` with an ERROR log
- `config_loader.py`: `_circuit_list` now rejects an empty circuit list
- `config_loader.py`: `circuits` default changed from mutable `["MK1","MK2"]` to tuple; `_circuit_list` always returns a `list`
- `test_kermi_client.py`: `_FakeSession` now has separate GET/POST response queues

### Added
- `_ENTITY_ATTRS` module-level dict in `kermi_bridge.py` — single source of truth for static HA entity attributes
- New tests: `test_terminate_without_client_does_not_raise`, `test_empty_circuits_logs_error`, `test_empty_circuit_list_rejected`, `test_connected_flag_true_after_401_reauth`

## [0.1.0] — 2026-03-17

### Added
- `apps/kermi_bridge/` — standalone AppDaemon app that bridges the Kermi x-center to HA
  - `config_loader.py` — voluptuous schema for `kermi_bridge:` config; validates host, password, poll_interval_s (≥10), circuits (MK1/MK2/HK), max_failures, timeout_s
  - `kermi_bridge.py` — `KermiBridge(hass.Hass)`: async poll loop, publishes 20 HA entities (`sensor.kermi_*`, `binary_sensor.kermi_evu_lock`, `sensor.kermi_bridge_status`), registers 6 services (`set_energy_mode`, `set_dhw_setpoint`, `trigger_dhw_oneshot`, `set_quiet_mode`, `set_heating_curve_shift`, `refresh`)
  - `kermi_client.py` — async HTTP client for the Kermi x-center local API: login/session management, `read_sensors()` returning a typed `KermiSensors` dataclass, `set_energy_mode()` for per-circuit control, automatic 401 re-auth
- `tests/test_kermi_bridge.py` — 40+ tests covering init, poll success/partial/errors, failure counting, recovery, all service handlers
- `tests/test_kermi_bridge_config.py` — schema tests: missing fields, defaults, range validation, invalid circuits, file errors
- `tests/test_kermi_client.py` — 15 tests covering connect/auth, sensor parsing, write payload, re-auth on 401, error cases
