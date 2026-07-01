---
name: Kermi HTTP API findings
description: Live investigation results for the Kermi x-change dynamic heat pump local HTTP API — endpoints, auth, datapoint GUIDs, control strategy
type: project
---

Kermi x-change dynamic is fully controllable via a local HTTP API — no Modbus, no cloud, no expert password needed for the integration use case.

**Why:** Discovered by live probing of the unit at 192.168.1.121 on 2026-03-16.
**How to apply:** Use this instead of Modbus TCP for Phase 1 integration. Build a Python client using these endpoints.

---

## Device info

| Field | Value |
|---|---|
| IP | 192.168.1.121 |
| Model | x-change dynamic |
| Serial | W20291-15-23-02850 |
| Firmware | 6.10 |
| Controller version | 1.6.2.26 (x-center Interfacemodul) |
| DeviceId (heat pump) | `67b4e4ca-df6e-4fb4-8107-f5a35df73981` |
| destinationId (local) | `00000000-0000-0000-0000-000000000000` |

---

## Authentication

- `POST http://192.168.1.121/api/Security/Login/00000000-0000-0000-0000-000000000000`
- Body: `{"Password": "<password>"}`
- Success: `{"isValid": true, "changePassword": false, "redirectUrl": null}`
- Returns a session cookie (`.AspNetCore.Cookie`) — include in all subsequent requests
- Logged-in user level: **10 (UserAccess)**
- Expert level (20) requires a separate installer password — not needed for our use case

---

## Key API endpoints (all require session cookie)

All URLs follow the pattern: `http://192.168.1.121/api/<Controller>/<Method>/00000000-0000-0000-0000-000000000000`

| Endpoint | Method | Body | Notes |
|---|---|---|---|
| `Security/Login/{dest}` | POST | `{"Password":"..."}` | Auth |
| `Security/Logout` | POST | — | Logout |
| `Device/GetAllDevices/{dest}` | GET | — | Lists all devices |
| `Datapoint/GetConfigsByDeviceType/{dest}` | POST | `{"DeviceType":2}` | All 331 heat pump datapoint configs |
| `Datapoint/ReadValues/{dest}` | POST | `{"DatapointValues":[{"DatapointConfigId":"...","DeviceId":"..."}]}` | Read live values |
| `Datapoint/WriteValues/{dest}` | POST | `{"DatapointValues":[{"$type":"...","DatapointConfigId":"...","DeviceId":"...","Value":...}]}` | Write values |
| `System/GetFrontendSettings/{dest}` | GET | — | Unauthenticated; returns OrgId, version |
| `User/GetUserLevel/{dest}` | GET | — | Returns current user level int |
| `User/UserLevelLogin/{dest}` | POST | `{"Password":"...","UserLevel":20}` | Escalate user level (needs installer password) |
| `Favorite/GetFavorites/{dest}` | POST | `{}` | Returns favorited datapoints with configs |

---

## WriteValues $type values

| Python type | $type string |
|---|---|
| bool | `BMS.Shared.DatapointCore.DatapointValue\`1[[System.Boolean, mscorlib]], BMS.Shared` |
| int | `BMS.Shared.DatapointCore.DatapointValue\`1[[System.Int32, mscorlib]], BMS.Shared` |
| float | `BMS.Shared.DatapointCore.DatapointValue\`1[[System.Single, mscorlib]], BMS.Shared` |

---

## Key datapoint GUIDs

### Monitoring (read-only, level 10)

| Name | GUID | Unit | Notes |
|---|---|---|---|
| HP state | `41258683-9b38-4065-80d2-34c9a7e6ec2c` | int | 1=active |
| Outside temp | `777c1a8e-ec1c-4a15-9bcc-4ec5b8e0e4f4` | °C | |
| Outside temp averaged | `7b712484-4c0e-4b8d-9425-25f9f7072777` | °C | Used for heating curve |
| Compressor power draw | `3576624b-1af4-4406-8e8b-12500acd4840` | kW | |
| Heating output | `1d86a071-53bc-4ab1-b705-1e9c7c104d02` | kW | |
| COP (current) | `34760a09-8f79-424f-a1b0-5f1a9339d864` | — | |
| COP heating averaged | `c95e6f93-eeb0-400a-a061-808c796a6739` | — | |
| SCOP total | `6728fd40-0370-40ca-aea6-d87670224b13` | — | |
| Flow temp MK1 | `4e53d1c7-f461-4e00-ad71-2e0375be8e0c` | °C | |
| Flow temp MK2 | `cf6fda09-6e9d-4477-b643-4839c4cc646f` | °C | |
| Hot water temp actual | `83a34595-924a-421e-b9c1-44c2a49f97ad` | °C | TWE |
| Buffer temp actual | `fc1c59db-33d8-41f4-afb9-0513d18e8095` | °C | |
| Heating setpoint (current) | `985cce22-e260-461f-bc25-44b72a13b8f3` | °C | |
| MK1 setpoint panel | `c068737a-aca4-4084-88d9-44cfe9b72a4c` | °C | |
| Total electricity consumed | `ac0a8989-e55d-4c8d-9550-071cfc57c01c` | kWh | Lifetime |
| Total heat produced | `ce268bd3-8262-4926-ae2c-e73075c89167` | kWh | Lifetime |
| Smart Grid status | `01abb662-cc1a-4225-a886-a9c2fa245b8d` | int | 2=normal |
| EVU lock status | `c2d20aa6-8dd4-4513-a3fa-a45ba942b3ee` | bool | |
| SG Ready input 1 (EVU) | `66814043-cb55-42b0-a972-eef1a6d98e45` | bool | Read: DI9 state |
| SG Ready input 2 (PV) | `2c458879-6417-43a9-a381-0ad768ae0f6d` | bool | Read: DI10 state |

