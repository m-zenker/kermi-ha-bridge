# kermi-ha-bridge

*Local API bridge for the Kermi x-change heat pump — full Home Assistant control, no cloud, no Modbus.*

![Version](https://img.shields.io/badge/version-v0.9.6-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![Tests](https://img.shields.io/badge/tests-148%20passing-brightgreen) ![AppDaemon](https://img.shields.io/badge/AppDaemon-4.x-orange)

AppDaemon app that bridges the **Kermi x-change dynamic** heat pump (x-center Interfacemodul) to Home Assistant via its local HTTP API. Publishes 20+ `sensor.kermi_*` entities and six control services — with no Modbus adapter, no cloud dependency, and no third-party middleware.

> **Note:** Requires the Kermi x-center Interfacemodul reachable on your LAN and AppDaemon 4.x running alongside Home Assistant.

---

## Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Published entities](#published-entities)
- [Available services](#available-services)
- [Events fired](#events-fired)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

---

## Installation

### Via HACS (recommended)

> **Prerequisite:** [AppDaemon 4.x](https://appdaemon.readthedocs.io/) must be installed and running.
> This is an AppDaemon app — it is **not** a standard Home Assistant integration.

1. In HACS → three-dot menu → **Custom repositories**
2. Enter the repository URL and set the **Category to "AppDaemon"** — not "Integration" (that will fail with a compliance error)
3. Click **ADD**, then find and install "Kermi HA Bridge"
4. Register the app — add to `/config/appdaemon/apps/apps.yaml`:

   ```yaml
   kermi_bridge:
     module: kermi_bridge.kermi_bridge
     class: KermiBridge
     em_config_path: /config/appdaemon/apps/kermi_bridge/config.yaml
   ```

5. Copy `apps/kermi_bridge/config.yaml.example` to `/config/appdaemon/apps/kermi_bridge/config.yaml` and fill in your heat pump IP and password
6. Restart AppDaemon

### Manual installation

**1 — Clone and copy files**

```bash
git clone http://forgejo:3000/martin/kermi-ha-bridge.git
cp -r kermi-ha-bridge/apps/kermi_bridge /config/appdaemon/apps/
```

**2 — Register the app**

Add to `/config/appdaemon/apps/apps.yaml`:

```yaml
kermi_bridge:
  module: kermi_bridge.kermi_bridge
  class: KermiBridge
  em_config_path: /config/appdaemon/apps/kermi_bridge/config.yaml
```

**3 — Create the config file**

Create `/config/appdaemon/apps/kermi_bridge/config.yaml`:

```yaml
kermi_bridge:
  host: 192.168.1.121        # x-center IP address
  password: "1234"           # web UI password (see unit sticker)
  # device_id: "67b4e4ca-…" # optional; auto-discovered if omitted
  poll_interval_s: 30        # minimum 10
  max_failures: 5            # failures before connection_error event fires
  timeout_s: 10
  circuits: [MK1, MK2]      # heating circuits to control; valid: MK1, MK2, HK
```

> `device_id` can be omitted — the bridge discovers it automatically on first connect.
> To find it manually: `GET http://<ip>/api/Device/GetAllDevices/00000000-0000-0000-0000-000000000000`

**4 — Restart AppDaemon and verify**

Restart AppDaemon, then confirm in **HA Developer Tools → States**:

- [ ] `sensor.kermi_bridge_status` = `ok`
- [ ] `sensor.kermi_outside_temp` shows a plausible value (not `unavailable`)
- [ ] All `sensor.kermi_*` entities present and updating

Test a service call in **Developer Tools → Services**:

```yaml
service: kermi_bridge/set_energy_mode
data:
  mode: COMFORT
```

Confirm `sensor.kermi_energy_mode_mk1` changes to `COMFORT`.

---

## Prerequisites

- Kermi x-center reachable from the HA host on port 80
- x-center web UI login works (password is the last 4 chars of the serial number by default)
- AppDaemon installed and running

Verify network access from the HA host:

```bash
nc -zv <ip> 80
```

---

## Published entities

| Entity | Description |
|---|---|
| `sensor.kermi_outside_temp` | Outside temperature (°C) |
| `sensor.kermi_outside_temp_avg` | Average outside temperature (°C) |
| `sensor.kermi_flow_temp_mk1/mk2` | Flow temperatures, circuits MK1/MK2 (°C) |
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
| `sensor.kermi_energy_mode_mk1/mk2/hk` | EnergyMode per circuit (ECO/NORMAL/COMFORT/CUSTOM) |
| `sensor.kermi_bridge_status` | Bridge health (`ok` / `unavailable` / `auth_error`) |

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
| `sensor.kermi_bridge_status` = `auth_error` | Wrong password or x-center blocked the session — check unit sticker |
| `sensor.kermi_bridge_status` = `unavailable` | Host unreachable; check IP and `nc -zv <ip> 80` |
| All sensors `unavailable` after poll | Device ID mismatch; try omitting `device_id` to force auto-discovery |
| Bridge not appearing in HA at all | AppDaemon log error — check `em_config_path` in `apps.yaml` is absolute and file exists |

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
