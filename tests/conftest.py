"""Shared pytest fixtures for the kermi-ha-bridge test suite."""

from __future__ import annotations

import sys
import types

# Stub appdaemon so app modules can be imported outside the AppDaemon runtime.
if "appdaemon" not in sys.modules:
    _fake_hass_cls = type("Hass", (), {})
    _hassapi_mod = types.ModuleType("appdaemon.plugins.hass.hassapi")
    _hassapi_mod.Hass = _fake_hass_cls  # type: ignore[attr-defined]
    sys.modules["appdaemon"] = types.ModuleType("appdaemon")
    sys.modules["appdaemon.plugins"] = types.ModuleType("appdaemon.plugins")
    sys.modules["appdaemon.plugins.hass"] = types.ModuleType("appdaemon.plugins.hass")
    sys.modules["appdaemon.plugins.hass.hassapi"] = _hassapi_mod