### Control (read/write, level 10)

| Name | GUID | Values | Notes |
|---|---|---|---|
| EnergyMode MK1 | `6879e0cf-d7d2-4809-8a72-f82dec836f19` | 0=Off,1=Eco,2=Normal,3=Comfort,4=Custom | **Primary PV control lever** |
| EnergyMode MK2 | `adeda139-96e1-47f6-b3bd-025bb0f40e28` | same | |
| EnergyMode HK | `836b65fd-0cc7-4232-9b49-d87fdbf425ad` | same | |
| Setpoint offset Normal MK1 | `017c06fc-90d2-4a9f-8a54-5e476324b9cf` | K | Currently 0 |
| Setpoint offset Comfort MK1 | `0251b706-be51-4bbc-8ac5-212eeaf87dfb` | K | Currently +2 |
| Setpoint offset Custom MK1 | `6eafb7b9-e385-4f2c-8c23-eb6f7c129bca` | K | Currently −2 |
| Setpoint offset Eco MK1 | `ae4b4373-3680-4e02-88d8-3c24370e6501` | K | Currently −2 |
| Setpoint offset Normal MK2 | `837506cc-ddbd-416e-a03f-2d52a6058bf2` | K | |
| Setpoint offset Comfort MK2 | `45c79187-175c-4c41-ab69-c18ef4e56523` | K | |
| Setpoint offset Eco MK2 | `faf821a1-08a4-47d4-ad79-51dfe879126d` | K | |
| Setpoint offset Normal HK | `735e89cd-dc32-495a-a4d7-b3a249f0dce3` | K | |
| Setpoint offset Comfort HK | `06a990b4-9ca7-49fd-b700-3c8a990f3944` | K | |
| Setpoint offset Eco HK | `6ab24375-081c-408e-aa13-6307a869f087` | K | |
| **DHW setpoint** | `ca4dd370-2cd7-4a6b-b091-f9df74150265` | °C [0–85], live=42°C | **Raise during solar surplus for thermal storage** |
| DHW enable | `b721846e-db37-4d6d-b1ae-7b0eb9b6c2f1` | bool | Currently True |
| DHW one-shot trigger | `2c2d38d5-ce4c-4195-9338-3081eb6987a4` | bool | Trigger single DHW charge (simpler than one-shot setpoint) |
| Hot water one-shot setpoint | `83049eb3-7f02-4032-98e4-7b39dfc9252d` | °C | Single DHW boost with custom target |
| **Heating curve shift MK1** | `ed643ada-7265-43b3-b6aa-13bcc08ed53e` | int [-5,+5], live=0 | Parallel shift of entire heating curve — complementary to EnergyMode |
| Heating curve shift MK2 | `3ea5f70b-d320-4592-8b19-06a8e3d26b53` | int [-5,+5] | |
| Heating curve shift HK | `04ba9dab-2dd7-4bc3-9b42-d0a5a8d7c5f9` | int [-5,+5] | |
| **Quiet mode** | `8b94090b-4115-44b0-98f1-4cceab305488` | bool, live=False | Reduce compressor/fan noise — useful for night hours |
| Summer mode threshold MK1 (heating off) | `6f96d286-44fc-4a26-9974-ce22f0d0536f` | °C [10–40], live=18°C | Heating disabled above this outside temp |
| Winter mode threshold MK1 (heating on) | `1c305650-0daf-4a58-bf52-243715028530` | °C [10–40], live=16°C | Heating re-enabled below this outside temp |
| Summer mode threshold MK2 (heating off) | `750addf8-97be-485c-a245-36ce365efd2a` | °C [10–40] | |
| Winter mode threshold MK2 (heating on) | `39fa3d8c-ec33-4bc0-917c-2b497b9fe44a` | °C [10–40] | |
| Summer mode state MK1 | `33e1264a-a507-4ef8-9c4c-7065906ef3ab` | bool | True = summer (no heating) |
| Manual season select MK1 | `fc3aa06b-f4a8-42bc-b888-d41164b539be` | 0=auto,1-3=manual, live=0 | Override auto season detection |

### Additional monitoring (read, level 10) — newly discovered

