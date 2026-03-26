"""AppDaemon app: Kermi x-center bridge.

Polls the Kermi heat pump via HTTP and publishes HA sensors + services.
All other subsystems (including energy_manager) interact with the heat pump
exclusively via these HA entities and services — never via KermiClient directly.
"""

from __future__ import annotations

import asyncio
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
from kermi_bridge.mqtt_mixin import MQTTMixin
from .config_loader import ConfigError, load_config

_LOGGER = logging.getLogger(__name__)

_ENERGY_MODE_NAMES = {e.name: e for e in EnergyMode}
_ENERGY_MODE_OPTIONS = [e.name for e in EnergyMode]

# Device block for MQTT Discovery
_KERMI_DEVICE = {
    "identifiers": ["em_kermi_bridge"],
    "name": "EM Kermi Bridge",
    "model": "AppDaemon App",
}

# All sensor entity IDs published by this app (legacy set_state path).
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

# MQTT sensor discovery config: (uid, name, unit, icon, device_class, state_class)
_SENSOR_DISCOVERY = [
    ("kermi_outside_temp",      "Kermi Outside Temperature",      "°C",  "mdi:thermometer",    "temperature", None),
    ("kermi_outside_temp_avg",  "Kermi Outside Temperature Avg",  "°C",  "mdi:thermometer",    "temperature", None),
    ("kermi_flow_temp_mk1",     "Kermi Flow Temperature MK1",     "°C",  "mdi:thermometer",    "temperature", None),
    ("kermi_flow_temp_mk2",     "Kermi Flow Temperature MK2",     "°C",  "mdi:thermometer",    "temperature", None),
    ("kermi_hot_water_temp",    "Kermi Hot Water Temperature",    "°C",  "mdi:thermometer",    "temperature", None),
    ("kermi_buffer_temp",       "Kermi Buffer Temperature",       "°C",  "mdi:thermometer",    "temperature", None),
    ("kermi_heating_setpoint",  "Kermi Heating Setpoint",         "°C",  "mdi:thermometer",    "temperature", None),
    ("kermi_setpoint_mk1",      "Kermi Setpoint MK1",             "°C",  "mdi:thermometer",    "temperature", None),
    ("kermi_compressor_power_kw", "Kermi Compressor Power",       "kW",  "mdi:lightning-bolt", "power",       "measurement"),
    ("kermi_heating_output_kw", "Kermi Heating Output",           "kW",  "mdi:heat-wave",      "power",       "measurement"),
    ("kermi_cop",               "Kermi COP",                      "",    "mdi:gauge",          None,          "measurement"),
    ("kermi_cop_heating_avg",   "Kermi COP Heating Avg",          "",    "mdi:gauge",          None,          "measurement"),
    ("kermi_scop",              "Kermi SCOP",                     "",    "mdi:gauge",          None,          "measurement"),
    ("kermi_lifetime_electricity_kwh", "Kermi Lifetime Electricity", "kWh", "mdi:lightning-bolt", "energy", "total_increasing"),
    ("kermi_lifetime_heat_kwh", "Kermi Lifetime Heat",            "kWh", "mdi:heat-wave",      "energy",      "total_increasing"),
    ("kermi_electricity_heating_kwh", "Kermi Electricity Heating","kWh", "mdi:lightning-bolt", "energy",      "total_increasing"),
    ("kermi_electricity_dhw_kwh", "Kermi Electricity DHW",        "kWh", "mdi:lightning-bolt", "energy",      "total_increasing"),
    ("kermi_hp_state",          "Kermi HP State",                 "",    "mdi:heat-pump",      None,          None),
    ("kermi_smart_grid_status", "Kermi Smart Grid Status",        "",    "mdi:transmission-tower", None,      None),
    # Note: kermi_bridge_status is published separately in _publish_mqtt_discovery()
    # because it requires a json_attrs_topic — do not add it here.
]


