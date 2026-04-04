"""Kermi x-center HTTP API client.

Talks directly to the heat pump's local web interface — no Modbus, no cloud.
The x-center exposes a cookie-authenticated REST API on port 80.

Typical usage::

    async with KermiClient("192.168.1.121", "password") as client:
        sensors = await client.read_sensors()
        await client.set_energy_mode(EnergyMode.COMFORT)

All I/O methods re-authenticate automatically if the session has expired.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

# Fixed for local (non-portal) access — identifies the local home server.
_DESTINATION_ID = "00000000-0000-0000-0000-000000000000"

# $type strings required by WriteValues for each Python type.
_TYPE_BOOL = (
    "BMS.Shared.DatapointCore.DatapointValue`1"
    "[[System.Boolean, mscorlib]], BMS.Shared"
)
_TYPE_INT = (
    "BMS.Shared.DatapointCore.DatapointValue`1"
    "[[System.Int32, mscorlib]], BMS.Shared"
)
_TYPE_FLOAT = (
    "BMS.Shared.DatapointCore.DatapointValue`1"
    "[[System.Single, mscorlib]], BMS.Shared"
)

# Datapoint GUIDs — discovered by live inspection of the x-center JS source and API.
_DP = {
    # Monitoring
    "hp_state":               "41258683-9b38-4065-80d2-34c9a7e6ec2c",
    "outside_temp":           "777c1a8e-ec1c-4a15-9bcc-4ec5b8e0e4f4",
    "outside_temp_avg":       "7b712484-4c0e-4b8d-9425-25f9f7072777",
    "compressor_power_kw":    "3576624b-1af4-4406-8e8b-12500acd4840",
    "heating_output_kw":      "1d86a071-53bc-4ab1-b705-1e9c7c104d02",
    "cop":                    "34760a09-8f79-424f-a1b0-5f1a9339d864",
    "cop_heating_avg":        "c95e6f93-eeb0-400a-a061-808c796a6739",
    "scop":                   "6728fd40-0370-40ca-aea6-d87670224b13",
    "flow_temp_mk1":          "4e53d1c7-f461-4e00-ad71-2e0375be8e0c",
    "flow_temp_mk2":          "cf6fda09-6e9d-4477-b643-4839c4cc646f",
    "hot_water_temp":         "83a34595-924a-421e-b9c1-44c2a49f97ad",
    "buffer_temp":            "fc1c59db-33d8-41f4-afb9-0513d18e8095",
    "heating_setpoint":       "985cce22-e260-461f-bc25-44b72a13b8f3",
    "setpoint_mk1":           "c068737a-aca4-4084-88d9-44cfe9b72a4c",
    "smart_grid_status":      "01abb662-cc1a-4225-a886-a9c2fa245b8d",
    "evu_status":             "c2d20aa6-8dd4-4513-a3fa-a45ba942b3ee",
    "lifetime_electricity_kwh": "ac0a8989-e55d-4c8d-9550-071cfc57c01c",
    "lifetime_heat_kwh":      "ce268bd3-8262-4926-ae2c-e73075c89167",
    "electricity_heating_kwh": "dbf925c9-f24e-456c-ac49-f7702adeb9d1",
    "electricity_dhw_kwh":    "b94586b8-1a4c-4c4f-b56c-07895cb71a89",
    # WEZ monitoring (read, level 10)
    "wez1_status":            "7b61bd2f-3f0c-4cda-85ac-790dd3f521e8",
    "wez1_operating_hours":   "90437f26-465c-456d-acee-fb5a911794c9",
    "wez2_status":            "3b981e54-70b3-47be-a611-3efe66b036a3",
    "wez2_operating_hours":   "23903818-d50d-47f2-b5ae-a0763fec44ca",
    # WEZ control (read/write, level 10)
    "wez1_betriebsart":       "baf5cfb8-940c-48cf-8a4f-506a5f78d336",
    "wez2_betriebsart":       "dfb042d3-8f06-41a2-9ba3-2df0660f5ed2",
    # WEZ monitoring (read, level 10) — additional sensors
    "wp_return_temp":         "6ca1372b-894d-4f27-add3-257fff9905c1",
    "wp_flow_temp_lc":        "6576ccc5-048a-482e-ac0d-ef4dc0de16c4",
    "cop_heating_live":       "cd908274-744c-45db-8ad2-564a4f81b210",
    "cop_dhw_live":           "5d8bd3ad-7bf4-41ff-8883-82f0d5bc3548",
    # Control (writable at user level 10)
    "energy_mode_mk1":        "6879e0cf-d7d2-4809-8a72-f82dec836f19",
    "energy_mode_mk2":        "adeda139-96e1-47f6-b3bd-025bb0f40e28",
    "energy_mode_hk":         "836b65fd-0cc7-4232-9b49-d87fdbf425ad",
    "dhw_setpoint":           "ca4dd370-2cd7-4a6b-b091-f9df74150265",  # float °C [0–85]
    "dhw_oneshot_trigger":    "2c2d38d5-ce4c-4195-9338-3081eb6987a4",  # bool (write True)
    "quiet_mode":             "8b94090b-4115-44b0-98f1-4cceab305488",  # bool
    "heating_curve_shift_mk1": "ed643ada-7265-43b3-b6aa-13bcc08ed53e",  # int [-5, +5]
    "heating_curve_shift_mk2": "3ea5f70b-d320-4592-8b19-06a8e3d26b53",
    "heating_curve_shift_hk":  "04ba9dab-2dd7-4bc3-9b42-d0a5a8d7c5f9",
}

_WEZ_TO_BETRIEBSART_DP = {1: "wez1_betriebsart", 2: "wez2_betriebsart"}

_CIRCUIT_TO_MODE_DP = {
    "MK1": "energy_mode_mk1",
    "MK2": "energy_mode_mk2",
    "HK":  "energy_mode_hk",
}

_CIRCUIT_TO_CURVE_DP = {
    "MK1": "heating_curve_shift_mk1",
    "MK2": "heating_curve_shift_mk2",
    "HK":  "heating_curve_shift_hk",
}

_READ_DATAPOINTS = [
    "hp_state", "outside_temp", "outside_temp_avg",
    "compressor_power_kw", "heating_output_kw",
    "cop", "cop_heating_avg", "scop",
    "flow_temp_mk1", "flow_temp_mk2",
    "hot_water_temp", "buffer_temp",
    "heating_setpoint", "setpoint_mk1",
    "smart_grid_status", "evu_status",
    "lifetime_electricity_kwh", "lifetime_heat_kwh",
    "electricity_heating_kwh", "electricity_dhw_kwh",
    "energy_mode_mk1", "energy_mode_mk2", "energy_mode_hk",
    "wez1_status", "wez1_operating_hours", "wez1_betriebsart",
    "wez2_status", "wez2_operating_hours", "wez2_betriebsart",
    "wp_return_temp", "wp_flow_temp_lc", "cop_heating_live", "cop_dhw_live",
]


class EnergyMode(IntEnum):
    """Heat pump energy mode — controls setpoint offset per heating circuit."""
    OFF     = 0  # Circuit disabled
    ECO     = 1  # Default efficiency mode (0 K offset)
    NORMAL  = 2  # Normal operation (0 K offset)
    COMFORT = 3  # Solar surplus mode (+2 K offset, absorbs more heat)
    CUSTOM  = 4  # User-defined offset (configured on device)


class WezMode(IntEnum):
    """Betriebsart for an external heat generator (WEZ)."""
    AUTO      = 0  # Heat pump decides automatically
    HP_ONLY   = 1  # Heat pump only — WEZ blocked
    BOTH      = 2  # HP + WEZ both permitted (parallel bivalent)
    SECONDARY = 3  # WEZ as backup when HP is insufficient


@dataclass
class KermiSensors:
    """Snapshot of heat pump sensor values from a single ReadValues call."""
    # Temperatures (°C)
    outside_temp: float | None = None
    outside_temp_avg: float | None = None
    flow_temp_mk1: float | None = None
    flow_temp_mk2: float | None = None
    hot_water_temp: float | None = None
    buffer_temp: float | None = None
    heating_setpoint: float | None = None
    setpoint_mk1: float | None = None
    # Power (kW)
    compressor_power_kw: float | None = None
    heating_output_kw: float | None = None
    # Efficiency
    cop: float | None = None
    cop_heating_avg: float | None = None
    scop: float | None = None
    # Lifetime energy (kWh)
    lifetime_electricity_kwh: float | None = None
    lifetime_heat_kwh: float | None = None
    electricity_heating_kwh: float | None = None
    electricity_dhw_kwh: float | None = None
    # State
    hp_state: int | None = None
    smart_grid_status: int | None = None
    evu_status: bool | None = None
    # Control readback
    energy_mode_mk1: EnergyMode | None = None
    energy_mode_mk2: EnergyMode | None = None
    energy_mode_hk: EnergyMode | None = None
    # WEZ (external heat generators)
    wez1_status: int | None = None
    wez1_operating_hours: float | None = None
    wez1_betriebsart: WezMode | None = None
    wez2_status: int | None = None
    wez2_operating_hours: float | None = None
    wez2_betriebsart: WezMode | None = None
    # WEZ additional monitoring (heat pump sensors)
    wp_return_temp: float | None = None
    wp_flow_temp_lc: float | None = None
    cop_heating_live: float | None = None
    cop_dhw_live: float | None = None
    # Metadata
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class KermiError(Exception):
    """Base exception for all Kermi client errors."""


class KermiAuthError(KermiError):
    """Login failed — wrong password or session could not be established."""


class KermiConnectionError(KermiError):
    """Network error reaching the heat pump."""


class KermiWriteError(KermiError):
    """The device rejected a WriteValues request."""


class KermiClient:
    """Async HTTP client for the Kermi x-center local API.

    Args:
        host: IP address or hostname of the x-center (e.g. "192.168.1.121").
        password: Web UI password (last 4 chars of serial number by default).
        device_id: Heat pump device UUID. If not supplied, it is discovered
                   automatically on first connect via GetAllDevices.
        timeout: Per-request timeout in seconds.
    """

    def __init__(
        self,
        host: str,
        password: str,
        device_id: str | None = None,
        timeout: int = 10,
    ) -> None:
        self._base = f"http://{host}/api"
        self._dest = _DESTINATION_ID
        self._password = password
        self._device_id = device_id
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None
        self._connected = False

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self) -> KermiClient:
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Open an aiohttp session and authenticate."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                cookie_jar=aiohttp.CookieJar(unsafe=True),
            )
        await self._login()
        if self._device_id is None:
            self._device_id = await self._discover_device_id()
        self._connected = True
        _LOGGER.debug("Kermi: connected to %s, device_id=%s", self._base, self._device_id)

    async def close(self) -> None:
        """Logout and close the HTTP session."""
        if self._session and not self._session.closed:
            try:
                await self._post("Security/Logout", {})
            except KermiError:
                pass
            await self._session.close()
        self._connected = False

    # ── Public API ────────────────────────────────────────────────────────────

    async def read_sensors(self) -> KermiSensors:
        """Read all monitored datapoints in a single API call.

        Returns:
            A :class:`KermiSensors` dataclass populated from the device response.
        """
        await self._ensure_connected()
        payload = {
            "DatapointValues": [
                {"DatapointConfigId": _DP[name], "DeviceId": self._device_id}
                for name in _READ_DATAPOINTS
            ]
        }
        data = await self._post("Datapoint/ReadValues", payload)
        return self._parse_sensors(data)

    async def set_energy_mode(
        self,
        mode: EnergyMode,
        circuits: list[str] | None = None,
    ) -> None:
        """Set the energy mode for one or more heating circuits.

        Args:
            mode: The desired :class:`EnergyMode` (e.g. ``EnergyMode.COMFORT``).
            circuits: List of circuit names to update. Defaults to ``["MK1", "MK2"]``.
                      Valid values: ``"MK1"``, ``"MK2"``, ``"HK"``.
        """
        await self._ensure_connected()
        if circuits is None:
            circuits = ["MK1", "MK2"]

        unknown = set(circuits) - set(_CIRCUIT_TO_MODE_DP)
        if unknown:
            raise ValueError(f"Unknown circuits: {unknown}. Valid: {set(_CIRCUIT_TO_MODE_DP)}")

        payload = {
            "DatapointValues": [
                {
                    "$type": _TYPE_INT,
                    "DatapointConfigId": _DP[_CIRCUIT_TO_MODE_DP[c]],
                    "DeviceId": self._device_id,
                    "Value": int(mode),
                }
                for c in circuits
            ]
        }
        data = await self._post("Datapoint/WriteValues", payload)
        if data.get("StatusCode", 1) != 0:
            raise KermiWriteError(
                f"WriteValues failed: {data.get('DisplayText')} "
                f"({(data.get('ExceptionData') or {}).get('ErrorCode')})"
            )
        _LOGGER.debug("Kermi: EnergyMode set to %s for circuits %s", mode.name, circuits)

    async def set_wez_mode(self, wez: int, mode: WezMode) -> None:
        """Set the Betriebsart (operating mode) for WEZ 1 or WEZ 2.

        Args:
            wez: WEZ unit number; must be 1 or 2.
            mode: The desired :class:`WezMode`.
        """
        await self._ensure_connected()
        if wez not in _WEZ_TO_BETRIEBSART_DP:
            raise ValueError(f"Unknown WEZ unit: {wez}. Valid: 1, 2")
        payload = {
            "DatapointValues": [
                {
                    "$type": _TYPE_INT,
                    "DatapointConfigId": _DP[_WEZ_TO_BETRIEBSART_DP[wez]],
                    "DeviceId": self._device_id,
                    "Value": int(mode),
                }
            ]
        }
        data = await self._post("Datapoint/WriteValues", payload)
        if data.get("StatusCode", 1) != 0:
            raise KermiWriteError(
                f"WriteValues failed: {data.get('DisplayText')} "
                f"({(data.get('ExceptionData') or {}).get('ErrorCode')})"
            )
        _LOGGER.debug("Kermi: WEZ%d Betriebsart set to %s", wez, mode.name)

    async def set_dhw_setpoint(self, temp: float) -> None:
        """Set the domestic hot water setpoint temperature.

        Args:
            temp: Target temperature in °C. Must be in the range [0, 85].
        """
        await self._ensure_connected()
        if not (0 <= temp <= 85):
            raise ValueError(f"DHW setpoint {temp} out of range [0, 85]")
        payload = {
            "DatapointValues": [
                {
                    "$type": _TYPE_FLOAT,
                    "DatapointConfigId": _DP["dhw_setpoint"],
                    "DeviceId": self._device_id,
                    "Value": float(temp),
                }
            ]
        }
        data = await self._post("Datapoint/WriteValues", payload)
        if data.get("StatusCode", 1) != 0:
            raise KermiWriteError(
                f"WriteValues failed: {data.get('DisplayText')} "
                f"({(data.get('ExceptionData') or {}).get('ErrorCode')})"
            )
        _LOGGER.debug("Kermi: DHW setpoint set to %s°C", temp)

    async def trigger_dhw_oneshot(self) -> None:
        """Trigger a single domestic hot water boost cycle."""
        await self._ensure_connected()
        payload = {
            "DatapointValues": [
                {
                    "$type": _TYPE_BOOL,
                    "DatapointConfigId": _DP["dhw_oneshot_trigger"],
                    "DeviceId": self._device_id,
                    "Value": True,
                }
            ]
        }
        data = await self._post("Datapoint/WriteValues", payload)
        if data.get("StatusCode", 1) != 0:
            raise KermiWriteError(
                f"WriteValues failed: {data.get('DisplayText')} "
                f"({(data.get('ExceptionData') or {}).get('ErrorCode')})"
            )
        _LOGGER.debug("Kermi: DHW oneshot triggered")

    async def set_quiet_mode(self, enabled: bool) -> None:
        """Enable or disable compressor quiet/night mode.

        Args:
            enabled: ``True`` to enable quiet mode, ``False`` to disable.
        """
        await self._ensure_connected()
        payload = {
            "DatapointValues": [
                {
                    "$type": _TYPE_BOOL,
                    "DatapointConfigId": _DP["quiet_mode"],
                    "DeviceId": self._device_id,
                    "Value": bool(enabled),
                }
            ]
        }
        data = await self._post("Datapoint/WriteValues", payload)
        if data.get("StatusCode", 1) != 0:
            raise KermiWriteError(
                f"WriteValues failed: {data.get('DisplayText')} "
                f"({(data.get('ExceptionData') or {}).get('ErrorCode')})"
            )
        _LOGGER.debug("Kermi: quiet mode set to %s", enabled)

    async def set_heating_curve_shift(
        self,
        shift: int,
        circuits: list[str] | None = None,
    ) -> None:
        """Parallel-shift the heating curve for one or more circuits.

        Args:
            shift: Offset in Kelvin. Must be in the range [-5, 5].
            circuits: List of circuit names to update. Defaults to ``["MK1", "MK2"]``.
                      Valid values: ``"MK1"``, ``"MK2"``, ``"HK"``.
        """
        await self._ensure_connected()
        if not (-5 <= shift <= 5):
            raise ValueError(f"Heating curve shift {shift} out of range [-5, 5]")
        if circuits is None:
            circuits = ["MK1", "MK2"]

        unknown = set(circuits) - set(_CIRCUIT_TO_CURVE_DP)
        if unknown:
            raise ValueError(f"Unknown circuits: {unknown}. Valid: {set(_CIRCUIT_TO_CURVE_DP)}")

        payload = {
            "DatapointValues": [
                {
                    "$type": _TYPE_INT,
                    "DatapointConfigId": _DP[_CIRCUIT_TO_CURVE_DP[c]],
                    "DeviceId": self._device_id,
                    "Value": int(shift),
                }
                for c in circuits
            ]
        }
        data = await self._post("Datapoint/WriteValues", payload)
        if data.get("StatusCode", 1) != 0:
            raise KermiWriteError(
                f"WriteValues failed: {data.get('DisplayText')} "
                f"({(data.get('ExceptionData') or {}).get('ErrorCode')})"
            )
        _LOGGER.debug("Kermi: heating curve shift set to %s for circuits %s", shift, circuits)

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _ensure_connected(self) -> None:
        if not self._connected:
            await self.connect()

    async def _login(self) -> None:
        url = f"{self._base}/Security/Login/{self._dest}"
        try:
            async with self._session.post(url, json={"Password": self._password}) as resp:
                resp.raise_for_status()
                body = await resp.json()
        except aiohttp.ClientError as exc:
            raise KermiConnectionError(f"Login request failed: {exc}") from exc

        if not body.get("isValid"):
            raise KermiAuthError("Kermi login rejected — check password")

    async def _discover_device_id(self) -> str:
        """Return the DeviceId of the first non-home-server device."""
        data = await self._get("Device/GetAllDevices")
        for device in data:
            # DeviceType 0 is the home server (x-center controller itself); skip it.
            if device.get("DeviceType", 0) != 0:
                device_id = device["DeviceId"]
                _LOGGER.debug("Kermi: discovered device_id=%s (%s)", device_id, device.get("Name"))
                return device_id
        raise KermiConnectionError("No heat pump device found via GetAllDevices")

    async def _get(self, endpoint: str) -> Any:
        url = f"{self._base}/{endpoint}/{self._dest}"
        try:
            async with self._session.get(url) as resp:
                if resp.status == 401:
                    raise KermiAuthError("Session expired (401)")
                resp.raise_for_status()
                body = await resp.json()
        except aiohttp.ClientError as exc:
            raise KermiConnectionError(f"GET {endpoint} failed: {exc}") from exc
        return body.get("ResponseData", body)

    async def _post(self, endpoint: str, payload: dict) -> dict:
        url = f"{self._base}/{endpoint}/{self._dest}"
        try:
            async with self._session.post(url, json=payload) as resp:
                if resp.status == 401:
                    self._connected = False
                    await self._login()
                    self._connected = True
                    # Retry once after re-auth.
                    async with self._session.post(url, json=payload) as retry:
                        retry.raise_for_status()
                        return await retry.json()
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientError as exc:
            raise KermiConnectionError(f"POST {endpoint} failed: {exc}") from exc

    @staticmethod
    def _parse_sensors(response: dict) -> KermiSensors:
        """Build a :class:`KermiSensors` from a ReadValues response dict."""
        items: list[dict] = response.get("ResponseData") or []
        by_config_id = {item["DatapointConfigId"]: item.get("Value") for item in items}

        def _get(name: str) -> Any:
            return by_config_id.get(_DP[name])

        def _float(name: str) -> float | None:
            v = _get(name)
            return float(v) if v is not None else None

        def _int(name: str) -> int | None:
            v = _get(name)
            return int(v) if v is not None else None

        def _bool(name: str) -> bool | None:
            v = _get(name)
            return bool(v) if v is not None else None

        def _mode(name: str) -> EnergyMode | None:
            v = _int(name)
            try:
                return EnergyMode(v) if v is not None else None
            except ValueError:
                return None

        def _wez_mode(name: str) -> WezMode | None:
            v = _int(name)
            try:
                return WezMode(v) if v is not None else None
            except ValueError:
                return None

        return KermiSensors(
            outside_temp=_float("outside_temp"),
            outside_temp_avg=_float("outside_temp_avg"),
            flow_temp_mk1=_float("flow_temp_mk1"),
            flow_temp_mk2=_float("flow_temp_mk2"),
            hot_water_temp=_float("hot_water_temp"),
            buffer_temp=_float("buffer_temp"),
            heating_setpoint=_float("heating_setpoint"),
            setpoint_mk1=_float("setpoint_mk1"),
            compressor_power_kw=_float("compressor_power_kw"),
            heating_output_kw=_float("heating_output_kw"),
            cop=_float("cop"),
            cop_heating_avg=_float("cop_heating_avg"),
            scop=_float("scop"),
            lifetime_electricity_kwh=_float("lifetime_electricity_kwh"),
            lifetime_heat_kwh=_float("lifetime_heat_kwh"),
            electricity_heating_kwh=_float("electricity_heating_kwh"),
            electricity_dhw_kwh=_float("electricity_dhw_kwh"),
            hp_state=_int("hp_state"),
            smart_grid_status=_int("smart_grid_status"),
            evu_status=_bool("evu_status"),
            energy_mode_mk1=_mode("energy_mode_mk1"),
            energy_mode_mk2=_mode("energy_mode_mk2"),
            energy_mode_hk=_mode("energy_mode_hk"),
            wez1_status=_int("wez1_status"),
            wez1_operating_hours=_float("wez1_operating_hours"),
            wez1_betriebsart=_wez_mode("wez1_betriebsart"),
            wez2_status=_int("wez2_status"),
            wez2_operating_hours=_float("wez2_operating_hours"),
            wez2_betriebsart=_wez_mode("wez2_betriebsart"),
            wp_return_temp=_float("wp_return_temp"),
            wp_flow_temp_lc=_float("wp_flow_temp_lc"),
            cop_heating_live=_float("cop_heating_live"),
            cop_dhw_live=_float("cop_dhw_live"),
        )
