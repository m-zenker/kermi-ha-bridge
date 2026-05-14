import asyncio
import json
import logging
from kermi_bridge.kermi_client import KermiClient

# Configure logging to see discovery details
logging.basicConfig(level=logging.DEBUG)

async def diagnose_kermi(host, password):
    async with KermiClient(host, password) as client:
        print(f"\n--- Connection Successful ---")
        print(f"Device ID: {client._device_id}")
        
        # 1. Try to list all devices
        try:
            devices = await client._get("Device/GetAllDevices")
            print(f"\n--- Devices found via GetAllDevices ---")
            print(json.dumps(devices, indent=2))
        except Exception as e:
            print(f"GetAllDevices failed: {e}")

        # 2. Try the speculative Favorites discovery endpoint
        # This endpoint is known to return WellKnownName mapping for many x-center versions
        try:
            print(f"\n--- Attempting Datapoint Discovery via Favorite/GetFavorites ---")
            favs = await client._post("Favorite/GetFavorites", {"WithDetails": True, "OnlyHomeScreen": False})
            print(json.dumps(favs, indent=2))
        except Exception as e:
            print(f"Favorite/GetFavorites discovery not available: {e}")

        # 3. Verify current known GUIDs
        print(f"\n--- Verifying current GUIDs in kermi_client.py ---")
        try:
            sensors = await client.read_sensors()
            found = []
            missing = []
            
            # Since read_sensors returns a dataclass, we check for non-None values
            for field_name, value in sensors.__dict__.items():
                if field_name == "timestamp": continue
                if value is not None:
                    found.append(field_name)
                else:
                    missing.append(field_name)
            
            print(f"Successfully read {len(found)} datapoints: {', '.join(found)}")
            if missing:
                print(f"Failed to read {len(missing)} datapoints (possibly different GUIDs): {', '.join(missing)}")
        except Exception as e:
            print(f"Bulk read failed: {e}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python diagnose_kermi.py <IP> <PASSWORD>")
    else:
        asyncio.run(diagnose_kermi(sys.argv[1], sys.argv[2]))
