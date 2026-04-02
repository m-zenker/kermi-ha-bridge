# Kermi HA Bridge

![Version](https://img.shields.io/badge/version-v0.8.1-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![Tests](https://img.shields.io/badge/tests-142%20passing-brightgreen) ![AppDaemon](https://img.shields.io/badge/AppDaemon-4.x-orange)

Local API bridge for the **Kermi x-change dynamic** heat pump — full Home Assistant control, no cloud, no Modbus. Publishes 20+ sensor entities and provides six control services to manage heating circuits, DHW setpoint, heating curve, and energy modes.

## Prerequisites

- Kermi x-center Interfacemodul reachable on your LAN (port 80)
- AppDaemon 4.x installed and running alongside Home Assistant
- x-center web UI login (password on the unit sticker)

## Installation

Add this repo as a custom HACS repository (type: AppDaemon), then install from HACS.

For detailed setup and configuration, see the [full README](https://github.com/martin/kermi-ha-bridge).

## License

[MIT](LICENSE) © 2026 Martin Zenker
