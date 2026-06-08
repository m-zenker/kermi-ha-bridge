#!/usr/bin/env python3
"""
Kermi x-center diagnostic script.

Self-contained — stdlib only, no project imports needed.

Usage:
    python3 diagnose_kermi.py <IP_ADDRESS> <PASSWORD>

What it does:
  1. Logs in to the x-center local HTTP API.
  2. Lists every device registered on the controller.
  3. Fires a ReadValues request against EVERY non-home-server device so you
     can see which device_id returns data and whether the datapoint GUIDs match.
  4. Tries the Favorite/GetFavorites endpoint to discover datapoint names on
     devices that may use different GUIDs (includes FavoriteDevice
     VisualizationDatapoints for Rubin firmware).
  5. Fetches the full datapoint catalogue for each DeviceType via
     Datapoint/GetConfigsByDeviceType, showing all available GUIDs and which
     ones the bridge already maps.
"""

import http.cookiejar
import json
import sys
import urllib.request

_DESTINATION_ID = "00000000-0000-0000-0000-000000000000"

# Datapoint GUIDs discovered by live inspection of the x-center JS source and API.
# These are the GUIDs baked into kermi-ha-bridge v0.10.x.
_DP = {
    "hp_state": "41258683-9b38-4065-80d2-34c9a7e6ec2c",
    "outside_temp": "777c1a8e-ec1c-4a15-9bcc-4ec5b8e0e4f4",
    "outside_temp_avg": "7b712484-4c0e-4b8d-9425-25f9f7072777",
    "compressor_power_kw": "3576624b-1af4-4406-8e8b-12500acd4840",
    "heating_output_kw": "1d86a071-53bc-4ab1-b705-1e9c7c104d02",
    "cop": "34760a09-8f79-424f-a1b0-5f1a9339d864",
    "cop_heating_avg": "c95e6f93-eeb0-400a-a061-808c796a6739",
    "scop": "6728fd40-0370-40ca-aea6-d87670224b13",
    "flow_temp_mk1": "4e53d1c7-f461-4e00-ad71-2e0375be8e0c",
    "flow_temp_mk2": "cf6fda09-6e9d-4477-b643-4839c4cc646f",
    "hot_water_temp": "83a34595-924a-421e-b9c1-44c2a49f97ad",
    "buffer_temp": "fc1c59db-33d8-41f4-afb9-0513d18e8095",
    "heating_setpoint": "985cce22-e260-461f-bc25-44b72a13b8f3",
    "setpoint_mk1": "c068737a-aca4-4084-88d9-44cfe9b72a4c",
    "smart_grid_status": "01abb662-cc1a-4225-a886-a9c2fa245b8d",
    "evu_status": "c2d20aa6-8dd4-4513-a3fa-a45ba942b3ee",
    "lifetime_electricity_kwh": "ac0a8989-e55d-4c8d-9550-071cfc57c01c",
    "lifetime_heat_kwh": "ce268bd3-8262-4926-ae2c-e73075c89167",
    "electricity_heating_kwh": "dbf925c9-f24e-456c-ac49-f7702adeb9d1",
    "electricity_dhw_kwh": "b94586b8-1a4c-4c4f-b56c-07895cb71a89",
    "wez1_status": "7b61bd2f-3f0c-4cda-85ac-790dd3f521e8",
    "wez1_operating_hours": "90437f26-465c-456d-acee-fb5a911794c9",
    "wez2_status": "3b981e54-70b3-47be-a611-3efe66b036a3",
    "wez2_operating_hours": "23903818-d50d-47f2-b5ae-a0763fec44ca",
    "wez1_betriebsart": "baf5cfb8-940c-48cf-8a4f-506a5f78d336",
    "wez2_betriebsart": "dfb042d3-8f06-41a2-9ba3-2df0660f5ed2",
    "wp_return_temp": "6ca1372b-894d-4f27-add3-257fff9905c1",
    "wp_flow_temp_lc": "6576ccc5-048a-482e-ac0d-ef4dc0de16c4",
    "cop_heating_live": "cd908274-744c-45db-8ad2-564a4f81b210",
    "cop_dhw_live": "5d8bd3ad-7bf4-41ff-8883-82f0d5bc3548",
    "energy_mode_mk1": "6879e0cf-d7d2-4809-8a72-f82dec836f19",
    "energy_mode_mk2": "adeda139-96e1-47f6-b3bd-025bb0f40e28",
    "energy_mode_hk": "836b65fd-0cc7-4232-9b49-d87fdbf425ad",
    "dhw_setpoint": "ca4dd370-2cd7-4a6b-b091-f9df74150265",
    "dhw_oneshot_trigger": "2c2d38d5-ce4c-4195-9338-3081eb6987a4",
    "quiet_mode": "8b94090b-4115-44b0-98f1-4cceab305488",
    "heating_curve_shift_mk1": "ed643ada-7265-43b3-b6aa-13bcc08ed53e",
    "heating_curve_shift_mk2": "3ea5f70b-d320-4592-8b19-06a8e3d26b53",
    "heating_curve_shift_hk": "04ba9dab-2dd7-4bc3-9b42-d0a5a8d7c5f9",
}

