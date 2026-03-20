"""AppDaemon app: Kermi x-center bridge.

Polls the Kermi heat pump via HTTP and publishes HA sensors + services.
All other subsystems interact with the heat pump exclusively via these
HA entities and services — never via KermiClient directly.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import appdaemon.plugins.hass.hassapi as hass

from kermi_bridge.kermi_client import (
    EnergyMode,
    KermiAuthError,
    KermiClient,
    KermiConnectionError,
    KermiError,
    KermiSensors,
)
from .config_loader import ConfigError, load_config

_LOGGER = logging.getLogger(__name__)

_ENERGY_MODE_NAMES = {e.name: e for e in EnergyMode}

# All sensor entity IDs published by this app.
_ALL_SENSOR_ENTITIES = [
    "sensor.kermi_outside_temp",
    "sensor.kermi_outside_temp_avg",
    "sensor.kermi_flow_temp_mk1",
    "sensor.kermi_flow_temp_mk2",
    "sensor.kermi_hot_water_temp",
    "sensor.kermi_buffer_temp",
    "sensor.kermi_heating_setpoint",
    "sensor.kermi_setpoint_mk1",
    "sensor.kermi_compressor_power_kw",
    "sensor.kermi_heating_output_kw",
    "sensor.kermi_cop",
    "sensor.kermi_cop_heating_avg",
    "sensor.kermi_scop",
    "sensor.kermi_lifetime_electricity_kwh",
    "sensor.kermi_lifetime_heat_kwh",
    "sensor.kermi_hp_state",
    "sensor.kermi_smart_grid_status",
    "binary_sensor.kermi_evu_lock",
    "sensor.kermi_energy_mode_mk1",
    "sensor.kermi_energy_mode_mk2",
    "sensor.kermi_energy_mode_hk",
    "sensor.kermi_electricity_heating_kwh",
    "sensor.kermi_electricity_dhw_kwh",
]

# Static HA attributes for each published entity (device_class, state_class, etc.).
# Used by both _publish_sensors() and _mark_all_unavailable() so attributes are
# preserved even when state transitions to "unavailable".
_ENTITY_ATTRS: dict[str, dict] = {
    "sensor.kermi_outside_temp":             {"device_class": "temperature", "unit_of_measurement": "°C"},
    "sensor.kermi_outside_temp_avg":         {"device_class": "temperature", "unit_of_measurement": "°C"},
    "sensor.kermi_flow_temp_mk1":            {"device_class": "temperature", "unit_of_measurement": "°C"},
    "sensor.kermi_flow_temp_mk2":            {"device_class": "temperature", "unit_of_measurement": "°C"},
    "sensor.kermi_hot_water_temp":           {"device_class": "temperature", "unit_of_measurement": "°C"},
    "sensor.kermi_buffer_temp":              {"device_class": "temperature", "unit_of_measurement": "°C"},
    "sensor.kermi_heating_setpoint":         {"device_class": "temperature", "unit_of_measurement": "°C"},
    "sensor.kermi_setpoint_mk1":             {"device_class": "temperature", "unit_of_measurement": "°C"},
    "sensor.kermi_compressor_power_kw":      {"device_class": "power", "state_class": "measurement", "unit_of_measurement": "kW"},
    "sensor.kermi_heating_output_kw":        {"device_class": "power", "state_class": "measurement", "unit_of_measurement": "kW"},
    "sensor.kermi_cop":                      {"state_class": "measurement"},
    "sensor.kermi_cop_heating_avg":          {"state_class": "measurement"},
    "sensor.kermi_scop":                     {"state_class": "measurement"},
    "sensor.kermi_lifetime_electricity_kwh": {"device_class": "energy", "state_class": "total_increasing", "unit_of_measurement": "kWh"},
    "sensor.kermi_lifetime_heat_kwh":        {"device_class": "energy", "state_class": "total_increasing", "unit_of_measurement": "kWh"},
    "sensor.kermi_hp_state":                 {},
    "sensor.kermi_smart_grid_status":        {},
    "binary_sensor.kermi_evu_lock":          {"device_class": "lock"},
    "sensor.kermi_electricity_heating_kwh":  {"device_class": "energy", "state_class": "total_increasing", "unit_of_measurement": "kWh"},
    "sensor.kermi_electricity_dhw_kwh":      {"device_class": "energy", "state_class": "total_increasing", "unit_of_measurement": "kWh"},
    # Energy-mode entities: {} static attrs; mode_int is added dynamically on success only.
    "sensor.kermi_energy_mode_mk1":          {},
    "sensor.kermi_energy_mode_mk2":          {},
    "sensor.kermi_energy_mode_hk":           {},
}


class KermiBridge(hass.Hass):
    """AppDaemon app: polls Kermi x-center and publishes HA sensors + services."""

    async def initialize(self) -> None:
        config_path = self.args.get(
            "em_config_path", "/config/apps/kermi_bridge/config.yaml"
        )
        try:
            cfg = load_config(config_path)
        except ConfigError as exc:
            self.log(f"KermiBridge config error: {exc}", level="ERROR")
            return

        kb = cfg["kermi_bridge"]
        self._poll_interval_s: int = kb["poll_interval_s"]
        self._max_failures: int = kb["max_failures"]
        self._circuits: list[str] = kb["circuits"]
        self._consecutive_failures: int = 0
        self._polling_active: bool = True

        self._client = KermiClient(
            host=kb["host"],
            password=kb["password"],
            device_id=kb.get("device_id"),
            timeout=kb["timeout_s"],
        )

        self._register_services()
        self._poll_handle = self.run_every(self._poll, "now", self._poll_interval_s)
        self.log(
            f"KermiBridge initialized (poll every {self._poll_interval_s}s)",
            level="INFO",
        )

    def _register_services(self) -> None:
        self.register_service(
            "kermi_bridge/set_energy_mode", self._svc_set_energy_mode
        )
        self.register_service(
            "kermi_bridge/set_dhw_setpoint", self._svc_set_dhw_setpoint
        )
        self.register_service(
            "kermi_bridge/trigger_dhw_oneshot", self._svc_trigger_dhw_oneshot
        )
        self.register_service(
            "kermi_bridge/set_quiet_mode", self._svc_set_quiet_mode
        )
        self.register_service(
            "kermi_bridge/set_heating_curve_shift", self._svc_set_heating_curve_shift
        )
        self.register_service("kermi_bridge/refresh", self._svc_refresh)

    async def _poll(self, kwargs: dict) -> None:
        if not self._polling_active:
            return

        try:
            sensors = await self._client.read_sensors()
        except KermiAuthError as exc:
            self.log(
                f"KermiBridge auth error — stopping poll: {exc}", level="ERROR"
            )
            self._mark_all_unavailable()
            self.set_state(
                "sensor.kermi_bridge_status",
                state="auth_error",
                attributes=self._status_attrs(),
            )
            self.fire_event("kermi_bridge_auth_error", message=str(exc))
            self._polling_active = False
            return
        except KermiConnectionError as exc:
            self._consecutive_failures += 1
            self.log(
                f"KermiBridge connection error #{self._consecutive_failures}: {exc}",
                level="WARNING",
            )
            self.set_state(
                "sensor.kermi_bridge_status",
                state="unavailable",
                attributes=self._status_attrs(),
            )
            if self._consecutive_failures == self._max_failures:
                self.fire_event(
                    "kermi_bridge_connection_error",
                    message=str(exc),
                    consecutive_failures=self._consecutive_failures,
                )
            return

        self._consecutive_failures = 0
        self._publish_sensors(sensors)
        self.set_state(
            "sensor.kermi_bridge_status",
            state="ok",
            attributes=self._status_attrs(),
        )

    def _status_attrs(self) -> dict:
        return {
            "last_poll": datetime.now(timezone.utc).isoformat(),
            "consecutive_failures": self._consecutive_failures,
            "poll_interval_s": self._poll_interval_s,
        }

    def _publish_sensors(self, sensors: KermiSensors) -> None:
        simple = [
            ("sensor.kermi_outside_temp",             sensors.outside_temp),
            ("sensor.kermi_outside_temp_avg",         sensors.outside_temp_avg),
            ("sensor.kermi_flow_temp_mk1",            sensors.flow_temp_mk1),
            ("sensor.kermi_flow_temp_mk2",            sensors.flow_temp_mk2),
            ("sensor.kermi_hot_water_temp",           sensors.hot_water_temp),
            ("sensor.kermi_buffer_temp",              sensors.buffer_temp),
            ("sensor.kermi_heating_setpoint",         sensors.heating_setpoint),
            ("sensor.kermi_setpoint_mk1",             sensors.setpoint_mk1),
            ("sensor.kermi_compressor_power_kw",      sensors.compressor_power_kw),
            ("sensor.kermi_heating_output_kw",        sensors.heating_output_kw),
            ("sensor.kermi_cop",                      sensors.cop),
            ("sensor.kermi_cop_heating_avg",          sensors.cop_heating_avg),
            ("sensor.kermi_scop",                     sensors.scop),
            ("sensor.kermi_lifetime_electricity_kwh", sensors.lifetime_electricity_kwh),
            ("sensor.kermi_lifetime_heat_kwh",        sensors.lifetime_heat_kwh),
            ("sensor.kermi_electricity_heating_kwh",  sensors.electricity_heating_kwh),
            ("sensor.kermi_electricity_dhw_kwh",      sensors.electricity_dhw_kwh),
            ("sensor.kermi_hp_state",                 sensors.hp_state),
            ("sensor.kermi_smart_grid_status",        sensors.smart_grid_status),
        ]
        for entity_id, value in simple:
            self.set_state(
                entity_id,
                state="unavailable" if value is None else str(value),
                attributes=_ENTITY_ATTRS.get(entity_id, {}),
            )

        # Binary sensor
        evu = sensors.evu_status
        self.set_state(
            "binary_sensor.kermi_evu_lock",
            state="unavailable" if evu is None else ("on" if evu else "off"),
            attributes=_ENTITY_ATTRS["binary_sensor.kermi_evu_lock"],
        )

        # Energy mode sensors
        for entity_id, mode in [
            ("sensor.kermi_energy_mode_mk1", sensors.energy_mode_mk1),
            ("sensor.kermi_energy_mode_mk2", sensors.energy_mode_mk2),
            ("sensor.kermi_energy_mode_hk", sensors.energy_mode_hk),
        ]:
            if mode is None:
                self.set_state(
                    entity_id,
                    state="unavailable",
                    attributes=_ENTITY_ATTRS.get(entity_id, {}),
                )
            else:
                self.set_state(
                    entity_id, state=mode.name, attributes={"mode_int": int(mode)}
                )

    def _mark_all_unavailable(self) -> None:
        for entity_id in _ALL_SENSOR_ENTITIES:
            self.set_state(
                entity_id,
                state="unavailable",
                attributes=_ENTITY_ATTRS.get(entity_id, {}),
            )

    async def _svc_set_energy_mode(
        self, namespace, domain, service, kwargs
    ) -> None:
        mode_str = str(kwargs.get("mode", "NORMAL")).upper()
        mode = _ENERGY_MODE_NAMES.get(mode_str)
        if mode is None:
            self.log(f"Unknown energy mode: '{mode_str}'", level="ERROR")
            return
        circuits = kwargs.get("circuits")
        if circuits is None:
            circuits = self._circuits
        elif len(circuits) == 0:
            self.log("set_energy_mode: circuits list is empty", level="ERROR")
            return
        try:
            await self._client.set_energy_mode(mode, circuits)
        except Exception as exc:
            self.log(f"set_energy_mode failed: {exc}", level="ERROR")

    async def _svc_set_dhw_setpoint(
        self, namespace, domain, service, kwargs
    ) -> None:
        temp = kwargs.get("temperature")
        if temp is None:
            self.log("set_dhw_setpoint: temperature is required", level="ERROR")
            return
        try:
            temp = float(temp)
        except (TypeError, ValueError):
            self.log(
                f"set_dhw_setpoint: invalid temperature: {temp!r}", level="ERROR"
            )
            return
        if not (0 <= temp <= 85):
            self.log(
                f"set_dhw_setpoint: {temp} out of range [0–85]", level="ERROR"
            )
            return
        try:
            await self._client.set_dhw_setpoint(temp)
        except KermiError as exc:
            self.log(f"set_dhw_setpoint failed: {exc}", level="ERROR")

    async def _svc_trigger_dhw_oneshot(
        self, namespace, domain, service, kwargs
    ) -> None:
        try:
            await self._client.trigger_dhw_oneshot()
        except KermiError as exc:
            self.log(f"trigger_dhw_oneshot failed: {exc}", level="ERROR")

    async def _svc_set_quiet_mode(
        self, namespace, domain, service, kwargs
    ) -> None:
        enabled = bool(kwargs.get("enabled", True))
        try:
            await self._client.set_quiet_mode(enabled)
        except KermiError as exc:
            self.log(f"set_quiet_mode failed: {exc}", level="ERROR")

    async def _svc_set_heating_curve_shift(
        self, namespace, domain, service, kwargs
    ) -> None:
        if "shift" not in kwargs:
            self.log("set_heating_curve_shift: shift is required", level="ERROR")
            return
        try:
            shift = int(kwargs["shift"])
        except (TypeError, ValueError):
            self.log(
                f"set_heating_curve_shift: invalid shift: {kwargs['shift']!r}",
                level="ERROR",
            )
            return
        if not (-5 <= shift <= 5):
            self.log(
                f"set_heating_curve_shift: {shift} out of range [-5, 5]", level="ERROR"
            )
            return
        circuits = kwargs.get("circuits")
        try:
            await self._client.set_heating_curve_shift(shift, circuits)
        except KermiError as exc:
            self.log(f"set_heating_curve_shift failed: {exc}", level="ERROR")

    async def _svc_refresh(self, namespace, domain, service, kwargs) -> None:
        await self._poll({})

    async def terminate(self) -> None:
        if hasattr(self, "_client"):
            await self._client.close()
