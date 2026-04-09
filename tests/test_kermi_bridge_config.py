"""Schema tests for kermi_bridge config_loader."""

from __future__ import annotations

import pytest
import yaml

from kermi_bridge.config_loader import ConfigError, load_config


# ── Helpers ───────────────────────────────────────────────────────────────────

MINIMAL_CONFIG = {
    "kermi_bridge": {
        "host": "192.168.1.100",
        "password": "test1234",
    }
}


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "kermi_bridge.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


# ── TestMissingRequiredFields ─────────────────────────────────────────────────

class TestMissingRequiredFields:
    def test_missing_host(self, tmp_path):
        p = _write(tmp_path, {"kermi_bridge": {"password": "test"}})
        with pytest.raises(ConfigError, match="host"):
            load_config(p)

    def test_missing_password(self, tmp_path):
        p = _write(tmp_path, {"kermi_bridge": {"host": "192.168.1.100"}})
        with pytest.raises(ConfigError, match="password"):
            load_config(p)

    def test_missing_kermi_bridge_section(self, tmp_path):
        p = _write(tmp_path, {"other_section": {}})
        with pytest.raises(ConfigError):
            load_config(p)


# ── TestDefaults ──────────────────────────────────────────────────────────────

class TestDefaults:
    def test_poll_interval_default(self, tmp_path):
        cfg = load_config(_write(tmp_path, MINIMAL_CONFIG))
        assert cfg["kermi_bridge"]["poll_interval_s"] == 30

    def test_max_failures_default(self, tmp_path):
        cfg = load_config(_write(tmp_path, MINIMAL_CONFIG))
        assert cfg["kermi_bridge"]["max_failures"] == 5

    def test_timeout_s_default(self, tmp_path):
        cfg = load_config(_write(tmp_path, MINIMAL_CONFIG))
        assert cfg["kermi_bridge"]["timeout_s"] == 10

    def test_circuits_default(self, tmp_path):
        cfg = load_config(_write(tmp_path, MINIMAL_CONFIG))
        assert cfg["kermi_bridge"]["circuits"] == ["MK1", "MK2"]

    def test_device_id_absent_when_not_provided(self, tmp_path):
        cfg = load_config(_write(tmp_path, MINIMAL_CONFIG))
        assert cfg["kermi_bridge"].get("device_id") is None

    def test_explicit_device_id_preserved(self, tmp_path):
        data = {
            "kermi_bridge": {**MINIMAL_CONFIG["kermi_bridge"], "device_id": "test-uuid-1234"}
        }
        cfg = load_config(_write(tmp_path, data))
        assert cfg["kermi_bridge"]["device_id"] == "test-uuid-1234"


# ── TestRangeValidation ───────────────────────────────────────────────────────

class TestRangeValidation:
    def test_poll_interval_below_minimum_rejected(self, tmp_path):
        data = {"kermi_bridge": {**MINIMAL_CONFIG["kermi_bridge"], "poll_interval_s": 9}}
        p = _write(tmp_path, data)
        with pytest.raises(ConfigError, match="poll_interval_s"):
            load_config(p)

    def test_poll_interval_exactly_10_accepted(self, tmp_path):
        data = {"kermi_bridge": {**MINIMAL_CONFIG["kermi_bridge"], "poll_interval_s": 10}}
        cfg = load_config(_write(tmp_path, data))
        assert cfg["kermi_bridge"]["poll_interval_s"] == 10

    def test_poll_interval_large_value_accepted(self, tmp_path):
        data = {
            "kermi_bridge": {**MINIMAL_CONFIG["kermi_bridge"], "poll_interval_s": 300}
        }
        cfg = load_config(_write(tmp_path, data))
        assert cfg["kermi_bridge"]["poll_interval_s"] == 300

    def test_max_failures_zero_rejected(self, tmp_path):
        data = {"kermi_bridge": {**MINIMAL_CONFIG["kermi_bridge"], "max_failures": 0}}
        p = _write(tmp_path, data)
        with pytest.raises(ConfigError):
            load_config(p)

    def test_max_failures_one_accepted(self, tmp_path):
        data = {"kermi_bridge": {**MINIMAL_CONFIG["kermi_bridge"], "max_failures": 1}}
        cfg = load_config(_write(tmp_path, data))
        assert cfg["kermi_bridge"]["max_failures"] == 1

    def test_timeout_zero_rejected(self, tmp_path):
        data = {"kermi_bridge": {**MINIMAL_CONFIG["kermi_bridge"], "timeout_s": 0}}
        p = _write(tmp_path, data)
        with pytest.raises(ConfigError):
            load_config(p)


# ── TestCircuitValidation ─────────────────────────────────────────────────────

class TestCircuitValidation:
    def test_all_valid_circuits(self, tmp_path):
        data = {
            "kermi_bridge": {
                **MINIMAL_CONFIG["kermi_bridge"],
                "circuits": ["MK1", "MK2", "HK"],
            }
        }
        cfg = load_config(_write(tmp_path, data))
        assert cfg["kermi_bridge"]["circuits"] == ["MK1", "MK2", "HK"]

    def test_single_circuit(self, tmp_path):
        data = {
            "kermi_bridge": {**MINIMAL_CONFIG["kermi_bridge"], "circuits": ["HK"]}
        }
        cfg = load_config(_write(tmp_path, data))
        assert cfg["kermi_bridge"]["circuits"] == ["HK"]

    def test_invalid_circuit_name_rejected(self, tmp_path):
        data = {
            "kermi_bridge": {
                **MINIMAL_CONFIG["kermi_bridge"],
                "circuits": ["MK1", "MK3"],
            }
        }
        p = _write(tmp_path, data)
        with pytest.raises(ConfigError, match="MK3"):
            load_config(p)

    def test_circuits_not_a_list_rejected(self, tmp_path):
        data = {
            "kermi_bridge": {**MINIMAL_CONFIG["kermi_bridge"], "circuits": "MK1"}
        }
        p = _write(tmp_path, data)
        with pytest.raises(ConfigError):
            load_config(p)

    def test_empty_circuit_list_rejected(self, tmp_path):
        data = {
            "kermi_bridge": {**MINIMAL_CONFIG["kermi_bridge"], "circuits": []}
        }
        p = _write(tmp_path, data)
        with pytest.raises(ConfigError, match="empty"):
            load_config(p)


# ── TestFileErrors ────────────────────────────────────────────────────────────

class TestFileErrors:
    def test_missing_file(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nonexistent.yaml")

    def test_bad_yaml(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("key: [unclosed bracket", encoding="utf-8")
        with pytest.raises(ConfigError, match="parse"):
            load_config(p)

    def test_non_mapping_yaml(self, tmp_path):
        p = tmp_path / "list.yaml"
        p.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(p)

    def test_extra_keys_in_top_level_allowed(self, tmp_path):
        data = {**MINIMAL_CONFIG, "other_app": {"foo": "bar"}}
        cfg = load_config(_write(tmp_path, data))
        assert cfg["kermi_bridge"]["host"] == "192.168.1.100"