_REVERSE_DP = {v: k for k, v in _DP.items()}

_WKN_TO_DP = {
    "LuftTemperatur": "outside_temp",
    "Aussentemperatur": "outside_temp",
    "VolWo_Temperature_Sensor_S1_Value": "outside_temp",
    "Aussentemperatur_gemittelt": "outside_temp_avg",
    # Rubin (x-change dynamic pro, DeviceType=97)
    "Rubin_CombinedHeatpumpState": "hp_state",
    "Rubin_CurrentCOP": "cop",
    "Rubin_CurrentCOPHeating": "cop_heating_live",
    "Rubin_CurrentCOPTwe": "cop_dhw_live",
    "Rubin_PvIsActive": "smart_grid_status",
    "Rubin_CurrentPowerInverter": "compressor_power_kw",
    "Rubin_SecondaryInletTemp": "wp_return_temp",
    "Rubin_SecondaryOutletTemp": "wp_flow_temp_lc",
    "Rubin_IsDefrosting": "is_defrosting",
    "Rubin_OperationHoursCompressor": "compressor_hours",
    "Rubin_CurrentOutputCapacityHeating": "modulation_pct",
    "Rubin_TemperatureSpreadHeating": "temp_spread",
    "Rubin_PvAvailablePower": "pv_available_power",
    # BufferSystem (DeviceType=95)
    "BufferSystem_TweTemperatureActual": "hot_water_temp",
    "BufferSystem_TweSetpoint": "dhw_setpoint",
    "BufferSystem_HeatingTemperatureActual": "buffer_temp",
    "BufferSystem_HeatingSetpoint": "heating_setpoint",
    "BufferSystem_OneTimeTwe": "dhw_oneshot_trigger",
    "BufferSystem_HeaterElectricalPower": "heater_power",
}


def _find_error_guid(raw, raw_str=None):
    """Return the first failing GUID from an error response, or None."""
    if raw_str is None:
        raw_str = json.dumps(raw)
    for field in ("ConfigId", "DatapointConfigId", "FailingConfigId"):
        val = raw.get(field)
        if isinstance(val, str) and val in _REVERSE_DP:
            return val
    exc = raw.get("Exception") or raw.get("Error") or {}
    if isinstance(exc, dict):
        for field in ("ConfigId", "DatapointConfigId"):
            val = exc.get(field)
            if isinstance(val, str) and val in _REVERSE_DP:
                return val
    for guid in _REVERSE_DP:
        if guid in raw_str:
            return guid
    return None


def _make_opener():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _post(opener, base, endpoint, payload, timeout=10):
    url = f"{base}/{endpoint}/{_DESTINATION_ID}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _get(opener, base, endpoint, timeout=10):
    url = f"{base}/{endpoint}/{_DESTINATION_ID}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with opener.open(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode())
    return body.get("ResponseData", body)


