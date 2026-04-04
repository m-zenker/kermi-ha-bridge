"""Tests for KermiClient — all HTTP calls are mocked; no live device needed."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from kermi_bridge.kermi_client import (
    EnergyMode,
    KermiAuthError,
    KermiClient,
    KermiConnectionError,
    KermiSensors,
    KermiWriteError,
    WezMode,
    _CIRCUIT_TO_CURVE_DP,
    _DP,
    _TYPE_BOOL,
    _TYPE_FLOAT,
    _TYPE_INT,
    _WEZ_TO_BETRIEBSART_DP,
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


def _make_read_response(values: dict[str, Any]) -> dict:
    """Build a ReadValues ResponseData payload from {dp_name: value} dict."""
    return {
        "ResponseData": [
            {"DatapointConfigId": _DP[name], "Value": value}
            for name, value in values.items()
        ],
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
            post=[_mock_response(LOGIN_OK)],        # POST Security/Login
            get=[_mock_response(DEVICES_RESPONSE)], # GET Device/GetAllDevices
        )
        client = _client_with_session(session)
        await client.connect()

        assert client._connected is True
        assert client._device_id == DEVICE_ID

    @pytest.mark.asyncio
    async def test_connect_uses_provided_device_id(self):
        session = _FakeSession(
            post=[_mock_response(LOGIN_OK)],  # POST Security/Login — no GetAllDevices call
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
        read_body = _make_read_response({
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
        })
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
            post=[_mock_response(LOGIN_OK), _mock_response(read_body)],
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
                return cm_401      # First call: 401
            elif call_count == 2:
                return cm_login    # Re-login
            else:
                return cm_retry    # Retry of original request

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
        assert EnergyMode.OFF     == 0
        assert EnergyMode.ECO     == 1
        assert EnergyMode.NORMAL  == 2
        assert EnergyMode.COMFORT == 3
        assert EnergyMode.CUSTOM  == 4

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
        assert WezMode.AUTO      == 0
        assert WezMode.HP_ONLY   == 1
        assert WezMode.BOTH      == 2
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
        read_body = _make_read_response({
            "outside_temp": 5.0,
            "wez1_status": 1,
            "wez1_operating_hours": 786.5,
            "wez1_betriebsart": 0,
            "wez2_status": 0,
            "wez2_operating_hours": 0.0,
            "wez2_betriebsart": 2,
        })
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
        read_body = _make_read_response({
            "wp_return_temp": 38.5,
            "wp_flow_temp_lc": 42.1,
            "cop_heating_live": 3.8,
            "cop_dhw_live": 2.9,
        })
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
        read_body = _make_read_response({
            "cop_heating_live": 0.0,
            "cop_dhw_live": 0.0,
        })
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