class KermiBridge(MQTTMixin, hass.Hass):
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
        self._loop = asyncio.get_event_loop()

        self._mqtt_setup(self.args, "kermi_bridge", _KERMI_DEVICE)
        if self._mqtt_enabled:
            self._publish_mqtt_discovery()
            self._subscribe_mqtt_commands()
            self._mqtt_publish_availability("online")
            self._mqtt_cleanup_legacy(
                _ALL_SENSOR_ENTITIES + ["sensor.kermi_bridge_status"]
            )
        else:
            self._register_services()

        self._poll_handle = self.run_every(self._poll, "now", self._poll_interval_s)
        self.log(
            f"KermiBridge initialized (poll every {self._poll_interval_s}s)",
            level="INFO",
        )

    def _publish_mqtt_discovery(self) -> None:
        # Scalar sensors
        for uid, name, unit, icon, dc, sc in _SENSOR_DISCOVERY:
            self._mqtt_publish_sensor_discovery(uid, name, unit, icon, dc, sc)

        # Bridge status with attributes
        self._mqtt_publish_sensor_discovery(
            "kermi_bridge_status", "Kermi Bridge Status",
            "", "mdi:bridge", None, None,
            json_attrs_topic=self._attrs_topic("kermi_bridge_status"),
        )

        # Binary sensor
        self._mqtt_publish_binary_sensor_discovery(
            "kermi_evu_lock", "Kermi EVU Lock", "mdi:lock", "lock"
        )

        # Energy mode selects: all 3 circuits are always published because every
        # Kermi circuit has an energy mode regardless of self._circuits config.
        # (Contrast with heating curve shift below, which is per configured circuit.)
        for circuit in ["mk1", "mk2", "hk"]:
            uid = f"kermi_energy_mode_{circuit}"
            self._mqtt_publish_select_discovery(
                uid,
                f"Kermi Energy Mode {circuit.upper()}",
                _ENERGY_MODE_OPTIONS,
                "mdi:tune",
            )

        # DHW setpoint number
        self._mqtt_publish_number_discovery(
            "kermi_dhw_setpoint", "Kermi DHW Setpoint",
            "°C", 0, 85, 0.5, "mdi:water-thermometer",
        )

        # Heating curve shift numbers: only for circuits in self._circuits config.
        # States are published optimistically after each successful set command.
        # HA shows "unknown" until the first command — the API does not return these
        # values in the regular poll (KermiSensors read-only snapshot).
        for circuit in self._circuits:
            uid = f"kermi_heating_curve_shift_{circuit.lower()}"
            self._mqtt_publish_number_discovery(
                uid,
                f"Kermi Heating Curve Shift {circuit.upper()}",
                "", -5, 5, 1, "mdi:chart-line",
            )

        # Quiet mode switch
        self._mqtt_publish_switch_discovery(
            "kermi_quiet_mode", "Kermi Quiet Mode", "mdi:volume-off"
        )

        # Buttons
        self._mqtt_publish_button_discovery(
            "kermi_dhw_oneshot", "Kermi DHW Oneshot", "mdi:water-boiler"
        )
        self._mqtt_publish_button_discovery(
            "kermi_refresh", "Kermi Refresh", "mdi:refresh"
        )

    def _subscribe_mqtt_commands(self) -> None:
        # Energy mode selects
        for circuit in ["mk1", "mk2", "hk"]:
            uid = f"kermi_energy_mode_{circuit}"
            self._mqtt_subscribe_command(
                "select", uid,
                lambda event, data, kwargs, c=circuit: self._on_cmd_energy_mode(c, data),
            )

        # DHW setpoint
        self._mqtt_subscribe_command(
            "number", "kermi_dhw_setpoint",
            lambda event, data, kwargs: self._on_cmd_dhw_setpoint(data),
        )

        # Heating curve shift per configured circuit
        for circuit in self._circuits:
            uid = f"kermi_heating_curve_shift_{circuit.lower()}"
            self._mqtt_subscribe_command(
                "number", uid,
                lambda event, data, kwargs, c=circuit: self._on_cmd_heating_curve_shift(c, data),
            )

        # Quiet mode switch
        self._mqtt_subscribe_command(
            "switch", "kermi_quiet_mode",
            lambda event, data, kwargs: self._on_cmd_quiet_mode(data),
        )

        # Buttons
        self._mqtt_subscribe_command(
            "button", "kermi_dhw_oneshot",
            lambda event, data, kwargs: self._on_cmd_dhw_oneshot(data),
        )
        self._mqtt_subscribe_command(
            "button", "kermi_refresh",
            lambda event, data, kwargs: self._on_cmd_refresh(data),
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
            self._set_bridge_status("auth_error")
            self.fire_event("kermi_bridge_auth_error", message=str(exc))
            self._polling_active = False
            return
        except KermiConnectionError as exc:
            self._consecutive_failures += 1
            self.log(
                f"KermiBridge connection error #{self._consecutive_failures}: {exc}",
                level="WARNING",
            )
            self._set_bridge_status("unavailable")
            if self._consecutive_failures == self._max_failures:
                self.fire_event(
                    "kermi_bridge_connection_error",
                    message=str(exc),
                    consecutive_failures=self._consecutive_failures,
                )
            return

        self._consecutive_failures = 0
        self._publish_sensors(sensors)
        self._set_bridge_status("ok")

    def _set_bridge_status(self, state: str) -> None:
        if self._mqtt_enabled:
            self._mqtt_set_sensor_raw("kermi_bridge_status", state)
            self._mqtt_publish_sensor_attributes("kermi_bridge_status", self._status_attrs())
        else:
            self.set_state(
                "sensor.kermi_bridge_status",
                state=state,
                attributes=self._status_attrs(),
            )

    def _status_attrs(self) -> dict:
        return {
            "last_poll": datetime.now(timezone.utc).isoformat(),
            "consecutive_failures": self._consecutive_failures,
            "poll_interval_s": self._poll_interval_s,
        }

    def _publish_sensors(self, sensors: KermiSensors) -> None:
        if self._mqtt_enabled:
            self._mqtt_publish_sensors(sensors)
        else:
            self._set_state_publish_sensors(sensors)

    def _mqtt_publish_sensors(self, sensors: KermiSensors) -> None:
        self._mqtt_publish_availability("online")

        simple = [
            ("kermi_outside_temp",             sensors.outside_temp),
            ("kermi_outside_temp_avg",         sensors.outside_temp_avg),
            ("kermi_flow_temp_mk1",            sensors.flow_temp_mk1),
            ("kermi_flow_temp_mk2",            sensors.flow_temp_mk2),
            ("kermi_hot_water_temp",           sensors.hot_water_temp),
            ("kermi_buffer_temp",              sensors.buffer_temp),
            ("kermi_heating_setpoint",         sensors.heating_setpoint),
            ("kermi_setpoint_mk1",             sensors.setpoint_mk1),
            ("kermi_compressor_power_kw",      sensors.compressor_power_kw),
            ("kermi_heating_output_kw",        sensors.heating_output_kw),
            ("kermi_cop",                      sensors.cop),
            ("kermi_cop_heating_avg",          sensors.cop_heating_avg),
            ("kermi_scop",                     sensors.scop),
            ("kermi_lifetime_electricity_kwh", sensors.lifetime_electricity_kwh),
            ("kermi_lifetime_heat_kwh",        sensors.lifetime_heat_kwh),
            ("kermi_electricity_heating_kwh",  sensors.electricity_heating_kwh),
            ("kermi_electricity_dhw_kwh",      sensors.electricity_dhw_kwh),
            ("kermi_hp_state",                 sensors.hp_state),
            ("kermi_smart_grid_status",        sensors.smart_grid_status),
        ]
        for uid, value in simple:
            if value is None:
                self._mqtt_set_sensor_raw(uid, "unavailable")
            else:
                self._mqtt_set_sensor(uid, value)

        # Binary sensor: ON/OFF for MQTT binary sensor
        evu = sensors.evu_status
        self._mqtt_set_sensor_raw(
            "kermi_evu_lock",
            "unavailable" if evu is None else ("ON" if evu else "OFF"),
        )

        # Energy mode selects
        for circuit, mode in [
            ("mk1", sensors.energy_mode_mk1),
            ("mk2", sensors.energy_mode_mk2),
            ("hk",  sensors.energy_mode_hk),
        ]:
            uid = f"kermi_energy_mode_{circuit}"
            if mode is None:
                self._mqtt_set_sensor_raw(uid, "unavailable")
            else:
                self._mqtt_set_sensor_raw(uid, mode.name)
                self._mqtt_publish_sensor_attributes(uid, {"mode_int": int(mode)})

    def _set_state_publish_sensors(self, sensors: KermiSensors) -> None:
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
        if self._mqtt_enabled:
            self._mqtt_publish_availability("offline")
        else:
            for entity_id in _ALL_SENSOR_ENTITIES:
                self.set_state(
                    entity_id,
                    state="unavailable",
                    attributes=_ENTITY_ATTRS.get(entity_id, {}),
                )

    # ── MQTT command handlers ──────────────────────────────────────────────────

    def _on_cmd_energy_mode(self, circuit: str, data: dict) -> None:
        payload = str(data.get("payload", "")).upper()
        mode = _ENERGY_MODE_NAMES.get(payload)
        if mode is None:
            self.log(f"set_energy_mode via MQTT: unknown mode '{payload}'", level="ERROR")
            return
        asyncio.run_coroutine_threadsafe(self._do_set_energy_mode(mode, [circuit.upper()]), self._loop)

    async def _do_set_energy_mode(self, mode: EnergyMode, circuits: list[str]) -> None:
        try:
            await self._client.set_energy_mode(mode, circuits)
        except Exception as exc:
            self.log(f"set_energy_mode failed: {exc}", level="ERROR")

    def _on_cmd_dhw_setpoint(self, data: dict) -> None:
        payload = data.get("payload", "")
        try:
            temp = float(payload)
        except (TypeError, ValueError):
            self.log(f"set_dhw_setpoint: invalid payload '{payload}'", level="ERROR")
            return
        if not (0 <= temp <= 85):
            self.log(f"set_dhw_setpoint: {temp} out of range [0–85]", level="ERROR")
            return
        asyncio.run_coroutine_threadsafe(self._do_set_dhw_setpoint(temp), self._loop)

    async def _do_set_dhw_setpoint(self, temp: float) -> None:
        try:
            await self._client.set_dhw_setpoint(temp)
            # Optimistic state update
            self._mqtt_set_sensor("kermi_dhw_setpoint", temp)
        except KermiError as exc:
            self.log(f"set_dhw_setpoint failed: {exc}", level="ERROR")

    def _on_cmd_dhw_oneshot(self, data: dict) -> None:
        asyncio.run_coroutine_threadsafe(self._do_trigger_dhw_oneshot(), self._loop)

    async def _do_trigger_dhw_oneshot(self) -> None:
        try:
            await self._client.trigger_dhw_oneshot()
        except KermiError as exc:
            self.log(f"trigger_dhw_oneshot failed: {exc}", level="ERROR")

    def _on_cmd_quiet_mode(self, data: dict) -> None:
        payload = str(data.get("payload", "")).upper()
        enabled = payload == "ON"
        asyncio.run_coroutine_threadsafe(self._do_set_quiet_mode(enabled), self._loop)

    async def _do_set_quiet_mode(self, enabled: bool) -> None:
        try:
            await self._client.set_quiet_mode(enabled)
            # Optimistic state update
            self._mqtt_set_sensor_raw("kermi_quiet_mode", "ON" if enabled else "OFF")
        except KermiError as exc:
            self.log(f"set_quiet_mode failed: {exc}", level="ERROR")

    def _on_cmd_heating_curve_shift(self, circuit: str, data: dict) -> None:
        payload = data.get("payload", "")
        try:
            shift = int(payload)
        except (TypeError, ValueError):
            self.log(
                f"set_heating_curve_shift: invalid payload '{payload}'", level="ERROR"
            )
            return
        if not (-5 <= shift <= 5):
            self.log(
                f"set_heating_curve_shift: {shift} out of range [-5, 5]", level="ERROR"
            )
            return
        asyncio.run_coroutine_threadsafe(self._do_set_heating_curve_shift(shift, circuit), self._loop)

    async def _do_set_heating_curve_shift(self, shift: int, circuit: str) -> None:
        try:
            await self._client.set_heating_curve_shift(shift, [circuit.upper()])
            # Optimistic state update
            uid = f"kermi_heating_curve_shift_{circuit.lower()}"
            self._mqtt_set_sensor(uid, shift)
        except KermiError as exc:
            self.log(f"set_heating_curve_shift failed: {exc}", level="ERROR")

    def _on_cmd_refresh(self, data: dict) -> None:
        asyncio.run_coroutine_threadsafe(self._poll({}), self._loop)

    # ── Legacy service handlers ────────────────────────────────────────────────

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
        if getattr(self, "_mqtt_enabled", False):
            self._mqtt_publish_availability("offline")
        if hasattr(self, "_client"):
            await self._client.close()
