#!/usr/bin/env python3
"""Deploy kermi-ha-bridge to the live HA instance via Samba.

Uploads kermi_bridge app files, creates config.yaml, patches apps.yaml,
then restarts AppDaemon.

Required env vars: EM_SMB_USER, EM_SMB_PASSWORD, EM_HA_TOKEN
Required env vars: KERMI_HOST, KERMI_PASSWORD, KERMI_CIRCUITS (comma-separated)
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import sys
import urllib.request

import yaml
from smb.SMBConnection import SMBConnection

REPO_ROOT = pathlib.Path(__file__).parent.parent
APP_DIR = REPO_ROOT / "apps" / "kermi_bridge"

HA_HOST = "homeassistant"
HA_PORT = 8123
SMB_SHARE = "addon_configs"
AD_BASE = "a0d7b954_appdaemon/apps"


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: required env var {name!r} not set.", file=sys.stderr)
        sys.exit(1)
    return value


SMB_USER = _require_env("EM_SMB_USER")
SMB_PASSWORD = _require_env("EM_SMB_PASSWORD")
TOKEN = _require_env("EM_HA_TOKEN")
KERMI_HOST = _require_env("KERMI_HOST")
KERMI_PASSWORD = _require_env("KERMI_PASSWORD")
KERMI_CIRCUITS = os.environ.get("KERMI_CIRCUITS", "MK1")

KERMI_CONFIG = f"""\
kermi_bridge:
  host: {KERMI_HOST}
  password: "{KERMI_PASSWORD}"
  circuits: [{KERMI_CIRCUITS}]
  timeout_s: 10
  poll_interval_s: 30
  max_failures: 5
"""

print("Connecting to HA via SMB …\n")
conn = SMBConnection(SMB_USER, SMB_PASSWORD, "deploy", "homeassistant", use_ntlm_v2=True)
assert conn.connect("homeassistant", 445)

print("[1/3] Uploading kermi_bridge source files …")
for fpath in sorted(APP_DIR.rglob("*.py")):
    rel = fpath.relative_to(APP_DIR.parent)
    remote = f"{AD_BASE}/{rel}"
    with open(fpath, "rb") as f:
        conn.storeFile(SMB_SHARE, remote, f)
    print(f"  uploaded {remote}")

print("\n[2/3] Uploading kermi_bridge config.yaml …")
config_remote = f"{AD_BASE}/kermi_bridge/config.yaml"
conn.storeFile(SMB_SHARE, config_remote, io.BytesIO(KERMI_CONFIG.encode()))
print(f"  uploaded {config_remote}")

print("\n[3/3] Patching apps.yaml …")
live_buf = io.BytesIO()
conn.retrieveFile(SMB_SHARE, f"{AD_BASE}/apps.yaml", live_buf)
live_yaml = live_buf.getvalue().decode()
live_apps = yaml.safe_load(live_yaml) or {}

if "kermi_bridge" in live_apps:
    print("  kermi_bridge entry already present — skipping")
else:
    new_entry = (REPO_ROOT / "apps.yaml").read_text()
    patched = live_yaml.rstrip() + "\n\n" + new_entry + "\n"
    conn.storeFile(SMB_SHARE, f"{AD_BASE}/apps.yaml", io.BytesIO(patched.encode()))
    print("  kermi_bridge entry added to apps.yaml")

conn.close()

print("\nRestarting AppDaemon …")
req = urllib.request.Request(
    f"http://{HA_HOST}:{HA_PORT}/api/services/hassio/addon_restart",
    data=json.dumps({"addon": "a0d7b954_appdaemon"}).encode(),
    headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    method="POST",
)
resp = urllib.request.urlopen(req)
print(f"  restart triggered (HTTP {resp.status}): {resp.read().decode()[:40]}")
print("\nDone. kermi-ha-bridge deployed. AppDaemon will restart in ~2 minutes.")
