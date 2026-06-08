# kermi-ha-bridge

*Local API bridge for the Kermi x-change heat pump — full Home Assistant control, no cloud, no Modbus.*

![Version](https://img.shields.io/badge/version-v0.11.0-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![Tests](https://img.shields.io/badge/tests-190%20passing-brightgreen) ![AppDaemon](https://img.shields.io/badge/AppDaemon-4.x-orange)

AppDaemon app that bridges the **Kermi x-change dynamic** heat pump (x-center Interfacemodul) to Home Assistant via its local HTTP API. Publishes 20+ `sensor.kermi_*` entities and six control services — with no Modbus adapter, no cloud dependency, and no third-party middleware.

Works with both firmware families:
- **Classic** (`HP_*` GUIDs) — original x-change dynamic firmware; 20 sensors
- **Rubin / x-change dynamic pro** (`Rubin_*` / `BufferSystem_*` GUIDs) — newer firmware; 26 sensors including 6 additional heat pump metrics

The bridge auto-detects firmware at startup via WellKnownName resolution and selects the correct datapoint GUIDs automatically. No manual configuration is needed for firmware type.

> **Note:** Requires the Kermi x-center Interfacemodul reachable on your LAN and AppDaemon 4.x running alongside Home Assistant.

---

## Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
  - [Via HACS (recommended)](#via-hacs-recommended)
  - [Manual installation](#manual-installation)
- [Config reference](#config-reference)
- [Verifying the installation](#verifying-the-installation)
- [Firmware variants](#firmware-variants)
- [Published entities](#published-entities)
- [Available services](#available-services)
- [Events fired](#events-fired)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

---

## Prerequisites

Before installing, confirm:

- **Kermi x-center Interfacemodul** is reachable from the HA host on port 80.
  Check with: `nc -zv <ip> 80`
- **x-center web UI password** — shown on the unit sticker (typically the last 4 characters of the serial number). Confirm by logging in to `http://<ip>` in a browser.
- **AppDaemon 4.x** is installed and running alongside Home Assistant.
- *(Optional)* **MQTT broker** — only needed if you want MQTT Discovery entities. The Mosquitto broker add-on works. If you use AppDaemon's native `set_state()` path, no MQTT broker is required.

---

## Installation

### Via HACS (recommended)

> **AppDaemon must be installed first.** This is an AppDaemon app — it is **not** a standard Home Assistant integration.

**Step 1 — Add the custom repository**

1. In HACS → three-dot menu → **Custom repositories**
2. Paste the repository URL
3. Set the **Category to "AppDaemon"** — not "Integration" (that will fail with a compliance error)
4. Click **ADD**, then find and install "Kermi HA Bridge"

**Step 2 — Register the app in `apps.yaml`**

Add to `/config/appdaemon/apps/apps.yaml`:

```yaml
kermi_bridge:
  module: kermi_bridge.kermi_bridge
  class: KermiBridge
  em_config_path: /config/appdaemon/apps/kermi_bridge/config.yaml
  # mqtt_discovery: true   # Uncomment to enable MQTT Discovery (requires MQTT broker)
```

**Step 3 — Create the config file**

Copy `apps/kermi_bridge/config.yaml.example` to `/config/appdaemon/apps/kermi_bridge/config.yaml`, then edit it:

```yaml
kermi_bridge:
  host: 192.168.1.121        # x-center IP address
  password: "1234"           # web UI password (see unit sticker)
  poll_interval_s: 30
  circuits: [MK1, MK2]
```

See [Config reference](#config-reference) for all options.

**Step 4 — Restart AppDaemon**

Then verify: see [Verifying the installation](#verifying-the-installation).

---

### Manual installation

**Step 1 — Copy files**

> **Important:** Copy only the `apps/kermi_bridge/` subdirectory — not the repository root. The `kermi_bridge/` directory (underscored, not hyphenated) must sit directly inside AppDaemon's `app_dir`. Find it by checking the AppDaemon log for the `Import paths:` line.

```bash
git clone <repo-url>
# Common locations for <apps_dir>:
#   /root/addon_configs/a0d7b954_appdaemon/apps  (AppDaemon add-on default)
#   /config/appdaemon/apps                        (if app_dir points to HA config)
cp -r kermi-ha-bridge/apps/kermi_bridge <apps_dir>/
```

**Step 2 — Register the app**

Add to `<apps_dir>/apps.yaml`:

```yaml
kermi_bridge:
  module: kermi_bridge.kermi_bridge
  class: KermiBridge
  em_config_path: <apps_dir>/kermi_bridge/config.yaml
  # mqtt_discovery: true   # Uncomment to enable MQTT Discovery (requires MQTT broker)
```

**Step 3 — Create the config file**

Create `<apps_dir>/kermi_bridge/config.yaml`:

```yaml
kermi_bridge:
  host: 192.168.1.121
  password: "1234"
  poll_interval_s: 30
  circuits: [MK1, MK2]
```

See [Config reference](#config-reference) for all options.

**Step 4 — Restart AppDaemon**

Then verify: see [Verifying the installation](#verifying-the-installation).

---

## Config reference

All keys go under the `kermi_bridge:` mapping in `config.yaml`.

| Key | Required | Default | Description |
|---|---|---|---|
| `host` | **yes** | — | x-center IP address (e.g. `192.168.1.121`) |
| `password` | **yes** | — | Web UI password (see unit sticker) |
| `circuits` | **yes** | — | Heating circuits to expose; valid values: `MK1`, `MK2`, `HK` |
| `poll_interval_s` | no | `30` | Poll interval in seconds (minimum `10`) |
| `max_failures` | no | `5` | Consecutive failures before `kermi_bridge_connection_error` fires |
| `timeout_s` | no | `10` | Per-request HTTP timeout in seconds |
| `device_id` | no | auto | x-center device GUID — omit to let the bridge discover it |

The following keys go in `apps.yaml` (not `config.yaml`), under the `kermi_bridge:` app registration:

| Key | Default | Description |
|---|---|---|
| `mqtt_discovery` | `false` | Set to `true` to publish MQTT Discovery messages. Requires an MQTT broker configured in AppDaemon. When `false`, the bridge uses AppDaemon's `set_state()` API instead. |
| `mqtt_discovery_prefix` | `homeassistant` | MQTT discovery topic prefix. Change only if your broker uses a non-standard prefix. |

> **`device_id`:** Omit this in almost all cases — the bridge discovers it from the x-center at startup. To find it manually: `GET http://<ip>/api/Device/GetAllDevices/00000000-0000-0000-0000-000000000000`

---

## Verifying the installation

After restarting AppDaemon, check the following in **HA Developer Tools → States**:

- [ ] `sensor.kermi_bridge_status` = `ok`
- [ ] `sensor.kermi_outside_temp` shows a plausible value (not `unavailable`)
- [ ] All `sensor.kermi_*` entities are present and updating

Test a service call in **Developer Tools → Services**:

```yaml
service: kermi_bridge/set_energy_mode
data:
  mode: COMFORT
```

Confirm `sensor.kermi_energy_mode_mk1` changes to `COMFORT`.

If using MQTT Discovery (`mqtt_discovery: true`), also verify that entities appear in the HA device registry under **"Kermi x-change"**.

---

## Firmware variants

The bridge supports two Kermi firmware families. It detects which one is running at startup by resolving WellKnownNames from the x-center API, then selects the correct datapoint GUIDs automatically.

| Firmware | GUID prefix | Sensors |
|---|---|---|
| Classic (original x-change dynamic) | `HP_*` | 20 (all classic sensors) |
| Rubin / x-change dynamic pro | `Rubin_*`, `BufferSystem_*` | 26 (20 classic + 6 Rubin-only) |

**Rubin-only sensors** (see [Published entities](#published-entities)) report `unavailable` on classic firmware — this is expected. They become active only when the x-center identifies as Rubin/BufferSystem firmware.

If GUID resolution fails at startup (e.g. the x-center is temporarily unreachable), the bridge falls back to the hardcoded classic GUIDs and logs a warning. Reconnect will retry resolution.

---

## Published entities

Sensors available on **all firmware**:

| Entity | Description |
|---|---|
| `sensor.kermi_outside_temp` | Outside temperature (°C) |
| `sensor.kermi_outside_temp_avg` | Average outside temperature (°C) |
| `sensor.kermi_flow_temp_mk1` / `_mk2` | Flow temperatures, circuits MK1/MK2 (°C) |
| `sensor.kermi_hot_water_temp` | DHW tank temperature (°C) |
| `sensor.kermi_buffer_temp` | Buffer temperature (°C) |
| `sensor.kermi_heating_setpoint` | Global heating setpoint (°C) |
| `sensor.kermi_setpoint_mk1` | MK1 circuit setpoint (°C) |
| `sensor.kermi_compressor_power_kw` | Compressor power draw (kW) |
| `sensor.kermi_heating_output_kw` | Heating output power (kW) |
| `sensor.kermi_cop` | Instantaneous COP |
| `sensor.kermi_cop_heating_avg` | Average heating COP |
| `sensor.kermi_scop` | Seasonal COP |
| `sensor.kermi_lifetime_electricity_kwh` | Lifetime electricity consumed (kWh) |
| `sensor.kermi_lifetime_heat_kwh` | Lifetime heat produced (kWh) |
| `sensor.kermi_electricity_heating_kwh` | Electricity used for heating (kWh) |
| `sensor.kermi_electricity_dhw_kwh` | Electricity used for DHW (kWh) |
| `sensor.kermi_hp_state` | Heat pump state (int; 1 = active) |
| `sensor.kermi_smart_grid_status` | Smart grid status (int; 2 = normal) |
| `binary_sensor.kermi_evu_lock` | EVU lock active |
| `sensor.kermi_energy_mode_mk1` / `_mk2` / `_hk` | EnergyMode per circuit (ECO/NORMAL/COMFORT/CUSTOM) |
| `sensor.kermi_bridge_status` | Bridge health (`ok` / `unavailable` / `auth_error`) |

Sensors available on **Rubin / x-change dynamic pro firmware only** (show `unavailable` on classic):

| Entity | Description |
|---|---|
| `sensor.kermi_is_defrosting` | Defrost cycle active (`true` / `false`) |
| `sensor.kermi_compressor_hours` | Compressor run hours (h, total_increasing) |
| `sensor.kermi_modulation_pct` | Compressor modulation (%) |
| `sensor.kermi_temp_spread` | Flow/return temperature spread (K) |
| `sensor.kermi_pv_available_power` | PV power available to the heat pump (kW) |
| `sensor.kermi_heater_power` | Electric heater power draw (kW) |

---

## Available services

| Service | Key parameters | Notes |
|---|---|---|
| `kermi_bridge/set_energy_mode` | `mode` (ECO/NORMAL/COMFORT/CUSTOM), `circuits` (optional) | Primary control lever for heat pump optimiser |
| `kermi_bridge/refresh` | — | Force an immediate poll |
| `kermi_bridge/set_dhw_setpoint` | `temperature` (float, °C) | Sets DHW tank setpoint (0–85 °C) |
| `kermi_bridge/trigger_dhw_oneshot` | — | Triggers a one-shot DHW heat cycle |
| `kermi_bridge/set_quiet_mode` | `enabled` (bool) | Enables/disables compressor quiet mode |
| `kermi_bridge/set_heating_curve_shift` | `shift` (int, K), `circuits` (optional) | Parallel-shifts the heating curve for the specified circuit(s) |

**EnergyMode values:**

| Mode | Effect | When to use |
|---|---|---|
| ECO (1) | 0 K offset — efficiency priority | Default |
| NORMAL (2) | 0 K offset | Normal operation |
| COMFORT (3) | +2 K offset — preheat buffer | Solar surplus available |
| CUSTOM (4) | Configurable offset | Reserved |

---

## Events fired

| Event | Meaning |
|---|---|
| `kermi_bridge_connection_error` | Consecutive failures reached `max_failures`; polling continues |
| `kermi_bridge_auth_error` | Authentication permanently failed; polling stopped |

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `No module named 'kermi_bridge'` in AppDaemon log | Files are in the wrong directory or named with hyphens. Check the `Import paths:` line in the log to find the expected app dir, then verify `kermi_bridge/` (underscored) exists directly inside it. |
| `sensor.kermi_bridge_status` = `auth_error` | Wrong password or x-center blocked the session — check unit sticker |
| `sensor.kermi_bridge_status` = `unavailable` | Host unreachable; check IP and `nc -zv <ip> 80` |
| All sensors `unavailable` after poll | Device ID mismatch; try omitting `device_id` to force auto-discovery |
| Rubin-only sensors always `unavailable` | x-center is running classic firmware — this is expected; those sensors only exist on Rubin/x-change dynamic pro firmware |
| Bridge not appearing in HA at all | AppDaemon log error — check `em_config_path` in `apps.yaml` is an absolute path and the file exists |
| MQTT entities not appearing | `mqtt_discovery: true` is set but no MQTT broker is configured in AppDaemon — check AppDaemon's `appdaemon.yaml` for a `plugins.MQTT` block |

AppDaemon log location: `/config/appdaemon/logs/appdaemon.log`

---

## Development

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```

---

## Licence

[MIT](LICENSE) © 2026 Martin Zenker
