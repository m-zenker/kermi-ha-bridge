"""Load and validate kermi_bridge config.yaml using voluptuous."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
import yaml

_LOGGER = logging.getLogger(__name__)

_VALID_CIRCUITS = {"MK1", "MK2", "HK"}


class ConfigError(Exception):
    """Raised when the configuration file is invalid."""


def _poll_interval(value: Any) -> int:
    value = vol.Coerce(int)(value)
    if value < 10:
        raise vol.Invalid(f"poll_interval_s must be >= 10, got {value}")
    return value


def _circuit_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise vol.Invalid("circuits must be a list")
    if not value:
        raise vol.Invalid("circuits must not be empty")
    for c in value:
        if c not in _VALID_CIRCUITS:
            raise vol.Invalid(
                f"Invalid circuit '{c}'. Valid: {sorted(_VALID_CIRCUITS)}"
            )
    return list(value)


_KERMI_BRIDGE_SCHEMA = vol.Schema(
    {
        vol.Required("host"): str,
        vol.Required("password"): str,
        vol.Optional("device_id"): str,
        vol.Optional("poll_interval_s", default=30): _poll_interval,
        vol.Optional("max_failures", default=5): vol.All(
            vol.Coerce(int), vol.Range(min=1)
        ),
        vol.Optional("timeout_s", default=10): vol.All(
            vol.Coerce(int), vol.Range(min=1)
        ),
        vol.Optional("circuits", default=("MK1", "MK2")): _circuit_list,
    },
    extra=vol.ALLOW_EXTRA,
)

_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required("kermi_bridge"): _KERMI_BRIDGE_SCHEMA,
    },
    extra=vol.ALLOW_EXTRA,
)


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

    try:
        config = _CONFIG_SCHEMA(raw)
    except vol.Invalid as exc:
        key_path = " → ".join(str(p) for p in exc.path) if exc.path else "(root)"
        raise ConfigError(
            f"Configuration error at '{key_path}': {exc.msg}"
        ) from exc

    _LOGGER.debug("KermiBridge configuration loaded from %s", path)
    return config
