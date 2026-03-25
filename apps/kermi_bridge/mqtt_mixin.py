"""MQTT Discovery mixin for AppDaemon apps.

Provides helpers for publishing HA MQTT Discovery configs, state, attributes,
availability, and command subscriptions.  Mix into an AppDaemon app alongside
``hass.Hass``::

    class MyApp(MQTTMixin, hass.Hass): ...

Call ``_mqtt_setup(self.args, slug, device)`` at the top of ``initialize()``
before publishing anything.
"""

from __future__ import annotations

import json
import math
from typing import Any


class MQTTMixin:
    """Mixin that adds MQTT Discovery helpers to AppDaemon apps."""

    # ── Setup ──────────────────────────────────────────────────────────────────

    def _mqtt_setup(self, args: dict, app_slug: str, device: dict) -> None:
        """Parse MQTT config keys and store on the instance."""
        self._mqtt_enabled: bool = bool(args.get("mqtt_discovery", False))
        self._mqtt_ns: str = str(args.get("mqtt_namespace", "mqtt"))
        self._mqtt_prefix: str = str(args.get("mqtt_discovery_prefix", "homeassistant"))
        self._mqtt_slug: str = app_slug
        self._mqtt_device: dict = device

    # ── Topic helpers ──────────────────────────────────────────────────────────

    @property
    def _mqtt_avail_topic(self) -> str:
        return f"{self._mqtt_prefix}/energy_manager/{self._mqtt_slug}/availability"

    def _state_topic(self, uid: str) -> str:
        return f"{self._mqtt_prefix}/energy_manager/{self._mqtt_slug}/sensor/{uid}/state"

    def _attrs_topic(self, uid: str) -> str:
        return (
            f"{self._mqtt_prefix}/energy_manager/{self._mqtt_slug}/sensor/{uid}/attributes"
        )

    def _cmd_topic(self, platform: str, uid: str) -> str:
        return f"{self._mqtt_prefix}/energy_manager/{self._mqtt_slug}/{platform}/{uid}/set"

    def _discovery_topic(self, platform: str, uid: str) -> str:
        return f"{self._mqtt_prefix}/{platform}/{uid}/config"

    # ── Low-level publish ──────────────────────────────────────────────────────

    def _mqtt_publish(self, topic: str, payload: str, retain: bool = True) -> None:
        try:
            self.call_service(
                "mqtt/publish",
                topic=topic,
                payload=payload,
                retain=retain,
                namespace=self._mqtt_ns,
            )
        except Exception as exc:
            self.log(f"MQTT publish failed on {topic}: {exc}", level="WARNING")

    # ── Discovery publishers ────────────────────────────────────────────────────

    def _mqtt_publish_sensor_discovery(
        self,
        uid: str,
        name: str,
        unit: str,
        icon: str,
        dc: str | None,
        sc: str | None,
        json_attrs_topic: str | None = None,
    ) -> None:
        payload: dict = {
            "name": name,
            "unique_id": uid,
            "state_topic": self._state_topic(uid),
            "availability_topic": self._mqtt_avail_topic,
            "icon": icon,
            "device": self._mqtt_device,
        }
        if unit:
            payload["unit_of_measurement"] = unit
        if dc:
            payload["device_class"] = dc
        if sc:
            payload["state_class"] = sc
        if json_attrs_topic:
            payload["json_attributes_topic"] = json_attrs_topic
        self._mqtt_publish(self._discovery_topic("sensor", uid), json.dumps(payload))

    def _mqtt_publish_binary_sensor_discovery(
        self, uid: str, name: str, icon: str, dc: str | None
    ) -> None:
        payload: dict = {
            "name": name,
            "unique_id": uid,
            "state_topic": self._state_topic(uid),
            "availability_topic": self._mqtt_avail_topic,
            "payload_on": "ON",
            "payload_off": "OFF",
            "icon": icon,
            "device": self._mqtt_device,
        }
        if dc:
            payload["device_class"] = dc
        self._mqtt_publish(self._discovery_topic("binary_sensor", uid), json.dumps(payload))

    def _mqtt_publish_button_discovery(self, uid: str, name: str, icon: str) -> None:
        payload: dict = {
            "name": name,
            "unique_id": uid,
            "command_topic": self._cmd_topic("button", uid),
            "availability_topic": self._mqtt_avail_topic,
            "payload_press": "PRESS",
            "icon": icon,
            "device": self._mqtt_device,
        }
        self._mqtt_publish(self._discovery_topic("button", uid), json.dumps(payload))

    def _mqtt_publish_number_discovery(
        self,
        uid: str,
        name: str,
        unit: str,
        min_val: float,
        max_val: float,
        step: float,
        icon: str,
        mode: str = "box",
    ) -> None:
        payload: dict = {
            "name": name,
            "unique_id": uid,
            "command_topic": self._cmd_topic("number", uid),
            "state_topic": self._state_topic(uid),
            "availability_topic": self._mqtt_avail_topic,
            "unit_of_measurement": unit,
            "min": min_val,
            "max": max_val,
            "step": step,
            "mode": mode,
            "icon": icon,
            "device": self._mqtt_device,
        }
        self._mqtt_publish(self._discovery_topic("number", uid), json.dumps(payload))

    def _mqtt_publish_select_discovery(
        self, uid: str, name: str, options: list[str], icon: str
    ) -> None:
        payload: dict = {
            "name": name,
            "unique_id": uid,
            "command_topic": self._cmd_topic("select", uid),
            "state_topic": self._state_topic(uid),
            "availability_topic": self._mqtt_avail_topic,
            "options": options,
            "icon": icon,
            "device": self._mqtt_device,
        }
        self._mqtt_publish(self._discovery_topic("select", uid), json.dumps(payload))

    def _mqtt_publish_switch_discovery(self, uid: str, name: str, icon: str) -> None:
        payload: dict = {
            "name": name,
            "unique_id": uid,
            "command_topic": self._cmd_topic("switch", uid),
            "state_topic": self._state_topic(uid),
            "availability_topic": self._mqtt_avail_topic,
            "payload_on": "ON",
            "payload_off": "OFF",
            "icon": icon,
            "device": self._mqtt_device,
        }
        self._mqtt_publish(self._discovery_topic("switch", uid), json.dumps(payload))

    # ── State publishers ────────────────────────────────────────────────────────

    def _mqtt_set_sensor(self, uid: str, value: Any) -> None:
        """Publish a numeric sensor value, normalising NaN/Inf to 0.0."""
        try:
            val = float(value)
            if math.isnan(val) or math.isinf(val):
                val = 0.0
        except (TypeError, ValueError):
            val = value
        self._mqtt_publish(self._state_topic(uid), str(val))

    def _mqtt_set_sensor_raw(self, uid: str, value_str: str) -> None:
        """Publish a pre-formatted string state."""
        self._mqtt_publish(self._state_topic(uid), value_str)

    def _mqtt_publish_sensor_attributes(self, uid: str, attrs: dict) -> None:
        self._mqtt_publish(self._attrs_topic(uid), json.dumps(attrs))

    def _mqtt_publish_availability(self, payload: str) -> None:
        self._mqtt_publish(self._mqtt_avail_topic, payload)

    # ── Command subscriptions ───────────────────────────────────────────────────

    def _mqtt_subscribe_command(self, platform: str, uid: str, callback: Any) -> None:
        """Subscribe to a command topic; callback receives (event_name, data, kwargs)."""
        topic = self._cmd_topic(platform, uid)
        self.listen_event(
            callback,
            "MQTT_MESSAGE",
            topic=topic,
            namespace=self._mqtt_ns,
        )

    # ── Legacy cleanup ──────────────────────────────────────────────────────────

    def _mqtt_cleanup_legacy(self, entity_ids: list[str]) -> None:
        """Mark old set_state-managed entities unavailable during MQTT migration."""
        for eid in entity_ids:
            try:
                self.set_state(eid, state="unavailable")
            except Exception as exc:
                self.log(f"Legacy cleanup failed for {eid}: {exc}", level="WARNING")
