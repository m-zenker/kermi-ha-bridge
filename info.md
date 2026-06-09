# Kermi HA Bridge

![Version](https://img.shields.io/badge/version-v0.12.0-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![Tests](https://img.shields.io/badge/tests-191%20passing-brightgreen) ![AppDaemon](https://img.shields.io/badge/AppDaemon-4.x-orange)

Local API bridge for the **Kermi x-change dynamic** heat pump — full Home Assistant control, no cloud, no Modbus. Publishes 20+ sensor entities (26 on Rubin/x-change dynamic pro firmware) and provides six control services to manage heating circuits, DHW setpoint, heating curve, and energy modes.

## Prerequisites

- Kermi x-center Interfacemodul reachable on your LAN (port 80)
- AppDaemon 4.x installed and running alongside Home Assistant
- x-center web UI password (shown on the unit sticker)

## Installation

This is an **AppDaemon app** — AppDaemon 4.x must already be installed alongside Home Assistant.

**Manual installation is recommended** — copy `apps/kermi_bridge/` into AppDaemon's `app_dir`, register the app in `apps.yaml`, and create the config file. See the README for full instructions.

HACS installation is also possible (experimental — HACS AppDaemon support can be unreliable): go to HACS → Custom repositories, paste the repo URL, set the **Category to "AppDaemon"** (not "Integration"), then install "Kermi HA Bridge".

## License

[MIT](LICENSE) © 2026 Martin Zenker