def main(host, password):
    base = f"http://{host}/api"
    opener = _make_opener()

    # ── 1. Login ──────────────────────────────────────────────────────────────
    print("=" * 60)
    print("1. LOGIN")
    print("=" * 60)
    try:
        body = _post(opener, base, "Security/Login", {"Password": password})
    except Exception as exc:
        print(f"ERROR: cannot reach {host}: {exc}")
        sys.exit(1)
    if not body.get("isValid"):
        print("ERROR: login rejected — wrong password?")
        sys.exit(1)
    print("OK")

    # ── 2. Device list ────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("2. DEVICE LIST  (GetAllDevices)")
    print("=" * 60)
    try:
        devices = _get(opener, base, "Device/GetAllDevices")
    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    print(json.dumps(devices, indent=2))

    candidates = [d for d in devices if isinstance(d, dict) and d.get("DeviceType", 0) != 0]
    if not candidates:
        print("\nNo non-home-server devices found — nothing to probe.")
        sys.exit(1)
    print(f"\n{len(candidates)} candidate device(s) to probe (DeviceType != 0).")

    # ── 3. ReadValues per candidate ───────────────────────────────────────────
    print("\n" + "=" * 60)
    print("3. ReadValues — one request per candidate device")
    print("   (shows which device_id returns data and whether GUIDs match)")
    print("=" * 60)
    for device in candidates:
        device_id = device.get("DeviceId", "?")
        name = device.get("Name", "?")
        dtype = device.get("DeviceType", "?")
        print(f"\n--- {name}  (DeviceType={dtype})  id={device_id} ---")
        try:
            payload = {"DatapointValues": [{"DatapointConfigId": guid, "DeviceId": device_id} for guid in _DP.values()]}
            raw = _post(opener, base, "Datapoint/ReadValues", payload)
            items = raw.get("ResponseData") or []
            print(f"Top-level response keys : {list(raw.keys())}")
            print(f"ResponseData item count : {len(items)}")
            if items:
                known = [
                    (i, _REVERSE_DP[i["DatapointConfigId"]]) for i in items if i.get("DatapointConfigId") in _REVERSE_DP
                ]
                unknown = [i for i in items if i.get("DatapointConfigId") not in _REVERSE_DP]
                if known:
                    print(f"  Known GUIDs ({len(known)}):")
                    for item, dp_name in known:
                        print(f"    [{dp_name}] = {item.get('Value')}")
                if unknown:
                    print(f"  UNKNOWN GUIDs ({len(unknown)}) — GUIDs differ on this device:")
                    for item in unknown:
                        print(f"    {item.get('DatapointConfigId')}  = {item.get('Value')}")
            else:
                print("  ResponseData is EMPTY — device_id accepted but returned no values.")
                raw_str = json.dumps(raw)
                if "EX_LO_DATAPOINT_005" in raw_str:
                    failing_guid = _find_error_guid(raw, raw_str)
                    if failing_guid:
                        dp_name = _REVERSE_DP[failing_guid]
                        print(f'  EX_LO_DATAPOINT_005 — "{dp_name}" ({failing_guid}) not found.')
                    else:
                        print("  EX_LO_DATAPOINT_005 — firmware uses different datapoint GUIDs.")
                    print("  Firmware uses different GUIDs — see section 4.")
                print("  Full response:")
                for line in json.dumps(raw, indent=2).splitlines():
                    print(f"    {line}")
        except Exception as exc:
            print(f"  ReadValues failed: {exc}")

    # ── 4. Favorites discovery ────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("4. DATAPOINT DISCOVERY  (Favorite/GetFavorites)")
    print("   (may reveal datapoint names/GUIDs on this firmware)")
    print("=" * 60)
    try:
        favs = _post(
            opener,
            base,
            "Favorite/GetFavorites",
            {"WithDetails": True, "OnlyHomeScreen": False},
        )
        items = favs.get("ResponseData") or []
        dp_items = [i for i in items if isinstance(i, dict) and "FavoriteDatapoint" in str(i.get("$type", ""))]

        # Also harvest VisualizationDatapoints from FavoriteDevice items
        for dev_item in items:
            if not (isinstance(dev_item, dict) and "FavoriteDevice" in str(dev_item.get("$type", ""))):
                continue
            for dp in (dev_item.get("VisualizationDatapoints") or {}).get("$values", []):
                config = dp.get("Config") or {}
                if not config.get("DatapointConfigId"):
                    continue
                dp_items.append(
                    {
                        "DatapointConfigId": config["DatapointConfigId"],
                        "DeviceId": dev_item.get("DeviceId", ""),
                        "DatapointConfig": config,
                        "DatapointValue": dp.get("DatapointValue") or {},
                        "_source": "FavoriteDevice.VisualizationDatapoints",
                    }
                )

        if not dp_items:
            print("No FavoriteDatapoint items found in response.")
            print("Full response:")
            for line in json.dumps(favs, indent=2).splitlines():
                print(f"  {line}")
        else:
            device_names = {d.get("DeviceId", ""): d.get("Name", "?") for d in candidates}

            by_device = {}
            for item in dp_items:
                cfg = item.get("DatapointConfig") or {}
                dev_id = item.get("DeviceId") or cfg.get("DeviceId") or "unknown"
                by_device.setdefault(dev_id, []).append(item)

            guid_to_wkn = {}

            for dev_id, dev_items in by_device.items():
                dev_name = device_names.get(dev_id, "?")
                print(f"\nDevice: {dev_id[:8]}  ({dev_name})")
                print(f"  {'GUID':<36}  {'WellKnownName':<36}  {'Display':<30}  Value")
                print(f"  {'-' * 36}  {'-' * 36}  {'-' * 30}  -----")
                for item in dev_items:
                    cfg = item.get("DatapointConfig") or {}
                    val_obj = item.get("DatapointValue") or {}
                    guid = item.get("DatapointConfigId") or cfg.get("DatapointConfigId") or "?"
                    wkn = cfg.get("WellKnownName", "")
                    display = cfg.get("DisplayName", "")
                    unit = cfg.get("Unit") or ""
                    raw_val = val_obj.get("Value") if "Value" in val_obj else val_obj.get("NumericValue", "?")
                    value_str = f"{raw_val} {unit}".strip() if unit else str(raw_val)

                    guid_to_wkn[guid] = wkn

                    if guid in _REVERSE_DP:
                        tag = f"   <- bridge: {_REVERSE_DP[guid]} (MATCH)"
                    elif wkn in _WKN_TO_DP:
                        tag = f"   <- bridge: {_WKN_TO_DP[wkn]} (GUID MISMATCH)"
                    else:
                        tag = "   (no match in bridge _DP)"

                    source = item.get("_source", "")
                    source_str = f"  [{source}]" if source else ""
                    print(f"  {guid:<36}  {wkn:<36}  {display:<30}  {value_str}{tag}{source_str}")

            print(f"\n{'─' * 60}")
            print("BRIDGE COMPATIBILITY SUMMARY")
            print(f"{'─' * 60}")

            all_fav_guids = set(guid_to_wkn)
            supported, guid_mismatch, not_present = [], [], []
            for dp_key, dp_guid in _DP.items():
                if dp_guid in all_fav_guids:
                    supported.append(dp_key)
                    continue
                wkn_for_key = next((wkn for wkn, k in _WKN_TO_DP.items() if k == dp_key), None)
                if wkn_for_key:
                    alt_guid = next(
                        (g for g, w in guid_to_wkn.items() if w == wkn_for_key and g != dp_guid),
                        None,
                    )
                    if alt_guid:
                        guid_mismatch.append((dp_key, dp_guid, alt_guid))
                        continue
                not_present.append(dp_key)

            if supported:
                print("\n  Supported (GUID match in GetFavorites):")
                for k in supported:
                    print(f"    + {k}")
            if guid_mismatch:
                print("\n  GUID mismatch (WellKnownName matched, GUID differs):")
                for k, old_g, new_g in guid_mismatch:
                    print(f"    ~ {k}: bridge={old_g[:8]}...  firmware={new_g[:8]}...")
            if not_present:
                print(f"\n  Not present in GetFavorites ({len(not_present)} of {len(_DP)}):")
                for k in not_present:
                    print(f"    - {k}")

            unknown_to_bridge = [(g, w) for g, w in guid_to_wkn.items() if g not in _REVERSE_DP]
            if unknown_to_bridge:
                print("\n  UNKNOWN to bridge (in firmware favorites, not in bridge _DP):")
                for guid, wkn in unknown_to_bridge:
                    print(f"    ? {guid}  WellKnownName: {wkn}")

    except Exception as exc:
        print(f"Not available on this firmware: {exc}")

    # ── 5. Full datapoint catalogue per device type ───────────────────────────
    print("\n" + "=" * 60)
    print("5. DATAPOINT CATALOGUE  (Datapoint/GetConfigsByDeviceType)")
    print("   (full list of datapoints supported by each device type)")
    print("=" * 60)
    seen_dtypes = []
    for d in candidates:
        dtype = d.get("DeviceType", 0)
        if dtype and dtype not in seen_dtypes:
            seen_dtypes.append(dtype)

    for dtype in seen_dtypes:
        print(f"\nDeviceType {dtype}:")
        try:
            raw = _post(opener, base, "Datapoint/GetConfigsByDeviceType", {"DeviceType": dtype})
            configs = raw.get("ResponseData") or []
            if not configs:
                print("  (no configs returned)")
                continue
            configs_sorted = sorted(configs, key=lambda c: c.get("Sort", 0))
            print(f"  {'GUID':<36}  {'WellKnownName':<36}  {'DisplayName':<30}  Unit  Bridge")
            print(f"  {'-' * 36}  {'-' * 36}  {'-' * 30}  ----  ------")
            for cfg in configs_sorted:
                guid = cfg.get("DatapointConfigId", "?")
                wkn = cfg.get("WellKnownName", "")
                display = cfg.get("DisplayName", "")
                unit = cfg.get("Unit") or ""
                if guid in _REVERSE_DP:
                    bridge_tag = f"<- bridge: {_REVERSE_DP[guid]}"
                else:
                    bridge_tag = ""
                print(f"  {guid:<36}  {wkn:<36}  {display:<30}  {unit:<4}  {bridge_tag}")
            print(f"  Total: {len(configs)} configs")
        except Exception as exc:
            print(f"  Not available on this firmware: {exc}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        print("Usage: python3 diagnose_kermi.py <IP_ADDRESS> <PASSWORD>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
