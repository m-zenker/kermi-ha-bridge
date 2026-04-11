# Kermi HA Bridge

![Version](https://img.shields.io/badge/version-v0.9.6-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![Tests](https://img.shields.io/badge/tests-142%20passing-brightgreen) ![AppDaemon](https://img.shields.io/badge/AppDaemon-4.x-orange)

Local API bridge for the **Kermi x-change dynamic** heat pump — full Home Assistant control, no cloud, no Modbus. Publishes 20+ sensor entities and provides six control services to manage heating circuits, DHW setpoint, heating curve, and energy modes.

## Prerequisites

- Kermi x-center Interfacemodul reachable on your LAN (port 80)
- AppDaemon 4.x installed and running alongside Home Assistant
- x-center web UI login (password on the unit sticker)

## Installation

This is an **AppDaemon app** — AppDaemon 4.x must already be installed alongside Home Assistant.

To add via HACS: go to HACS → Custom repositories, paste the repo URL, and set the **Category to "AppDaemon"** (not "Integration" — that will fail). Then install "Kermi HA Bridge" and follow the README to register the app in `apps.yaml` and create the config file.

## License

[MIT](LICENSE) © 2026 Martin Zenker
