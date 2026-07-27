import json
import logging
import threading
from collections import OrderedDict
from types import SimpleNamespace

import pytest

from web.backend.services import settings_service as service
from zero import config
from zero.core.wrapper import VolatilityWrapper


@pytest.fixture
def isolated_runtime_settings(tmp_path, monkeypatch):
    settings_file = tmp_path / ".zero" / "runtime_settings.json"
    monkeypatch.setattr(service, "_SETTINGS_FILE", settings_file)
    monkeypatch.setattr(service, "_apply_live_settings", lambda: None)

    for spec in service._SPECS:
        monkeypatch.setattr(
            config,
            spec.config_name,
            getattr(config, spec.config_name, spec.default),
            raising=False,
        )

    root = logging.getLogger()
    root_level = root.level
    handler_levels = [(handler, handler.level) for handler in root.handlers]
    yield settings_file
    root.setLevel(root_level)
    for handler, level in handler_levels:
        handler.setLevel(level)


def test_save_runtime_settings_persists_scaled_values(isolated_runtime_settings):
    values = service.save_runtime_settings(
        {
            "plugin_timeout_seconds": 1800,
            "plugin_stall_timeout_seconds": 0,
            "worker_heartbeat_seconds": 10,
            "auto_symbol_scan_max_mb": 512,
        }
    )

    assert values["plugin_timeout_seconds"] == 1800
    assert config.PLUGIN_TIMEOUT_SECONDS == 1800
    assert config.PLUGIN_STALL_TIMEOUT_SECONDS == 0
    assert config.AUTO_SYMBOL_SCAN_MAX_BYTES == 512 * 1024 * 1024

    payload = json.loads(isolated_runtime_settings.read_text(encoding="utf-8"))
    assert payload["settings"]["auto_symbol_scan_max_mb"] == 512


def test_runtime_settings_reject_invalid_relationships(isolated_runtime_settings):
    with pytest.raises(ValueError, match="心跳"):
        service.save_runtime_settings(
            {
                "plugin_stall_timeout_seconds": 10,
                "worker_heartbeat_seconds": 10,
            }
        )

    with pytest.raises(ValueError, match="陈旧索引"):
        service.save_runtime_settings(
            {
                "symbol_index_ttl_minutes": 1440,
                "symbol_index_stale_hours": 1,
            }
        )


def test_load_runtime_settings_applies_persisted_overlay(isolated_runtime_settings):
    isolated_runtime_settings.parent.mkdir(parents=True)
    isolated_runtime_settings.write_text(
        json.dumps(
            {
                "version": 1,
                "settings": {
                    "plugin_timeout_seconds": 2400,
                    "results_memory_cache_max": 12,
                },
            }
        ),
        encoding="utf-8",
    )

    values = service.load_runtime_settings()

    assert values["plugin_timeout_seconds"] == 2400
    assert config.PLUGIN_TIMEOUT_SECONDS == 2400
    assert config.RESULTS_MEMORY_CACHE_MAX == 12


def test_wrapper_applies_updated_runtime_controls(monkeypatch):
    wrapper = VolatilityWrapper.__new__(VolatilityWrapper)
    wrapper.worker_start_method = "fork"
    wrapper._disk_cache = SimpleNamespace(enabled=True)
    wrapper._cache = OrderedDict((str(i), object()) for i in range(4))
    wrapper._state_lock = threading.Lock()

    monkeypatch.setattr(config, "PLUGIN_TIMEOUT_SECONDS", 1800)
    monkeypatch.setattr(config, "PLUGIN_STALL_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(config, "PROGRESS_LOG_THROTTLE_SECONDS", 1.5)
    monkeypatch.setattr(config, "TERMINATE_GRACE_SECONDS", 3)
    monkeypatch.setattr(config, "WORKER_START_METHOD", "spawn")
    monkeypatch.setattr(config, "ENABLE_DISK_CACHE", False)
    monkeypatch.setattr(config, "RESULTS_MEMORY_CACHE_MAX", 2)

    wrapper.apply_runtime_settings()

    assert wrapper.plugin_timeout_seconds == 1800
    assert wrapper.stall_timeout_seconds == 0
    assert wrapper.worker_start_method == "spawn"
    assert wrapper._disk_cache.enabled is False
    assert list(wrapper._cache) == ["2", "3"]
