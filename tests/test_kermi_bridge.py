"""Tests for the kermi_bridge AppDaemon app.

All HTTP calls are mocked; no live device needed.
appdaemon is installed but KermiBridge is instantiated via __new__ to avoid
AppDaemon's infrastructure __init__. Hass methods are monkey-patched per instance.
"""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

# Stub appdaemon before importing kermi_bridge (not installed in test env).
# Use types.ModuleType (not MagicMock) so attribute access is plain and
# our _FakeHass class survives without being shadowed by auto-mock creation.
class _FakeHass:
    """Minimal stand-in for appdaemon.plugins.hass.hassapi.Hass in tests."""


_hassapi_mod = types.ModuleType("appdaemon.plugins.hass.hassapi")
_hassapi_mod.Hass = _FakeHass
sys.modules["appdaemon"] = types.ModuleType("appdaemon")
sys.modules["appdaemon.plugins"] = types.ModuleType("appdaemon.plugins")
sys.modules["appdaemon.plugins.hass"] = types.ModuleType("appdaemon.plugins.hass")
sys.modules["appdaemon.plugins.hass.hassapi"] = _hassapi_mod

from kermi_bridge.kermi_bridge import KermiBridge, _ALL_SENSOR_ENTITIES  # noqa: E402
from kermi_bridge.kermi_client import (  # noqa: E402
    EnergyMode,
    KermiAuthError,
    KermiConnectionError,
    KermiError,
    KermiSensors,
    KermiWriteError,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

MINIMAL_KB_CONFIG = {
    "kermi_bridge": {
        "host": "192.168.1.100",
        "password": "test1234",
        "poll_interval_s": 30,
        "max_failures": 3,
        "timeout_s": 10,
        "circuits": ["MK1", "MK2"],
    }
}


def _make_sensors(**overrides) -> KermiSensors:
    defaults = dict(
        outside_temp=5.0,
        outside_temp_avg=4.5,
        flow_temp_mk1=35.0,
        flow_temp_mk2=33.0,
        hot_water_temp=55.0,
        buffer_temp=40.0,
        heating_setpoint=20.0,
        compressor_power_kw=1.5,
        heating_output_kw=4.5,
        cop=3.0,
        cop_heating_avg=3.1,
        scop=3.5,
        lifetime_electricity_kwh=1000.0,
        lifetime_heat_kwh=3500.0,
        electricity_heating_kwh=800.0,
        electricity_dhw_kwh=200.0,
        hp_state=1,
        smart_grid_status=0,
        evu_status=False,
        energy_mode_mk1=EnergyMode.NORMAL,
        energy_mode_mk2=EnergyMode.NORMAL,
        energy_mode_hk=EnergyMode.ECO,
    )
    defaults.update(overrides)
    return KermiSensors(**defaults)


def _make_bridge_instance(config_path: str, extra_args: dict | None = None) -> KermiBridge:
    """Create a KermiBridge without calling Hass.__init__."""
    b = KermiBridge.__new__(KermiBridge)
    b.args = {"em_config_path": config_path, **(extra_args or {})}

    # Tracking collections
    b.set_state_calls: list = []
    b.call_service_calls: list = []
    b.listen_event_calls: list = []
    b.registered_services: dict = {}
    b.run_every_calls: list = []
    b.fire_event_calls: list = []
    b._log_output: list = []

    # Monkey-patch Hass methods
    def _set_state(entity_id, **kwargs):
        b.set_state_calls.append({"entity_id": entity_id, **kwargs})

    def _call_service(service, **kwargs):
        b.call_service_calls.append({"service": service, **kwargs})

    def _listen_event(callback, event, **kwargs):
        b.listen_event_calls.append({"callback": callback, "event": event, **kwargs})

    def _register_service(name, callback):
        b.registered_services[name] = callback

    def _run_every(callback, start, interval, **kwargs):
        b.run_every_calls.append(
            {"callback": callback, "start": start, "interval": interval}
        )
        return "mock-handle"

    def _fire_event(event, **kwargs):
        b.fire_event_calls.append({"event": event, **kwargs})

    def _log(msg, level="INFO"):
        b._log_output.append(f"[{level}] {msg}")

    b.set_state = _set_state
    b.call_service = _call_service
    b.listen_event = _listen_event
    b.register_service = _register_service
    b.run_every = _run_every
    b.fire_event = _fire_event
    b.log = _log

    return b


@pytest.fixture
def mock_client():
    client = AsyncMock()
    client.read_sensors = AsyncMock(return_value=_make_sensors())
    client.set_energy_mode = AsyncMock()
    client.set_dhw_setpoint = AsyncMock()
    client.trigger_dhw_oneshot = AsyncMock()
    client.set_quiet_mode = AsyncMock()
    client.set_heating_curve_shift = AsyncMock()
    client.close = AsyncMock()
    return client


@pytest.fixture
def bridge(tmp_path, mock_client):
    """Initialized KermiBridge with mocked KermiClient and all Hass methods."""
    cfg_path = tmp_path / "kermi_bridge.yaml"
    cfg_path.write_text(yaml.dump(MINIMAL_KB_CONFIG), encoding="utf-8")

    b = _make_bridge_instance(str(cfg_path))

    with patch("kermi_bridge.kermi_bridge.KermiClient", return_value=mock_client):
        asyncio.run(b.initialize())

    return b


# ── TestInitialize ────────────────────────────────────────────────────────────

class TestInitialize:
    def test_run_every_scheduled(self, bridge):
        assert len(bridge.run_every_calls) == 1
        call = bridge.run_every_calls[0]
        assert call["interval"] == 30
        assert call["start"] == "now"

    def test_all_services_registered(self, bridge):
        expected = [
            "kermi_bridge/set_energy_mode",
            "kermi_bridge/set_dhw_setpoint",
            "kermi_bridge/trigger_dhw_oneshot",
            "kermi_bridge/set_quiet_mode",
            "kermi_bridge/set_heating_curve_shift",
            "kermi_bridge/refresh",
        ]
        for svc in expected:
            assert svc in bridge.registered_services, f"Missing service: {svc}"

    def test_config_error_aborts_init(self, tmp_path):
        b = _make_bridge_instance(str(tmp_path / "nonexistent.yaml"))
        asyncio.run(b.initialize())
        assert any("config error" in msg.lower() for msg in b._log_output)
        assert b.run_every_calls == []

    def test_info_log_on_success(self, bridge):
        assert any("[INFO]" in msg for msg in bridge._log_output)


# ── TestPollSuccess ───────────────────────────────────────────────────────────

class TestPollSuccess:
    def test_status_ok(self, bridge, mock_client):
        asyncio.run(bridge._poll({}))
        status = [
            c for c in bridge.set_state_calls
            if c["entity_id"] == "sensor.kermi_bridge_status"
        ]
        assert status[-1]["state"] == "ok"

    def test_all_entities_published(self, bridge, mock_client):
        asyncio.run(bridge._poll({}))
        published = {c["entity_id"] for c in bridge.set_state_calls}
        for eid in _ALL_SENSOR_ENTITIES:
            assert eid in published, f"Missing entity: {eid}"

    def test_temperature_value(self, bridge, mock_client):
        mock_client.read_sensors.return_value = _make_sensors(outside_temp=7.5)
        asyncio.run(bridge._poll({}))
        calls = [
            c for c in bridge.set_state_calls
            if c["entity_id"] == "sensor.kermi_outside_temp"
        ]
        assert calls[-1]["state"] == "7.5"

    def test_consecutive_failures_reset(self, bridge, mock_client):
        bridge._consecutive_failures = 2
        asyncio.run(bridge._poll({}))
        assert bridge._consecutive_failures == 0

    def test_evu_binary_sensor_off(self, bridge, mock_client):
        mock_client.read_sensors.return_value = _make_sensors(evu_status=False)
        asyncio.run(bridge._poll({}))
        calls = [
            c for c in bridge.set_state_calls
            if c["entity_id"] == "binary_sensor.kermi_evu_lock"
        ]
        assert calls[-1]["state"] == "off"

    def test_evu_binary_sensor_on(self, bridge, mock_client):
        mock_client.read_sensors.return_value = _make_sensors(evu_status=True)
        asyncio.run(bridge._poll({}))
        calls = [
            c for c in bridge.set_state_calls
            if c["entity_id"] == "binary_sensor.kermi_evu_lock"
        ]
        assert calls[-1]["state"] == "on"

    def test_energy_mode_name_and_int(self, bridge, mock_client):
        asyncio.run(bridge._poll({}))
        calls = [
            c for c in bridge.set_state_calls
            if c["entity_id"] == "sensor.kermi_energy_mode_mk1"
        ]
        assert calls[-1]["state"] == "NORMAL"
        assert calls[-1]["attributes"]["mode_int"] == int(EnergyMode.NORMAL)

    def test_split_electricity_sensors_published_with_energy_attrs(self, bridge, mock_client):
        mock_client.read_sensors.return_value = _make_sensors(
            electricity_heating_kwh=8886.0, electricity_dhw_kwh=1282.0
        )
        asyncio.run(bridge._poll({}))
        for entity_id, expected_value in [
            ("sensor.kermi_electricity_heating_kwh", "8886.0"),
            ("sensor.kermi_electricity_dhw_kwh", "1282.0"),
        ]:
            calls = [c for c in bridge.set_state_calls if c["entity_id"] == entity_id]
            assert calls, f"{entity_id} not published"
            last = calls[-1]
            assert last["state"] == expected_value
            attrs = last["attributes"]
            assert attrs["device_class"] == "energy"
            assert attrs["state_class"] == "total_increasing"
            assert attrs["unit_of_measurement"] == "kWh"

    def test_status_attributes_present(self, bridge, mock_client):
        asyncio.run(bridge._poll({}))
        status = [
            c for c in bridge.set_state_calls
            if c["entity_id"] == "sensor.kermi_bridge_status"
        ]
        attrs = status[-1]["attributes"]
        assert "last_poll" in attrs
        assert "consecutive_failures" in attrs
        assert attrs["poll_interval_s"] == 30


# ── TestPollPartial ───────────────────────────────────────────────────────────

class TestPollPartial:
    def test_none_float_becomes_unavailable(self, bridge, mock_client):
        mock_client.read_sensors.return_value = _make_sensors(outside_temp=None)
        asyncio.run(bridge._poll({}))
        calls = [
            c for c in bridge.set_state_calls
            if c["entity_id"] == "sensor.kermi_outside_temp"
        ]
        assert calls[-1]["state"] == "unavailable"

    def test_none_evu_becomes_unavailable(self, bridge, mock_client):
        mock_client.read_sensors.return_value = _make_sensors(evu_status=None)
        asyncio.run(bridge._poll({}))
        calls = [
            c for c in bridge.set_state_calls
            if c["entity_id"] == "binary_sensor.kermi_evu_lock"
        ]
        assert calls[-1]["state"] == "unavailable"

    def test_none_energy_mode_becomes_unavailable(self, bridge, mock_client):
        mock_client.read_sensors.return_value = _make_sensors(energy_mode_mk1=None)
        asyncio.run(bridge._poll({}))
        calls = [
            c for c in bridge.set_state_calls
            if c["entity_id"] == "sensor.kermi_energy_mode_mk1"
        ]
        assert calls[-1]["state"] == "unavailable"

    def test_non_none_sensors_still_published(self, bridge, mock_client):
        mock_client.read_sensors.return_value = _make_sensors(outside_temp=None, cop=5.0)
        asyncio.run(bridge._poll({}))
        cop_calls = [
            c for c in bridge.set_state_calls
            if c["entity_id"] == "sensor.kermi_cop"
        ]
        assert cop_calls[-1]["state"] == "5.0"


# ── TestPollConnError ─────────────────────────────────────────────────────────

class TestPollConnError:
    def test_status_unavailable(self, bridge, mock_client):
        mock_client.read_sensors.side_effect = KermiConnectionError("timeout")
        asyncio.run(bridge._poll({}))
        calls = [
            c for c in bridge.set_state_calls
            if c["entity_id"] == "sensor.kermi_bridge_status"
        ]
        assert calls[-1]["state"] == "unavailable"

    def test_failure_counter_increments(self, bridge, mock_client):
        mock_client.read_sensors.side_effect = KermiConnectionError("timeout")
        asyncio.run(bridge._poll({}))
        assert bridge._consecutive_failures == 1

    def test_no_event_before_max_failures(self, bridge, mock_client):
        mock_client.read_sensors.side_effect = KermiConnectionError("timeout")
        asyncio.run(bridge._poll({}))
        assert bridge.fire_event_calls == []

    def test_polling_remains_active(self, bridge, mock_client):
        mock_client.read_sensors.side_effect = KermiConnectionError("timeout")
        asyncio.run(bridge._poll({}))
        assert bridge._polling_active is True


# ── TestPollAuthError ─────────────────────────────────────────────────────────

class TestPollAuthError:
    def test_status_auth_error(self, bridge, mock_client):
        mock_client.read_sensors.side_effect = KermiAuthError("bad password")
        asyncio.run(bridge._poll({}))
        calls = [
            c for c in bridge.set_state_calls
            if c["entity_id"] == "sensor.kermi_bridge_status"
        ]
        assert calls[-1]["state"] == "auth_error"

    def test_all_sensors_unavailable(self, bridge, mock_client):
        mock_client.read_sensors.side_effect = KermiAuthError("bad password")
        asyncio.run(bridge._poll({}))
        for eid in _ALL_SENSOR_ENTITIES:
            entity_calls = [
                c for c in bridge.set_state_calls if c["entity_id"] == eid
            ]
            assert entity_calls and entity_calls[-1]["state"] == "unavailable", (
                f"{eid} not marked unavailable"
            )

    def test_auth_error_event_fired(self, bridge, mock_client):
        mock_client.read_sensors.side_effect = KermiAuthError("bad password")
        asyncio.run(bridge._poll({}))
        events = [
            e for e in bridge.fire_event_calls
            if e["event"] == "kermi_bridge_auth_error"
        ]
        assert len(events) == 1

    def test_polling_stopped(self, bridge, mock_client):
        mock_client.read_sensors.side_effect = KermiAuthError("bad password")
        asyncio.run(bridge._poll({}))
        assert bridge._polling_active is False

    def test_subsequent_poll_is_noop(self, bridge, mock_client):
        mock_client.read_sensors.side_effect = KermiAuthError("bad password")
        asyncio.run(bridge._poll({}))
        bridge.set_state_calls.clear()
        asyncio.run(bridge._poll({}))
        assert bridge.set_state_calls == []


# ── TestRecovery ──────────────────────────────────────────────────────────────

class TestRecovery:
    def test_failures_reset_after_success(self, bridge, mock_client):
        mock_client.read_sensors.side_effect = KermiConnectionError("timeout")
        asyncio.run(bridge._poll({}))
        assert bridge._consecutive_failures == 1

        mock_client.read_sensors.side_effect = None
        mock_client.read_sensors.return_value = _make_sensors()
        asyncio.run(bridge._poll({}))
        assert bridge._consecutive_failures == 0

    def test_status_ok_after_recovery(self, bridge, mock_client):
        mock_client.read_sensors.side_effect = KermiConnectionError("timeout")
        asyncio.run(bridge._poll({}))
        mock_client.read_sensors.side_effect = None
        mock_client.read_sensors.return_value = _make_sensors()
        asyncio.run(bridge._poll({}))
        calls = [
            c for c in bridge.set_state_calls
            if c["entity_id"] == "sensor.kermi_bridge_status"
        ]
        assert calls[-1]["state"] == "ok"


# ── TestMaxFailures ───────────────────────────────────────────────────────────

class TestMaxFailures:
    def test_event_fired_exactly_at_max_failures(self, bridge, mock_client):
        # max_failures = 3 in MINIMAL_KB_CONFIG
        mock_client.read_sensors.side_effect = KermiConnectionError("timeout")
        for _ in range(3):
            asyncio.run(bridge._poll({}))
        events = [
            e for e in bridge.fire_event_calls
            if e["event"] == "kermi_bridge_connection_error"
        ]
        assert len(events) == 1

    def test_event_not_fired_again_beyond_max(self, bridge, mock_client):
        mock_client.read_sensors.side_effect = KermiConnectionError("timeout")
        for _ in range(6):
            asyncio.run(bridge._poll({}))
        events = [
            e for e in bridge.fire_event_calls
            if e["event"] == "kermi_bridge_connection_error"
        ]
        # Event fires once at failure #3, never again
        assert len(events) == 1

    def test_event_not_fired_before_max_failures(self, bridge, mock_client):
        mock_client.read_sensors.side_effect = KermiConnectionError("timeout")
        for _ in range(2):
            asyncio.run(bridge._poll({}))
        assert bridge.fire_event_calls == []


# ── TestSetEnergyMode ─────────────────────────────────────────────────────────

class TestSetEnergyMode:
    def test_calls_client_with_mode(self, bridge, mock_client):
        asyncio.run(
            bridge._svc_set_energy_mode(None, None, None, {"mode": "COMFORT"})
        )
        mock_client.set_energy_mode.assert_called_once_with(
            EnergyMode.COMFORT, ["MK1", "MK2"]
        )

    def test_custom_circuits(self, bridge, mock_client):
        asyncio.run(
            bridge._svc_set_energy_mode(
                None, None, None, {"mode": "ECO", "circuits": ["HK"]}
            )
        )
        mock_client.set_energy_mode.assert_called_once_with(EnergyMode.ECO, ["HK"])

    def test_unknown_mode_logs_error(self, bridge, mock_client):
        asyncio.run(
            bridge._svc_set_energy_mode(None, None, None, {"mode": "TURBO"})
        )
        mock_client.set_energy_mode.assert_not_called()
        assert any("[ERROR]" in msg for msg in bridge._log_output)

    def test_client_exception_logs_error(self, bridge, mock_client):
        mock_client.set_energy_mode.side_effect = Exception("device busy")
        asyncio.run(
            bridge._svc_set_energy_mode(None, None, None, {"mode": "NORMAL"})
        )
        assert any("[ERROR]" in msg for msg in bridge._log_output)

    def test_case_insensitive_mode(self, bridge, mock_client):
        asyncio.run(
            bridge._svc_set_energy_mode(None, None, None, {"mode": "eco"})
        )
        mock_client.set_energy_mode.assert_called_once_with(
            EnergyMode.ECO, ["MK1", "MK2"]
        )

    def test_empty_circuits_logs_error(self, bridge, mock_client):
        asyncio.run(
            bridge._svc_set_energy_mode(None, None, None, {"mode": "NORMAL", "circuits": []})
        )
        mock_client.set_energy_mode.assert_not_called()
        assert any("[ERROR]" in msg for msg in bridge._log_output)


# ── TestSetDhwSetpoint ────────────────────────────────────────────────────────

class TestSetDhwSetpoint:
    def test_calls_client_with_correct_temp(self, bridge, mock_client):
        asyncio.run(
            bridge._svc_set_dhw_setpoint(None, None, None, {"temperature": 55.0})
        )
        mock_client.set_dhw_setpoint.assert_called_once_with(55.0)

    def test_out_of_range_logs_error(self, bridge, mock_client):
        asyncio.run(
            bridge._svc_set_dhw_setpoint(None, None, None, {"temperature": 100.0})
        )
        mock_client.set_dhw_setpoint.assert_not_called()
        assert any("[ERROR]" in msg for msg in bridge._log_output)

    def test_missing_temperature_logs_error(self, bridge, mock_client):
        asyncio.run(bridge._svc_set_dhw_setpoint(None, None, None, {}))
        mock_client.set_dhw_setpoint.assert_not_called()
        assert any("[ERROR]" in msg for msg in bridge._log_output)

    def test_boundary_zero_calls_client(self, bridge, mock_client):
        asyncio.run(
            bridge._svc_set_dhw_setpoint(None, None, None, {"temperature": 0.0})
        )
        mock_client.set_dhw_setpoint.assert_called_once_with(0.0)

    def test_boundary_85_calls_client(self, bridge, mock_client):
        asyncio.run(
            bridge._svc_set_dhw_setpoint(None, None, None, {"temperature": 85.0})
        )
        mock_client.set_dhw_setpoint.assert_called_once_with(85.0)

    def test_client_error_logged(self, bridge, mock_client):
        mock_client.set_dhw_setpoint.side_effect = KermiWriteError("write failed")
        asyncio.run(
            bridge._svc_set_dhw_setpoint(None, None, None, {"temperature": 55.0})
        )
        assert any("[ERROR]" in msg for msg in bridge._log_output)


# ── TestTriggerDhwOneshot ─────────────────────────────────────────────────────

class TestTriggerDhwOneshot:
    def test_calls_client(self, bridge, mock_client):
        asyncio.run(bridge._svc_trigger_dhw_oneshot(None, None, None, {}))
        mock_client.trigger_dhw_oneshot.assert_called_once()

    def test_client_error_logged(self, bridge, mock_client):
        mock_client.trigger_dhw_oneshot.side_effect = KermiWriteError("write failed")
        asyncio.run(bridge._svc_trigger_dhw_oneshot(None, None, None, {}))
        assert any("[ERROR]" in msg for msg in bridge._log_output)


# ── TestSetQuietMode ──────────────────────────────────────────────────────────

class TestSetQuietMode:
    def test_calls_client_true(self, bridge, mock_client):
        asyncio.run(
            bridge._svc_set_quiet_mode(None, None, None, {"enabled": True})
        )
        mock_client.set_quiet_mode.assert_called_once_with(True)

    def test_calls_client_false(self, bridge, mock_client):
        asyncio.run(
            bridge._svc_set_quiet_mode(None, None, None, {"enabled": False})
        )
        mock_client.set_quiet_mode.assert_called_once_with(False)

    def test_defaults_enabled_true(self, bridge, mock_client):
        asyncio.run(bridge._svc_set_quiet_mode(None, None, None, {}))
        mock_client.set_quiet_mode.assert_called_once_with(True)

    def test_client_error_logged(self, bridge, mock_client):
        mock_client.set_quiet_mode.side_effect = KermiWriteError("write failed")
        asyncio.run(bridge._svc_set_quiet_mode(None, None, None, {"enabled": True}))
        assert any("[ERROR]" in msg for msg in bridge._log_output)


# ── TestSetHeatingCurveShift ──────────────────────────────────────────────────

class TestSetHeatingCurveShift:
    def test_calls_client_default_circuits(self, bridge, mock_client):
        asyncio.run(
            bridge._svc_set_heating_curve_shift(None, None, None, {"shift": 2})
        )
        mock_client.set_heating_curve_shift.assert_called_once_with(2, None)

    def test_calls_client_with_circuits(self, bridge, mock_client):
        asyncio.run(
            bridge._svc_set_heating_curve_shift(
                None, None, None, {"shift": -1, "circuits": ["HK"]}
            )
        )
        mock_client.set_heating_curve_shift.assert_called_once_with(-1, ["HK"])

    def test_missing_shift_logs_error(self, bridge, mock_client):
        asyncio.run(bridge._svc_set_heating_curve_shift(None, None, None, {}))
        mock_client.set_heating_curve_shift.assert_not_called()
        assert any("[ERROR]" in msg for msg in bridge._log_output)

    def test_out_of_range_shift_logs_error(self, bridge, mock_client):
        asyncio.run(
            bridge._svc_set_heating_curve_shift(None, None, None, {"shift": 10})
        )
        mock_client.set_heating_curve_shift.assert_not_called()
        assert any("[ERROR]" in msg for msg in bridge._log_output)

    def test_client_error_logged(self, bridge, mock_client):
        mock_client.set_heating_curve_shift.side_effect = KermiError("write failed")
        asyncio.run(
            bridge._svc_set_heating_curve_shift(None, None, None, {"shift": 1})
        )
        assert any("[ERROR]" in msg for msg in bridge._log_output)


# ── TestRefreshService ────────────────────────────────────────────────────────

class TestRefreshService:
    def test_triggers_poll(self, bridge, mock_client):
        before = mock_client.read_sensors.call_count
        asyncio.run(bridge._svc_refresh(None, None, None, {}))
        assert mock_client.read_sensors.call_count == before + 1


# ── TestTerminate ─────────────────────────────────────────────────────────────

class TestTerminate:
    def test_closes_client(self, bridge, mock_client):
        asyncio.run(bridge.terminate())
        mock_client.close.assert_called_once()

    def test_terminate_without_client_does_not_raise(self, tmp_path):
        """terminate() must not raise if initialize() never ran (e.g. config error)."""
        b = _make_bridge_instance(str(tmp_path / "nonexistent.yaml"))
        asyncio.run(b.initialize())  # config error → _client never set
        asyncio.run(b.terminate())   # must not raise AttributeError


# ── MQTT test helpers ─────────────────────────────────────────────────────────

import json as _json

MINIMAL_KB_CONFIG_MQTT = {**MINIMAL_KB_CONFIG}


@pytest.fixture
def mqtt_bridge(tmp_path, mock_client):
    """Initialized KermiBridge with MQTT Discovery enabled."""
    cfg_path = tmp_path / "kermi_bridge.yaml"
    cfg_path.write_text(yaml.dump(MINIMAL_KB_CONFIG_MQTT), encoding="utf-8")

    b = _make_bridge_instance(
        str(cfg_path),
        extra_args={"mqtt_discovery": True, "mqtt_namespace": "mqtt"},
    )
    with patch("kermi_bridge.kermi_bridge.KermiClient", return_value=mock_client):
        asyncio.run(b.initialize())
    return b


# ── TestMqttInitialize ────────────────────────────────────────────────────────

class TestMqttInitialize:
    def test_no_services_registered_in_mqtt_mode(self, mqtt_bridge):
        assert mqtt_bridge.registered_services == {}

    def test_services_registered_in_legacy_mode(self, bridge):
        assert len(bridge.registered_services) == 6

    def test_discovery_published_for_sensors(self, mqtt_bridge):
        topics = [c.get("topic", "") for c in mqtt_bridge.call_service_calls]
        sensor_cfgs = [t for t in topics if t.startswith("homeassistant/sensor/kermi_")]
        assert len(sensor_cfgs) >= 20, f"Expected ≥20 sensor discovery topics, got {len(sensor_cfgs)}"

    def test_discovery_published_for_binary_sensor(self, mqtt_bridge):
        topics = [c.get("topic", "") for c in mqtt_bridge.call_service_calls]
        assert "homeassistant/binary_sensor/kermi_evu_lock/config" in topics

    def test_discovery_published_for_energy_mode_selects(self, mqtt_bridge):
        topics = [c.get("topic", "") for c in mqtt_bridge.call_service_calls]
        for circuit in ("mk1", "mk2", "hk"):
            assert f"homeassistant/select/kermi_energy_mode_{circuit}/config" in topics

    def test_energy_mode_select_has_all_options(self, mqtt_bridge):
        calls = [
            c for c in mqtt_bridge.call_service_calls
            if c.get("topic") == "homeassistant/select/kermi_energy_mode_mk1/config"
        ]
        assert calls
        payload = _json.loads(calls[0]["payload"])
        assert set(payload["options"]) == {"OFF", "ECO", "NORMAL", "COMFORT", "CUSTOM"}

    def test_dhw_setpoint_number_discovery(self, mqtt_bridge):
        topics = [c.get("topic", "") for c in mqtt_bridge.call_service_calls]
        assert "homeassistant/number/kermi_dhw_setpoint/config" in topics
        calls = [c for c in mqtt_bridge.call_service_calls
                 if c.get("topic") == "homeassistant/number/kermi_dhw_setpoint/config"]
        payload = _json.loads(calls[0]["payload"])
        assert payload["min"] == 0
        assert payload["max"] == 85

    def test_heating_curve_shift_per_circuit(self, mqtt_bridge):
        topics = [c.get("topic", "") for c in mqtt_bridge.call_service_calls]
        # config has circuits: [MK1, MK2]
        assert "homeassistant/number/kermi_heating_curve_shift_mk1/config" in topics
        assert "homeassistant/number/kermi_heating_curve_shift_mk2/config" in topics
        assert "homeassistant/number/kermi_heating_curve_shift_hk/config" not in topics

    def test_button_entities_published(self, mqtt_bridge):
        topics = [c.get("topic", "") for c in mqtt_bridge.call_service_calls]
        assert "homeassistant/button/kermi_dhw_oneshot/config" in topics
        assert "homeassistant/button/kermi_refresh/config" in topics

    def test_switch_entity_published(self, mqtt_bridge):
        topics = [c.get("topic", "") for c in mqtt_bridge.call_service_calls]
        assert "homeassistant/switch/kermi_quiet_mode/config" in topics

    def test_availability_online_at_init(self, mqtt_bridge):
        online_calls = [c for c in mqtt_bridge.call_service_calls
                        if c.get("payload") == "online"]
        assert online_calls

    def test_legacy_entities_marked_unavailable(self, mqtt_bridge):
        for eid in _ALL_SENSOR_ENTITIES[:3]:
            calls = [c for c in mqtt_bridge.set_state_calls if c["entity_id"] == eid]
            assert calls and calls[-1]["state"] == "unavailable"

    def test_command_subscriptions_registered(self, mqtt_bridge):
        events = {e.get("event") for e in mqtt_bridge.listen_event_calls}
        assert "MQTT_MESSAGE" in events
        # Should have subscriptions for selects, numbers, switch, buttons
        topics = {e.get("topic") for e in mqtt_bridge.listen_event_calls}
        assert any("kermi_energy_mode_mk1" in t for t in topics)
        assert any("kermi_dhw_setpoint" in t for t in topics)
        assert any("kermi_quiet_mode" in t for t in topics)


# ── TestMqttPoll ──────────────────────────────────────────────────────────────

class TestMqttPoll:
    def test_poll_publishes_via_mqtt_not_set_state(self, mqtt_bridge, mock_client):
        mqtt_bridge.set_state_calls.clear()
        mqtt_bridge.call_service_calls.clear()
        asyncio.run(mqtt_bridge._poll({}))

        # No data sensors via set_state
        data_set_state = [
            c for c in mqtt_bridge.set_state_calls
            if c.get("entity_id", "").startswith("sensor.kermi_outside")
        ]
        assert data_set_state == []

        # State topics published
        state_publishes = [
            c for c in mqtt_bridge.call_service_calls
            if c.get("topic", "").endswith("/state")
        ]
        assert len(state_publishes) >= 20

    def test_poll_publishes_energy_mode_to_select_state_topic(self, mqtt_bridge, mock_client):
        mock_client.read_sensors.return_value = _make_sensors(
            energy_mode_mk1=EnergyMode.COMFORT
        )
        mqtt_bridge.call_service_calls.clear()
        asyncio.run(mqtt_bridge._poll({}))

        state_calls = [
            c for c in mqtt_bridge.call_service_calls
            if "kermi_energy_mode_mk1/state" in c.get("topic", "")
        ]
        assert state_calls and state_calls[-1]["payload"] == "COMFORT"

    def test_poll_publishes_evu_as_ON_OFF(self, mqtt_bridge, mock_client):
        mock_client.read_sensors.return_value = _make_sensors(evu_status=True)
        mqtt_bridge.call_service_calls.clear()
        asyncio.run(mqtt_bridge._poll({}))

        evu_calls = [
            c for c in mqtt_bridge.call_service_calls
            if "kermi_evu_lock/state" in c.get("topic", "")
        ]
        assert evu_calls and evu_calls[-1]["payload"] == "ON"

    def test_poll_bridge_status_published(self, mqtt_bridge, mock_client):
        mqtt_bridge.call_service_calls.clear()
        asyncio.run(mqtt_bridge._poll({}))
        status_calls = [
            c for c in mqtt_bridge.call_service_calls
            if "kermi_bridge_status/state" in c.get("topic", "")
        ]
        assert status_calls and status_calls[-1]["payload"] == "ok"

    def test_auth_error_publishes_offline(self, mqtt_bridge, mock_client):
        mock_client.read_sensors.side_effect = KermiAuthError("bad pwd")
        mqtt_bridge.call_service_calls.clear()
        asyncio.run(mqtt_bridge._poll({}))
        offline = [c for c in mqtt_bridge.call_service_calls if c.get("payload") == "offline"]
        assert offline


# ── TestMqttCommandHandlers ───────────────────────────────────────────────────

class TestMqttCommandHandlers:
    def test_energy_mode_cmd_calls_client(self, mqtt_bridge, mock_client):
        asyncio.run(mqtt_bridge._do_set_energy_mode(EnergyMode.ECO, ["MK1"]))
        mock_client.set_energy_mode.assert_called_once_with(EnergyMode.ECO, ["MK1"])

    def test_energy_mode_cmd_unknown_logs_error(self, mqtt_bridge, mock_client):
        mqtt_bridge._on_cmd_energy_mode("mk1", {"payload": "TURBO"})
        assert any("[ERROR]" in m for m in mqtt_bridge._log_output)
        mock_client.set_energy_mode.assert_not_called()

    def test_dhw_setpoint_cmd_calls_client(self, mqtt_bridge, mock_client):
        asyncio.run(mqtt_bridge._do_set_dhw_setpoint(55.0))
        mock_client.set_dhw_setpoint.assert_called_once_with(55.0)

    def test_dhw_setpoint_cmd_out_of_range_logs_error(self, mqtt_bridge, mock_client):
        mqtt_bridge._on_cmd_dhw_setpoint({"payload": "100"})
        assert any("[ERROR]" in m for m in mqtt_bridge._log_output)
        mock_client.set_dhw_setpoint.assert_not_called()

    def test_dhw_setpoint_cmd_invalid_payload_logs_error(self, mqtt_bridge, mock_client):
        mqtt_bridge._on_cmd_dhw_setpoint({"payload": "not-a-number"})
        assert any("[ERROR]" in m for m in mqtt_bridge._log_output)

    def test_quiet_mode_on(self, mqtt_bridge, mock_client):
        asyncio.run(mqtt_bridge._do_set_quiet_mode(True))
        mock_client.set_quiet_mode.assert_called_once_with(True)

    def test_quiet_mode_off(self, mqtt_bridge, mock_client):
        asyncio.run(mqtt_bridge._do_set_quiet_mode(False))
        mock_client.set_quiet_mode.assert_called_once_with(False)

    def test_dhw_oneshot_calls_client(self, mqtt_bridge, mock_client):
        asyncio.run(mqtt_bridge._do_trigger_dhw_oneshot())
        mock_client.trigger_dhw_oneshot.assert_called_once()

    def test_heating_curve_shift_cmd(self, mqtt_bridge, mock_client):
        asyncio.run(mqtt_bridge._do_set_heating_curve_shift(2, "mk1"))
        mock_client.set_heating_curve_shift.assert_called_once_with(2, ["MK1"])

    def test_heating_curve_shift_out_of_range(self, mqtt_bridge, mock_client):
        mqtt_bridge._on_cmd_heating_curve_shift("mk1", {"payload": "10"})
        assert any("[ERROR]" in m for m in mqtt_bridge._log_output)

    def test_heating_curve_shift_invalid_payload(self, mqtt_bridge, mock_client):
        mqtt_bridge._on_cmd_heating_curve_shift("mk1", {"payload": "x"})
        assert any("[ERROR]" in m for m in mqtt_bridge._log_output)
