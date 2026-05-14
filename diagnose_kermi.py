import asyncio
import json
import logging
from kermi_bridge.kermi_client import KermiClient, _DP

logging.basicConfig(level=logging.DEBUG)


async def diagnose_kermi(host, password):
    async with KermiClient(host, password) as client:
        print(f"\n--- Connection Successful ---")
        print(f"Device ID: {client._device_id}")

        # 1. List all devices
        try:
            devices = await client._get("Device/GetAllDevices")
            print(f"\n--- Devices found via GetAllDevices ---")
            print(json.dumps(devices, indent=2))
        except Exception as e:
            print(f"GetAllDevices failed: {e}")

        # 2. Speculative favorites discovery
        try:
            print(f"\n--- Attempting Datapoint Discovery via Favorite/GetFavorites ---")
            favs = await client._post(
                "Favorite/GetFavorites",
                {"WithDetails": True, "OnlyHomeScreen": False},
            )
            print(json.dumps(favs, indent=2))
        except Exception as e:
            print(f"Favorite/GetFavorites discovery not available: {e}")

        # 3. Raw ReadValues response — distinguishes GUID mismatch from device_id mismatch
        print(f"\n--- Raw ReadValues response ---")
        try:
            raw = await client.read_sensors_raw()
            items = raw.get("ResponseData") or []
            print(f"Top-level keys: {list(raw.keys())}")
            print(f"ResponseData item count: {len(items)}")
            if items:
                reverse_dp = {v: k for k, v in _DP.items()}
                print("\nReturned datapoints:")
                for item in items:
                    cfg_id = item.get("DatapointConfigId", "?")
                    value  = item.get("Value")
                    name   = reverse_dp.get(cfg_id, "UNKNOWN GUID")
                    print(f"  {cfg_id}  [{name}]  = {value}")
            else:
                print("ResponseData is empty — device returned no values.")
                print("Full raw response:")
                print(json.dumps(raw, indent=2))
        except Exception as e:
            print(f"ReadValues raw dump failed: {e}")

        # 4. Parsed sensor summary
        print(f"\n--- Parsed sensor summary ---")
        try:
            sensors = await client.read_sensors()
            found, missing = [], []
            for field_name, value in sensors.__dict__.items():
                if field_name == "timestamp":
                    continue
                (found if value is not None else missing).append(field_name)
            print(f"Successfully read {len(found)} datapoints: {', '.join(found) or 'none'}")
            if missing:
                print(f"Returned None for {len(missing)} datapoints: {', '.join(missing)}")
        except Exception as e:
            print(f"Bulk read failed: {e}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python diagnose_kermi.py <IP> <PASSWORD>")
    else:
        asyncio.run(diagnose_kermi(sys.argv[1], sys.argv[2]))
