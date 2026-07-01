"""Tests for KermiClient — all HTTP calls are mocked; no live device needed."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from kermi_bridge.kermi_client import (
    _DP,
    _TYPE_BOOL,
    _TYPE_FLOAT,
    _TYPE_INT,
    EnergyMode,
    KermiAuthError,
    KermiClient,
    KermiConnectionError,
    KermiSensors,
    KermiWriteError,
    WezMode,
)

# ── Fixtures & helpers ────────────────────────────────────────────────────────

LOGIN_OK = {"isValid": True, "changePassword": False, "redirectUrl": None}
LOGIN_FAIL = {"isValid": False, "changePassword": False, "redirectUrl": None}

DEVICES_RESPONSE = {
    "ResponseData": [
        {
            "DeviceId": "67b4e4ca-df6e-4fb4-8107-f5a35df73981",
            "DeviceType": 2,
            "Name": "x-change dynamic",
        },
        {
            "DeviceId": "00000000-0000-0000-0000-000000000000",
            "DeviceType": 0,
            "Name": "x-center Interfacemodul",
        },
    ],
    "StatusCode": 0,
}

DEVICE_ID = "67b4e4ca-df6e-4fb4-8107-f5a35df73981"

DEVICES_RUBIN = {
    "ResponseData": [
        {
            "DeviceId": "aaaa0001-0000-0000-0000-000000000000",
            "DeviceType": 97,
            "Name": "x-change dynamic pro",
        },
        {
            "DeviceId": "bbbb0001-0000-0000-0000-000000000000",
            "DeviceType": 95,
            "Name": "Heizen",
        },
    ],
    "StatusCode": 0,
}
DEVICE_ID_RUBIN = "aaaa0001-0000-0000-0000-000000000000"
DEVICE_ID_BUFFER = "bbbb0001-0000-0000-0000-000000000000"
DEVICE_ID_BUFFER_DHW = "cccc0001-0000-0000-0000-000000000000"

# Rubin firmware with split DT95 devices (separate Heating and DHW buffer systems).
DEVICES_RUBIN_SPLIT = {
    "ResponseData": [
        {
            "DeviceId": DEVICE_ID_RUBIN,
            "DeviceType": 97,
            "Name": "x-change dynamic pro",
        },
        {
            "DeviceId": DEVICE_ID_BUFFER,
            "DeviceType": 95,
            "Name": "Heizen",
            "CustomProperties": {"WizardAnswer": '{"BufferSystemType":2,"PowermoduleFunctionType":1}'},
        },
        {
            "DeviceId": DEVICE_ID_BUFFER_DHW,
            "DeviceType": 95,
            "Name": "Trinkwassererwärmung",
            "CustomProperties": {"WizardAnswer": '{"BufferSystemType":2,"PowermoduleFunctionType":2}'},
        },
    ],
    "StatusCode": 0,
}

# Minimal GetConfigsByDeviceType responses — include only the entries needed for tests.
CONFIGS_CLASSIC_DT2 = {
    "ResponseData": [
        {"WellKnownName": "HP_HeatpumpState", "DatapointConfigId": "41258683-9b38-4065-80d2-34c9a7e6ec2c"},
        {"WellKnownName": "HP_TotalCOP", "DatapointConfigId": "34760a09-8f79-424f-a1b0-5f1a9339d864"},
    ],
    "StatusCode": 0,
}
CONFIGS_RUBIN_DT97 = {
    "ResponseData": [
        {"WellKnownName": "Rubin_CombinedHeatpumpState", "DatapointConfigId": "f3966fa2-a25e-4cfe-a360-4749a0c5c1e0"},
        {"WellKnownName": "Rubin_CurrentCOP", "DatapointConfigId": "76b2a146-4cd6-477c-bf22-e420eeb51253"},
        {"WellKnownName": "Rubin_IsDefrostingState", "DatapointConfigId": "beff28be-32db-410d-b7ab-4304481e4b4a"},
    ],
    "StatusCode": 0,
}
CONFIGS_RUBIN_DT95 = {
    "ResponseData": [
        {
            "WellKnownName": "BufferSystem_TweTemperatureActual",
            "DatapointConfigId": "06e61673-abc2-4671-9e5a-960809d1f326",
        },
        {
            "WellKnownName": "BufferSystem_HeatingTemperatureActual",
            "DatapointConfigId": "63b1281e-d5b2-406d-b6cd-6564e7168d18",
        },
        {"WellKnownName": "BufferSystem_TweSetpoint", "DatapointConfigId": "8f7dd1c6-8e9c-4699-8d6e-3f561d947df4"},
    ],
    "StatusCode": 0,
}
CONFIGS_EMPTY = {"ResponseData": [], "StatusCode": 0}


def _make_read_response(values: dict[str, Any]) -> dict:
    """Build a ReadValues ResponseData payload from {dp_name: value} dict."""
    return {
        "ResponseData": [{"DatapointConfigId": _DP[name], "Value": value} for name, value in values.items()],
        "StatusCode": 0,
    }


def _make_write_response(status: int = 0, display: str = "") -> dict:
    return {"StatusCode": status, "ExceptionData": None, "DisplayText": display, "DetailedText": ""}


def _mock_response(body: dict, status: int = 200) -> MagicMock:
    """Return an async context-manager mock that yields a response-like object."""
    resp = MagicMock()
    resp.status = status
    resp.raise_for_status = MagicMock()
    resp.json = AsyncMock(return_value=body)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


class _FakeSession:
    """Minimal aiohttp.ClientSession mock with separate GET/POST response queues."""

    def __init__(
        self,
        get: list[MagicMock] | None = None,
        post: list[MagicMock] | None = None,
    ) -> None:
        self._get_responses = iter(get or [])
        self._post_responses = iter(post or [])
        self.closed = False

    def get(self, *_a, **_kw) -> MagicMock:
        return next(self._get_responses)

    def post(self, *_a, **_kw) -> MagicMock:
        return next(self._post_responses)

    async def close(self) -> None:
        self.closed = True


def _client_with_session(session: _FakeSession, device_id: str | None = None) -> KermiClient:
    client = KermiClient("192.168.1.121", "testpass", device_id=device_id)
    client._session = session
    if device_id:
        client._device_id = device_id
    return client


# ── Login / connect ───────────────────────────────────────────────────────────


class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_creates_session_with_unsafe_cookie_jar(self):
        """Session must use CookieJar(unsafe=True) so IP-address cookies are stored."""
        created_sessions = []

        original_init = aiohttp.ClientSession.__init__

        def _capture_init(self_session, *args, **kwargs):
            created_sessions.append(kwargs.get("cookie_jar"))
            original_init(self_session, *args, **kwargs)

        session = _FakeSession(
            post=[_mock_response(LOGIN_OK)],
            get=[_mock_response(DEVICES_RESPONSE)],
        )

        with patch("aiohttp.ClientSession") as mock_cls:
            instance = MagicMock()
            instance.closed = False
            instance.post = MagicMock(side_effect=session.post)
            instance.get = MagicMock(side_effect=session.get)
            mock_cls.return_value = instance

            client = KermiClient("192.168.1.121", "testpass")
            await client.connect()

        _, kwargs = mock_cls.call_args
        jar = kwargs.get("cookie_jar")
        assert jar is not None, "cookie_jar must be passed to ClientSession"
        assert isinstance(jar, aiohttp.CookieJar)
        assert jar._unsafe is True, "CookieJar must be unsafe=True for IP-address auth cookies"

    @pytest.mark.asyncio
    async def test_connect_login_and_discover_device(self):
        session = _FakeSession(
            post=[_mock_response(LOGIN_OK), _mock_response(CONFIGS_CLASSIC_DT2)],  # Login, GetConfigsByDeviceType
            get=[_mock_response(DEVICES_RESPONSE)],  # GET Device/GetAllDevices
        )
        client = _client_with_session(session)
        await client.connect()

        assert client._connected is True
        assert client._device_id == DEVICE_ID

    @pytest.mark.asyncio
    async def test_connect_uses_provided_device_id(self):
        session = _FakeSession(
            post=[_mock_response(LOGIN_OK), _mock_response(CONFIGS_CLASSIC_DT2)],
            get=[_mock_response(DEVICES_RESPONSE)],
        )
        client = _client_with_session(session, device_id=DEVICE_ID)
        await client.connect()

        assert client._device_id == DEVICE_ID

    @pytest.mark.asyncio
    async def test_connect_raises_on_bad_password(self):
        session = _FakeSession(post=[_mock_response(LOGIN_FAIL)])
        client = _client_with_session(session)

        with pytest.raises(KermiAuthError):
            await client.connect()

    @pytest.mark.asyncio
    async def test_connect_raises_on_network_error(self):
        import aiohttp

        resp_cm = MagicMock()
        resp_cm.__aenter__ = AsyncMock(side_effect=aiohttp.ClientConnectionError("refused"))
        resp_cm.__aexit__ = AsyncMock(return_value=False)

        session = _FakeSession(post=[resp_cm])
        client = _client_with_session(session)

        with pytest.raises(KermiConnectionError):
            await client.connect()


# ── read_sensors ─────────────────────────────────────────────────────────────


class TestReadSensors:
    @pytest.mark.asyncio
    async def test_returns_correct_sensor_values(self):
        read_body = _make_read_response(
            {
                "outside_temp": 8.1,
                "outside_temp_avg": 8.1,
                "flow_temp_mk1": 37.5,
                "flow_temp_mk2": 36.0,
                "hot_water_temp": 50.3,
                "buffer_temp": 39.6,
                "heating_setpoint": 35.0,
                "setpoint_mk1": 35.0,
                "compressor_power_kw": 0.0,
                "heating_output_kw": 0.0,
                "cop": 4.32,
                "cop_heating_avg": 4.36,
                "scop": 4.31,
                "lifetime_electricity_kwh": 10160.0,
                "lifetime_heat_kwh": 43854.0,
                "electricity_heating_kwh": 8886.0,
                "electricity_dhw_kwh": 1282.0,
                "hp_state": 1,
                "smart_grid_status": 2,
                "evu_status": False,
                "energy_mode_mk1": 1,
                "energy_mode_mk2": 1,
                "energy_mode_hk": 1,
                "global_alarm": False,
                "alarm_number": 0,
                "fan_power": 0.0,
            }
        )
        session = _FakeSession(post=[_mock_response(read_body)])
        client = _client_with_session(session, device_id=DEVICE_ID)
        client._connected = True

        sensors = await client.read_sensors()

        assert isinstance(sensors, KermiSensors)
        assert sensors.outside_temp == pytest.approx(8.1)
        assert sensors.flow_temp_mk1 == pytest.approx(37.5)
        assert sensors.hot_water_temp == pytest.approx(50.3)
        assert sensors.compressor_power_kw == pytest.approx(0.0)
        assert sensors.cop == pytest.approx(4.32)
        assert sensors.hp_state == 1
        assert sensors.smart_grid_status == 2
        assert sensors.evu_status is False
        assert sensors.energy_mode_mk1 == EnergyMode.ECO
        assert sensors.electricity_heating_kwh == pytest.approx(8886.0)
        assert sensors.electricity_dhw_kwh == pytest.approx(1282.0)
        assert sensors.timestamp is not None
        assert sensors.is_defrosting is None
        assert sensors.compressor_hours is None
        assert sensors.modulation_pct is None
        assert sensors.temp_spread is None
        assert sensors.pv_available_power is None
        assert sensors.heater_power is None
        assert sensors.global_alarm is False
        assert sensors.alarm_number == 0
        assert sensors.fan_power == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_handles_missing_datapoints_gracefully(self):
        """Partial response (some GUIDs absent) should not raise."""
        read_body = _make_read_response({"outside_temp": 5.0})
        session = _FakeSession(post=[_mock_response(read_body)])
        client = _client_with_session(session, device_id=DEVICE_ID)
        client._connected = True

        sensors = await client.read_sensors()

        assert sensors.outside_temp == pytest.approx(5.0)
        assert sensors.flow_temp_mk1 is None
        assert sensors.cop is None

    @pytest.mark.asyncio
    async def test_auto_connects_if_not_connected(self):
        """read_sensors should call connect() when _connected is False."""
        read_body = _make_read_response({"outside_temp": 7.0})
        session = _FakeSession(
            post=[_mock_response(LOGIN_OK), _mock_response(CONFIGS_CLASSIC_DT2), _mock_response(read_body)],
            get=[_mock_response(DEVICES_RESPONSE)],
        )
        client = _client_with_session(session)

        sensors = await client.read_sensors()
        assert sensors.outside_temp == pytest.approx(7.0)


# ── set_energy_mode ───────────────────────────────────────────────────────────


class TestSetEnergyMode:
    @pytest.mark.asyncio
    async def test_writes_correct_payload_for_two_circuits(self):
        write_body = _make_write_response(0)
        posted_payloads = []

        async def _capture_post(url, json=None, **_kw):
            posted_payloads.append(json)
            return _mock_response(write_body).__aenter__.return_value

        session = MagicMock()
        session.closed = False

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=_mock_response(write_body).__aenter__.return_value)
        cm.__aexit__ = AsyncMock(return_value=False)
        session.post = MagicMock(return_value=cm)

        client = _client_with_session(session, device_id=DEVICE_ID)
        client._connected = True

        await client.set_energy_mode(EnergyMode.COMFORT, circuits=["MK1", "MK2"])

        call_args = session.post.call_args
        payload = call_args.kwargs.get("json") or call_args.args[1]
        dp_values = payload["DatapointValues"]

        assert len(dp_values) == 2
        assert dp_values[0]["Value"] == int(EnergyMode.COMFORT)
        assert dp_values[0]["$type"] == _TYPE_INT
        assert dp_values[0]["DatapointConfigId"] == _DP["energy_mode_mk1"]
        assert dp_values[1]["DatapointConfigId"] == _DP["energy_mode_mk2"]

    @pytest.mark.asyncio
    async def test_raises_on_server_error(self):
        write_body = {
            "StatusCode": 1,
            "DisplayText": "Schreiben nicht erlaubt.",
            "ExceptionData": {"ErrorCode": "EX_LO_DATAPOINT_009"},
        }
        session = _FakeSession(post=[_mock_response(write_body)])
        client = _client_with_session(session, device_id=DEVICE_ID)
        client._connected = True

        with pytest.raises(KermiWriteError, match="WriteValues failed"):
            await client.set_energy_mode(EnergyMode.COMFORT)

    @pytest.mark.asyncio
    async def test_raises_on_unknown_circuit(self):
        client = KermiClient("192.168.1.121", "pass", device_id=DEVICE_ID)
        client._connected = True

        with pytest.raises(ValueError, match="Unknown circuits"):
            await client.set_energy_mode(EnergyMode.COMFORT, circuits=["MK9"])

    @pytest.mark.asyncio
    async def test_single_circuit_hk(self):
        write_body = _make_write_response(0)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=_mock_response(write_body).__aenter__.return_value)
        cm.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.closed = False
        session.post = MagicMock(return_value=cm)

        client = _client_with_session(session, device_id=DEVICE_ID)
        client._connected = True

        await client.set_energy_mode(EnergyMode.ECO, circuits=["HK"])

        payload = session.post.call_args.kwargs.get("json") or session.post.call_args.args[1]
        assert len(payload["DatapointValues"]) == 1
        assert payload["DatapointValues"][0]["DatapointConfigId"] == _DP["energy_mode_hk"]
        assert payload["DatapointValues"][0]["Value"] == int(EnergyMode.ECO)


# ── 401 re-auth ───────────────────────────────────────────────────────────────


class TestReAuth:
    @pytest.mark.asyncio
    async def test_reconnects_on_401_and_retries(self):
        """POST returning 401 should trigger re-login then retry the same request."""
        read_body = _make_read_response({"outside_temp": 12.0})

        resp_401 = MagicMock()
        resp_401.status = 401
        resp_401.raise_for_status = MagicMock()
        resp_401.json = AsyncMock(return_value={})

        cm_401 = MagicMock()
        cm_401.__aenter__ = AsyncMock(return_value=resp_401)
        cm_401.__aexit__ = AsyncMock(return_value=False)

        # After 401: re-login succeeds, then retry succeeds.
        cm_login = _mock_response(LOGIN_OK)
        cm_retry = _mock_response(read_body)

        call_count = 0

        def _post_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return cm_401  # First call: 401
            elif call_count == 2:
                return cm_login  # Re-login
            else:
                return cm_retry  # Retry of original request

        session = MagicMock()
        session.closed = False
        session.post = MagicMock(side_effect=_post_side_effect)

        client = _client_with_session(session, device_id=DEVICE_ID)
        client._connected = True

        sensors = await client.read_sensors()
        assert sensors.outside_temp == pytest.approx(12.0)

    @pytest.mark.asyncio
    async def test_connected_flag_true_after_401_reauth(self):
        """After inline 401 re-auth, _connected must be True (not False)."""
        read_body = _make_read_response({"outside_temp": 12.0})

        resp_401 = MagicMock()
        resp_401.status = 401
        resp_401.raise_for_status = MagicMock()
        resp_401.json = AsyncMock(return_value={})

        cm_401 = MagicMock()
        cm_401.__aenter__ = AsyncMock(return_value=resp_401)
        cm_401.__aexit__ = AsyncMock(return_value=False)

        cm_login = _mock_response(LOGIN_OK)
        cm_retry = _mock_response(read_body)

        call_count = 0

        def _post_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return cm_401
            elif call_count == 2:
                return cm_login
            else:
                return cm_retry

        session = MagicMock()
        session.closed = False
        session.post = MagicMock(side_effect=_post_side_effect)

        client = _client_with_session(session, device_id=DEVICE_ID)
        client._connected = True

        await client.read_sensors()

        assert client._connected is True


# ── EnergyMode enum ───────────────────────────────────────────────────────────


class TestEnergyModeEnum:
    def test_values(self):
        assert EnergyMode.OFF == 0
        assert EnergyMode.ECO == 1
        assert EnergyMode.NORMAL == 2
        assert EnergyMode.COMFORT == 3
        assert EnergyMode.CUSTOM == 4

    def test_roundtrip_from_int(self):
        assert EnergyMode(3) == EnergyMode.COMFORT

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            EnergyMode(99)


# ── set_dhw_setpoint ──────────────────────────────────────────────────────────


class TestSetDhwSetpoint:
    def _make_client(self, write_body: dict) -> tuple[KermiClient, MagicMock]:
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=_mock_response(write_body).__aenter__.return_value)
        cm.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.closed = False
        session.post = MagicMock(return_value=cm)
        client = _client_with_session(session, device_id=DEVICE_ID)
        client._connected = True
        return client, session

    @pytest.mark.asyncio
    async def test_set_dhw_setpoint_success(self):
        client, session = self._make_client(_make_write_response(0))
        await client.set_dhw_setpoint(55.0)

        payload = session.post.call_args.kwargs.get("json") or session.post.call_args.args[1]
        dp_values = payload["DatapointValues"]
        assert len(dp_values) == 1
        assert dp_values[0]["$type"] == _TYPE_FLOAT
        assert dp_values[0]["DatapointConfigId"] == _DP["dhw_setpoint"]
        assert dp_values[0]["Value"] == pytest.approx(55.0)

    @pytest.mark.asyncio
    async def test_set_dhw_setpoint_raises_on_write_failure(self):
        client, _ = self._make_client(_make_write_response(1, "Schreiben nicht erlaubt."))
        with pytest.raises(KermiWriteError, match="WriteValues failed"):
            await client.set_dhw_setpoint(55.0)

    @pytest.mark.asyncio
    async def test_set_dhw_setpoint_raises_on_out_of_range(self):
        client = KermiClient("192.168.1.121", "pass", device_id=DEVICE_ID)
        client._connected = True
        with pytest.raises(ValueError, match="out of range"):
            await client.set_dhw_setpoint(90.0)

    @pytest.mark.asyncio
    async def test_set_dhw_setpoint_boundary_zero(self):
        client, session = self._make_client(_make_write_response(0))
        await client.set_dhw_setpoint(0.0)
        payload = session.post.call_args.kwargs.get("json") or session.post.call_args.args[1]
        assert payload["DatapointValues"][0]["Value"] == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_set_dhw_setpoint_boundary_85(self):
        client, session = self._make_client(_make_write_response(0))
        await client.set_dhw_setpoint(85.0)
        payload = session.post.call_args.kwargs.get("json") or session.post.call_args.args[1]
        assert payload["DatapointValues"][0]["Value"] == pytest.approx(85.0)


# ── trigger_dhw_oneshot ───────────────────────────────────────────────────────


class TestTriggerDhwOneshot:
    def _make_client(self, write_body: dict) -> tuple[KermiClient, MagicMock]:
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=_mock_response(write_body).__aenter__.return_value)
        cm.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.closed = False
        session.post = MagicMock(return_value=cm)
        client = _client_with_session(session, device_id=DEVICE_ID)
        client._connected = True
        return client, session

    @pytest.mark.asyncio
    async def test_trigger_dhw_oneshot_success(self):
        client, session = self._make_client(_make_write_response(0))
        await client.trigger_dhw_oneshot()

        payload = session.post.call_args.kwargs.get("json") or session.post.call_args.args[1]
        dp_values = payload["DatapointValues"]
        assert len(dp_values) == 1
        assert dp_values[0]["$type"] == _TYPE_BOOL
        assert dp_values[0]["DatapointConfigId"] == _DP["dhw_oneshot_trigger"]
        assert dp_values[0]["Value"] is True

    @pytest.mark.asyncio
    async def test_trigger_dhw_oneshot_raises_on_write_failure(self):
        client, _ = self._make_client(_make_write_response(1, "error"))
        with pytest.raises(KermiWriteError, match="WriteValues failed"):
            await client.trigger_dhw_oneshot()


# ── set_quiet_mode ────────────────────────────────────────────────────────────


class TestSetQuietMode:
    def _make_client(self, write_body: dict) -> tuple[KermiClient, MagicMock]:
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=_mock_response(write_body).__aenter__.return_value)
        cm.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.closed = False
        session.post = MagicMock(return_value=cm)
        client = _client_with_session(session, device_id=DEVICE_ID)
        client._connected = True
        return client, session

    @pytest.mark.asyncio
    async def test_set_quiet_mode_true(self):
        client, session = self._make_client(_make_write_response(0))
        await client.set_quiet_mode(True)

        payload = session.post.call_args.kwargs.get("json") or session.post.call_args.args[1]
        dp_values = payload["DatapointValues"]
        assert len(dp_values) == 1
        assert dp_values[0]["$type"] == _TYPE_BOOL
        assert dp_values[0]["DatapointConfigId"] == _DP["quiet_mode"]
        assert dp_values[0]["Value"] is True

    @pytest.mark.asyncio
    async def test_set_quiet_mode_false(self):
        client, session = self._make_client(_make_write_response(0))
        await client.set_quiet_mode(False)

        payload = session.post.call_args.kwargs.get("json") or session.post.call_args.args[1]
        assert payload["DatapointValues"][0]["Value"] is False

    @pytest.mark.asyncio
    async def test_set_quiet_mode_raises_on_write_failure(self):
        client, _ = self._make_client(_make_write_response(1, "error"))
        with pytest.raises(KermiWriteError):
            await client.set_quiet_mode(True)


# ── set_heating_curve_shift ───────────────────────────────────────────────────


class TestSetHeatingCurveShift:
    def _make_client(self, write_body: dict) -> tuple[KermiClient, MagicMock]:
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=_mock_response(write_body).__aenter__.return_value)
        cm.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.closed = False
        session.post = MagicMock(return_value=cm)
        client = _client_with_session(session, device_id=DEVICE_ID)
        client._connected = True
        return client, session

    @pytest.mark.asyncio
    async def test_set_heating_curve_shift_success(self):
        client, session = self._make_client(_make_write_response(0))
        await client.set_heating_curve_shift(2)

        payload = session.post.call_args.kwargs.get("json") or session.post.call_args.args[1]
        dp_values = payload["DatapointValues"]
        assert len(dp_values) == 2  # MK1 + MK2 by default
        assert dp_values[0]["$type"] == _TYPE_INT
        assert dp_values[0]["DatapointConfigId"] == _DP["heating_curve_shift_mk1"]
        assert dp_values[0]["Value"] == 2
        assert dp_values[1]["DatapointConfigId"] == _DP["heating_curve_shift_mk2"]

    @pytest.mark.asyncio
    async def test_set_heating_curve_shift_single_circuit(self):
        client, session = self._make_client(_make_write_response(0))
        await client.set_heating_curve_shift(-3, circuits=["HK"])

        payload = session.post.call_args.kwargs.get("json") or session.post.call_args.args[1]
        dp_values = payload["DatapointValues"]
        assert len(dp_values) == 1
        assert dp_values[0]["DatapointConfigId"] == _DP["heating_curve_shift_hk"]
        assert dp_values[0]["Value"] == -3

    @pytest.mark.asyncio
    async def test_set_heating_curve_shift_unknown_circuit(self):
        client = KermiClient("192.168.1.121", "pass", device_id=DEVICE_ID)
        client._connected = True
        with pytest.raises(ValueError, match="Unknown circuits"):
            await client.set_heating_curve_shift(1, circuits=["MK9"])

    @pytest.mark.asyncio
    async def test_set_heating_curve_shift_out_of_range(self):
        client = KermiClient("192.168.1.121", "pass", device_id=DEVICE_ID)
        client._connected = True
        with pytest.raises(ValueError, match="out of range"):
            await client.set_heating_curve_shift(10)

    @pytest.mark.asyncio
    async def test_set_heating_curve_shift_raises_on_write_failure(self):
        client, _ = self._make_client(_make_write_response(1, "error"))
        with pytest.raises(KermiWriteError):
            await client.set_heating_curve_shift(1)


# ── WezMode enum ──────────────────────────────────────────────────────────────


class TestWezModeEnum:
    def test_values(self):
        assert WezMode.AUTO == 0
        assert WezMode.HP_ONLY == 1
        assert WezMode.BOTH == 2
        assert WezMode.SECONDARY == 3

    def test_roundtrip_from_int(self):
        assert WezMode(2) == WezMode.BOTH

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            WezMode(99)


# ── WEZ field parsing ─────────────────────────────────────────────────────────


class TestReadSensorsWez:
    @pytest.mark.asyncio
    async def test_returns_wez_sensor_values(self):
        read_body = _make_read_response(
            {
                "outside_temp": 5.0,
                "wez1_status": 1,
                "wez1_operating_hours": 786.5,
                "wez1_betriebsart": 0,
                "wez2_status": 0,
                "wez2_operating_hours": 0.0,
                "wez2_betriebsart": 2,
            }
        )
        session = _FakeSession(post=[_mock_response(read_body)])
        client = _client_with_session(session, device_id=DEVICE_ID)
        client._connected = True

        sensors = await client.read_sensors()

        assert sensors.wez1_status == 1
        assert sensors.wez1_operating_hours == pytest.approx(786.5)
        assert sensors.wez1_betriebsart == WezMode.AUTO
        assert sensors.wez2_status == 0
        assert sensors.wez2_operating_hours == pytest.approx(0.0)
        assert sensors.wez2_betriebsart == WezMode.BOTH

    @pytest.mark.asyncio
    async def test_wez_fields_none_when_absent(self):
        read_body = _make_read_response({"outside_temp": 5.0})
        session = _FakeSession(post=[_mock_response(read_body)])
        client = _client_with_session(session, device_id=DEVICE_ID)
        client._connected = True

        sensors = await client.read_sensors()

        assert sensors.wez1_status is None
        assert sensors.wez1_operating_hours is None
        assert sensors.wez1_betriebsart is None
        assert sensors.wez2_status is None
        assert sensors.wez2_betriebsart is None

    @pytest.mark.asyncio
    async def test_wez_betriebsart_unknown_value_yields_none(self):
        """An out-of-range Betriebsart int must not raise — returns None."""
        read_body = _make_read_response({"wez1_betriebsart": 99})
        session = _FakeSession(post=[_mock_response(read_body)])
        client = _client_with_session(session, device_id=DEVICE_ID)
        client._connected = True

        sensors = await client.read_sensors()

        assert sensors.wez1_betriebsart is None


# ── New monitoring sensors (WP return temp, flow LC, COP live) ────────────────


class TestReadSensorsNewFields:
    @pytest.mark.asyncio
    async def test_returns_new_sensor_values(self):
        """New sensors: wp_return_temp, wp_flow_temp_lc, cop_heating_live, cop_dhw_live."""
        read_body = _make_read_response(
            {
                "wp_return_temp": 38.5,
                "wp_flow_temp_lc": 42.1,
                "cop_heating_live": 3.8,
                "cop_dhw_live": 2.9,
            }
        )
        session = _FakeSession(post=[_mock_response(read_body)])
        client = _client_with_session(session, device_id=DEVICE_ID)
        client._connected = True

        sensors = await client.read_sensors()

        assert sensors.wp_return_temp == pytest.approx(38.5)
        assert sensors.wp_flow_temp_lc == pytest.approx(42.1)
        assert sensors.cop_heating_live == pytest.approx(3.8)
        assert sensors.cop_dhw_live == pytest.approx(2.9)

    @pytest.mark.asyncio
    async def test_new_fields_none_when_absent(self):
        """New fields are None when not in response."""
        read_body = _make_read_response({"outside_temp": 5.0})
        session = _FakeSession(post=[_mock_response(read_body)])
        client = _client_with_session(session, device_id=DEVICE_ID)
        client._connected = True

        sensors = await client.read_sensors()

        assert sensors.wp_return_temp is None
        assert sensors.wp_flow_temp_lc is None
        assert sensors.cop_heating_live is None
        assert sensors.cop_dhw_live is None

    @pytest.mark.asyncio
    async def test_cop_zero_is_valid_not_none(self):
        """COP value of 0.0 is valid (not suppressed to None)."""
        read_body = _make_read_response(
            {
                "cop_heating_live": 0.0,
                "cop_dhw_live": 0.0,
            }
        )
        session = _FakeSession(post=[_mock_response(read_body)])
        client = _client_with_session(session, device_id=DEVICE_ID)
        client._connected = True

        sensors = await client.read_sensors()

        assert sensors.cop_heating_live == 0.0
        assert sensors.cop_dhw_live == 0.0


# ── set_wez_mode ──────────────────────────────────────────────────────────────


class TestSetWezMode:
    def _make_client(self, write_body: dict) -> tuple[KermiClient, MagicMock]:
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=_mock_response(write_body).__aenter__.return_value)
        cm.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.closed = False
        session.post = MagicMock(return_value=cm)
        client = _client_with_session(session, device_id=DEVICE_ID)
        client._connected = True
        return client, session

    @pytest.mark.asyncio
    async def test_set_wez1_mode_correct_payload(self):
        client, session = self._make_client(_make_write_response(0))
        await client.set_wez_mode(1, WezMode.BOTH)

        payload = session.post.call_args.kwargs.get("json") or session.post.call_args.args[1]
        dp_values = payload["DatapointValues"]
        assert len(dp_values) == 1
        assert dp_values[0]["$type"] == _TYPE_INT
        assert dp_values[0]["DatapointConfigId"] == _DP["wez1_betriebsart"]
        assert dp_values[0]["Value"] == int(WezMode.BOTH)

    @pytest.mark.asyncio
    async def test_set_wez2_mode_correct_guid(self):
        client, session = self._make_client(_make_write_response(0))
        await client.set_wez_mode(2, WezMode.SECONDARY)

        payload = session.post.call_args.kwargs.get("json") or session.post.call_args.args[1]
        assert payload["DatapointValues"][0]["DatapointConfigId"] == _DP["wez2_betriebsart"]
        assert payload["DatapointValues"][0]["Value"] == int(WezMode.SECONDARY)

    @pytest.mark.asyncio
    async def test_invalid_wez_unit_raises_value_error(self):
        client = KermiClient("192.168.1.121", "pass", device_id=DEVICE_ID)
        client._connected = True
        with pytest.raises(ValueError, match="Unknown WEZ unit"):
            await client.set_wez_mode(3, WezMode.AUTO)

    @pytest.mark.asyncio
    async def test_raises_on_write_failure(self):
        client, _ = self._make_client(_make_write_response(1, "Schreiben nicht erlaubt."))
        with pytest.raises(KermiWriteError, match="WriteValues failed"):
            await client.set_wez_mode(1, WezMode.AUTO)


# ── WKN GUID resolution ───────────────────────────────────────────────────────


class TestResolveGuids:
    """Tests for WKN-based GUID resolution at connect time."""

    @pytest.mark.asyncio
    async def test_classic_firmware_guids_unchanged(self):
        """Classic firmware: WKN matches existing GUID → self._dp unchanged."""
        session = _FakeSession(
            post=[
                _mock_response(LOGIN_OK),
                _mock_response(CONFIGS_CLASSIC_DT2),  # DeviceType=2
            ],
            get=[_mock_response(DEVICES_RESPONSE)],
        )
        client = _client_with_session(session)
        await client.connect()

        assert client._dp["hp_state"] == _DP["hp_state"]
        assert client._dp["cop"] == _DP["cop"]

    @pytest.mark.asyncio
    async def test_rubin_firmware_guids_overridden(self):
        """Rubin firmware: Rubin_* WKNs override hp_state and cop GUIDs."""
        session = _FakeSession(
            post=[
                _mock_response(LOGIN_OK),
                _mock_response(CONFIGS_RUBIN_DT95),  # DeviceType=95 (sorted first)
                _mock_response(CONFIGS_RUBIN_DT97),  # DeviceType=97
            ],
            get=[_mock_response(DEVICES_RUBIN)],
        )
        client = _client_with_session(session)
        await client.connect()

        assert client._dp["hp_state"] == "f3966fa2-a25e-4cfe-a360-4749a0c5c1e0"
        assert client._dp["cop"] == "76b2a146-4cd6-477c-bf22-e420eeb51253"
        assert client._dp["hot_water_temp"] == "06e61673-abc2-4671-9e5a-960809d1f326"

    @pytest.mark.asyncio
    async def test_rubin_new_sensors_added_to_dp(self):
        """Rubin-only sensors absent from _DP are added to self._dp after resolution."""
        session = _FakeSession(
            post=[
                _mock_response(LOGIN_OK),
                _mock_response(CONFIGS_RUBIN_DT95),
                _mock_response(CONFIGS_RUBIN_DT97),
            ],
            get=[_mock_response(DEVICES_RUBIN)],
        )
        client = _client_with_session(session)

        assert "is_defrosting" not in _DP  # confirm it's absent from the module-level table

        await client.connect()

        assert client._dp["is_defrosting"] == "beff28be-32db-410d-b7ab-4304481e4b4a"

    @pytest.mark.asyncio
    async def test_resolution_failure_falls_back_to_hardcoded_guids(self):
        """If GetConfigsByDeviceType raises, self._dp keeps the original _DP values."""
        # Only provide Login and GetAllDevices — no GetConfigsByDeviceType response.
        # StopIteration from exhausted _FakeSession becomes RuntimeError inside the coroutine
        # (PEP 479); caught by the outer except Exception in _resolve_guids.
        session = _FakeSession(
            post=[_mock_response(LOGIN_OK)],
            get=[_mock_response(DEVICES_RESPONSE)],
        )
        client = _client_with_session(session)
        await client.connect()  # must not raise

        assert client._connected is True
        assert client._dp["hp_state"] == _DP["hp_state"]

    @pytest.mark.asyncio
    async def test_read_payload_skips_unresolved_rubin_keys(self):
        """On classic firmware, Rubin-only keys absent from self._dp are not in the ReadValues payload."""
        session = _FakeSession(post=[], get=[])
        client = _client_with_session(session, device_id=DEVICE_ID)
        client._connected = True
        # Manually set up a classic _dp (no is_defrosting key)
        client._dp = dict(_DP)
        client._device_id = DEVICE_ID

        # Capture what payload is sent
        sent_payloads = []
        original_post = client._post

        async def _capturing_post(endpoint, payload):
            if "ReadValues" in endpoint:
                sent_payloads.append(payload)
                return {"ResponseData": [], "StatusCode": 0}
            return await original_post(endpoint, payload)

        client._post = _capturing_post
        await client.read_sensors_raw()

        guids_sent = {v["DatapointConfigId"] for v in sent_payloads[0]["DatapointValues"]}
        assert "beff28be-32db-410d-b7ab-4304481e4b4a" not in guids_sent  # Rubin_IsDefrosting not sent

    @pytest.mark.asyncio
    async def test_device_routing_uses_correct_device_id(self):
        """Rubin firmware: BufferSystem datapoints use the DeviceType=95 DeviceId."""
        session = _FakeSession(
            post=[
                _mock_response(LOGIN_OK),
                _mock_response(CONFIGS_RUBIN_DT95),
                _mock_response(CONFIGS_RUBIN_DT97),
            ],
            get=[_mock_response(DEVICES_RUBIN)],
        )
        client = _client_with_session(session)
        await client.connect()

        # hot_water_temp resolved from BufferSystem (DeviceType=95)
        assert client._device_for("hot_water_temp") == DEVICE_ID_BUFFER
        # hp_state resolved from Rubin (DeviceType=97)
        assert client._device_for("hp_state") == DEVICE_ID_RUBIN

    @pytest.mark.asyncio
    async def test_split_dt95_routes_twe_to_dhw_device(self):
        """Two DT95 devices: TWE datapoints route to the DHW device (PowermoduleFunctionType=2)."""
        session = _FakeSession(
            post=[
                _mock_response(LOGIN_OK),
                _mock_response(CONFIGS_RUBIN_DT95),
                _mock_response(CONFIGS_RUBIN_DT97),
            ],
            get=[_mock_response(DEVICES_RUBIN_SPLIT)],
        )
        client = _client_with_session(session)
        await client.connect()

        # TWE datapoints routed to the dedicated DHW device
        assert client._device_for("hot_water_temp") == DEVICE_ID_BUFFER_DHW
        assert client._device_for("dhw_setpoint") == DEVICE_ID_BUFFER_DHW
        # Heating datapoints routed to the first DT95 (Heizen) via dtype_device fallback
        assert client._device_for("buffer_temp") == DEVICE_ID_BUFFER
        # HP datapoints routed to the DT97 device
        assert client._device_for("hp_state") == DEVICE_ID_RUBIN


# ── Rubin sensor parsing ──────────────────────────────────────────────────────


class TestReadSensorsRubin:
    @pytest.mark.asyncio
    async def test_read_sensors_parses_rubin_fields(self):
        """Rubin-specific fields are parsed when their GUIDs are in self._dp."""
        client = _client_with_session(_FakeSession(), device_id=DEVICE_ID)
        client._connected = True
        client._dp = dict(_DP)
        client._dp["is_defrosting"] = "beff28be-32db-410d-b7ab-4304481e4b4a"
        client._dp["compressor_hours"] = "7d291fb2-8756-4d80-b3d6-d71c9575ae88"
        client._dp_dtype = {}
        client._dtype_device = {}

        response = {
            "ResponseData": [
                {"DatapointConfigId": "beff28be-32db-410d-b7ab-4304481e4b4a", "Value": True},
                {"DatapointConfigId": "7d291fb2-8756-4d80-b3d6-d71c9575ae88", "Value": 1234.5},
            ],
            "StatusCode": 0,
        }
        sensors = client._parse_sensors(response)

        assert sensors.is_defrosting is True
        assert sensors.compressor_hours == pytest.approx(1234.5)


# ── Global alarm & fan power ─────────────────────────────────────────────────


class TestReadSensorsAlarmAndFan:
    @pytest.mark.asyncio
    async def test_alarm_active_with_fault_code(self):
        read_body = _make_read_response({"global_alarm": True, "alarm_number": 42, "fan_power": 55.5})
        session = _FakeSession(post=[_mock_response(read_body)])
        client = _client_with_session(session, device_id=DEVICE_ID)
        client._connected = True

        sensors = await client.read_sensors()

        assert sensors.global_alarm is True
        assert sensors.alarm_number == 42
        assert sensors.fan_power == pytest.approx(55.5)

    @pytest.mark.asyncio
    async def test_alarm_and_fan_none_when_datapoints_absent(self):
        read_body = _make_read_response({"outside_temp": 5.0})
        session = _FakeSession(post=[_mock_response(read_body)])
        client = _client_with_session(session, device_id=DEVICE_ID)
        client._connected = True

        sensors = await client.read_sensors()

        assert sensors.global_alarm is None
        assert sensors.alarm_number is None
        assert sensors.fan_power is None

    def test_new_datapoint_guids_match_spec(self):
        assert _DP["global_alarm"] == "df73b450-8665-446e-9119-82327b842b87"
        assert _DP["alarm_number"] == "87a7fe74-493d-42ec-9661-51a5b3622414"
        assert _DP["fan_power"] == "5f8144fc-bec7-46c3-b5f5-0fb6b1179c4e"

    def test_new_sensors_have_no_wkn_alias(self):
        from kermi_bridge.kermi_client import _DP_TO_WKN

        assert _DP_TO_WKN["global_alarm"] == []
        assert _DP_TO_WKN["alarm_number"] == []
        assert _DP_TO_WKN["fan_power"] == []