| Name | GUID | Unit | Live value | Notes |
|---|---|---|---|---|
| Operation state | `1433aec1-d8bc-4b15-b9b5-48c39cf0e75e` | int 0–102 | 1 | Granular HP state machine (0=standby) |
| Compressor speed | `02d54a7c-eeac-4776-bdb7-cd6bb4286651` | rps | 0 | Inverter compressor RPM |
| Fan power | `5f8144fc-bec7-46c3-b5f5-0fb6b1179c4e` | % | 0 | Implemented as `sensor.kermi_fan_power` |
| WP flow rate | `01860ced-3dd2-4ab2-9cd8-da0e7cde597e` | l/min | 0 | |
| WP return temp (load circuit) | `6ca1372b-894d-4f27-add3-257fff9905c1` | °C | 35.8 | Inlet of heat pump (return from buffer) |
| WP flow temp (load circuit) | `6576ccc5-048a-482e-ac0d-ef4dc0de16c4` | °C | 36.2 | Outlet of heat pump (to buffer) |
| Electricity — heating only | `dbf925c9-f24e-456c-ac49-f7702adeb9d1` | kWh | 8,886 | Split from total; allows heating vs DHW efficiency calc |
| Electricity — DHW only | `b94586b8-1a4c-4c4f-b56c-07895cb71a89` | kWh | 1,282 | |
| Heat produced — heating only | `1e107669-d310-43f4-9840-22539ff1798d` | kWh | 38,756 | Heating COP = 38756/8886 ≈ 4.36 |
| Heat produced — DHW only | `54dade80-c7cd-443c-8726-d5a6ba2b73c5` | kWh | 5,125 | DHW COP = 5125/1282 ≈ 4.0 |
| COP heating (live, mode-specific) | `cd908274-744c-45db-8ad2-564a4f81b210` | — | — | Only returned while heating (idle = null) |
| COP DHW (live, mode-specific) | `5d8bd3ad-7bf4-41ff-8883-82f0d5bc3548` | — | — | Only returned while heating DHW |
| Alarm active | `df73b450-8665-446e-9119-82327b842b87` | bool | False | Implemented as `binary_sensor.kermi_global_alarm` |
| Alarm number | `87a7fe74-493d-42ec-9661-51a5b3622414` | int | 0 | Fault code when alarm active — implemented as `alarm_number` attribute on `binary_sensor.kermi_global_alarm` |

### Control (level 20 — needs installer password, NOT used)

| Name | GUID | Notes |
|---|---|---|
| SG Ready input 1 write | `66814043-cb55-42b0-a972-eef1a6d98e45` | w20 — skip, use EnergyMode instead |
| SG Ready input 2 write | `2c458879-6417-43a9-a381-0ad768ae0f6d` | w20 — skip |
| Operating mode MK1 | `683ed933-d27e-4c87-90a7-5549ffba869f` | w40 — not needed |

---

## Control strategy for heat pump optimiser

EnergyMode is the correct integration lever (writable at user level 10, confirmed working):

| Solar condition | Action | EnergyMode value |
|---|---|---|
| Large surplus (> threshold) | Preheat buffer, raise setpoints | Comfort (3) → +2K |
| Normal / no surplus | Default operation | Eco (1) |
| EVU lock / grid stress | Reduce heating load | Off (0) or Eco (1) |

**Extended control options (all level 10, newly confirmed 2026-03-17):**

- **DHW setpoint**: Raise `Solltemperatur TWE` to 55–60°C during large surplus for cheaper thermal storage than battery. Currently 42°C. Range 0–85°C.
- **Heating curve shift**: `Parallelverschiebung Heizkurve MK1` shifts the entire heating curve by ±5K — a finer/complementary lever to EnergyMode setpoint offsets. Currently 0.
- **Quiet mode**: `Flüstermodus` reduces compressor/fan noise — can schedule off overnight.
- **Season thresholds**: `Sommerbetrieb MK1 (Heizen Aus)` at 18°C and `Winterbetrieb MK1 (Heizen Ein)` at 16°C are writable — tune start/end of heating season dynamically.

This eliminates the need for SG Ready hardware wiring or Modbus TCP activation.

---

## Live readings at time of investigation (2026-03-16 ~20:30 UTC)

- Outside temp: 8.1°C | Flow MK1: 37.5°C | Setpoint: 35°C
- Hot water (TWE): 50.3°C | Compressor power: 0 kW (idle)
- Lifetime: 43,855 kWh heat produced, 10,161 kWh electricity consumed → implied SCOP 4.32

## Live readings 2026-03-17

- DHW setpoint: 42°C | Heating curve shift: 0 | Quiet mode: off | Season: auto (summer at 18°C, winter at 16°C)
- Split lifetime: heating 38,756 kWh heat / 8,886 kWh elec (COP 4.36); DHW 5,125 kWh heat / 1,282 kWh elec (COP 4.0)
- HP idle (compressor 0 rps, operation state 1)
