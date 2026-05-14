"""Load and validate kermi_bridge config.yaml."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

_LOGGER = logging.getLogger(__name__)

_VALID_CIRCUITS = {"MK1", "MK2", "HK"}


class ConfigError(Exception):
    """Raised when the configuration file is invalid."""


def _validate(raw: dict) -> dict:
    bridge = raw.get("kermi_bridge")
    if not isinstance(bridge, dict):
        raise ConfigError("Missing required key 'kermi_bridge'")

    for key in ("host", "password"):
        if key not in bridge:
            raise ConfigError(f"kermi_bridge → '{key}' is required")
        if not isinstance(bridge[key], str):
            raise ConfigError(f"kermi_bridge → '{key}' must be a string")

    if "device_id" in bridge and not isinstance(bridge["device_id"], str):
        raise ConfigError("kermi_bridge → 'device_id' must be a string")

    poll = int(bridge.get("poll_interval_s", 30))
    if poll < 10:
        raise ConfigError(f"kermi_bridge → poll_interval_s must be >= 10, got {poll}")
    bridge["poll_interval_s"] = poll

    mf = int(bridge.get("max_failures", 5))
    if mf < 1:
        raise ConfigError(f"kermi_bridge → max_failures must be >= 1, got {mf}")
    bridge["max_failures"] = mf

    ts = int(bridge.get("timeout_s", 10))
    if ts < 1:
        raise ConfigError(f"kermi_bridge → timeout_s must be >= 1, got {ts}")
    bridge["timeout_s"] = ts

    circuits_raw = bridge.get("circuits", ("MK1", "MK2"))
    if not isinstance(circuits_raw, (list, tuple)):
        raise ConfigError("kermi_bridge → circuits must be a list")
    circuits = list(circuits_raw)
    if not circuits:
        raise ConfigError("kermi_bridge → circuits must not be empty")
    for c in circuits:
        if c not in _VALID_CIRCUITS:
            raise ConfigError(
                f"kermi_bridge → invalid circuit '{c}'. Valid: {sorted(_VALID_CIRCUITS)}"
            )
    bridge["circuits"] = circuits

    return raw


def load_config(path: str | Path) -> dict[str, Any]:
    """Load and validate kermi_bridge config.

    Args:
        path: Path to the YAML config file.

    Returns:
        Validated config dict with defaults applied.

    Raises:
        ConfigError: If file is missing, unparseable, or fails validation.
    """
    path = Path(path)

    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must be a YAML mapping, got {type(raw).__name__}")

    _validate(raw)

    _LOGGER.debug("KermiBridge configuration loaded from %s", path)
    return raw
