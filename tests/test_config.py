"""Tests for configuration loading."""

import json
import tempfile
from pathlib import Path

from config import (
    Route,
    RouterConfig,
    _parse_route,
    add_route,
    load_config,
    maybe_reload,
    remove_route,
    save_config,
)


class TestParseRoute:
    """Test individual route parsing."""

    def test_valid_route(self):
        route = _parse_route({
            "platform": "telegram",
            "chat_id": "-100123",
            "thread_id": "5",
            "model": "kimi-k2.5",
            "label": "Kimi",
        })
        assert route is not None
        assert route.platform == "telegram"
        assert route.model == "kimi-k2.5"
        assert route.label == "Kimi"

    def test_missing_required_field(self):
        route = _parse_route({
            "platform": "telegram",
            "chat_id": "-100123",
            # missing thread_id and model
        })
        assert route is None

    def test_label_optional(self):
        route = _parse_route({
            "platform": "telegram",
            "chat_id": "-100123",
            "thread_id": "5",
            "model": "kimi-k2.5",
        })
        assert route is not None
        assert route.label == ""

    def test_values_cast_to_string(self):
        route = _parse_route({
            "platform": "Telegram",
            "chat_id": -100123,
            "thread_id": 5,
            "model": "kimi-k2.5",
        })
        assert route is not None
        assert route.platform == "telegram"
        assert route.chat_id == "-100123"
        assert route.thread_id == "5"


class TestLoadConfig:
    """Test config file loading."""

    def test_load_from_env_var(self, monkeypatch, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "routes": [
                {
                    "platform": "telegram",
                    "chat_id": "-100123",
                    "thread_id": "5",
                    "model": "kimi-k2.5",
                }
            ],
            "default_model": "fallback",
            "log_routing": False,
        }))
        monkeypatch.setenv("TOPIC_ROUTER_CONFIG", str(config_file))

        config = load_config()
        assert len(config.routes) == 1
        assert config.routes[0].model == "kimi-k2.5"
        assert config.default_model == "fallback"
        assert config.log_routing is False

    def test_missing_config_returns_empty(self, monkeypatch):
        monkeypatch.setenv("TOPIC_ROUTER_CONFIG", "/nonexistent/config.json")
        # Also patch search paths to avoid picking up real config
        monkeypatch.setattr(
            "config._SEARCH_PATHS", []
        )
        config = load_config()
        assert len(config.routes) == 0
        assert config.default_model is None

    def test_invalid_json(self, monkeypatch, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text("not valid json {{{")
        monkeypatch.setenv("TOPIC_ROUTER_CONFIG", str(config_file))

        config = load_config()
        assert len(config.routes) == 0

    def test_skips_invalid_routes(self, monkeypatch, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "routes": [
                {"platform": "telegram", "chat_id": "-100123", "thread_id": "5", "model": "good"},
                {"platform": "telegram"},  # invalid, missing fields
                {"platform": "telegram", "chat_id": "-100123", "thread_id": "7", "model": "also-good"},
            ]
        }))
        monkeypatch.setenv("TOPIC_ROUTER_CONFIG", str(config_file))

        config = load_config()
        assert len(config.routes) == 2


class TestMaybeReload:
    """Test hot-reload on file change."""

    def test_no_reload_when_unchanged(self, monkeypatch, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "routes": [
                {"platform": "telegram", "chat_id": "-100123", "thread_id": "5", "model": "v1"},
            ]
        }))
        monkeypatch.setenv("TOPIC_ROUTER_CONFIG", str(config_file))

        config = load_config()
        assert config.routes[0].model == "v1"

        # Same mtime, should not reload
        reloaded = maybe_reload(config)
        assert reloaded.routes[0].model == "v1"

    def test_reloads_on_mtime_change(self, monkeypatch, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "routes": [
                {"platform": "telegram", "chat_id": "-100123", "thread_id": "5", "model": "v1"},
            ]
        }))
        monkeypatch.setenv("TOPIC_ROUTER_CONFIG", str(config_file))

        config = load_config()
        assert config.routes[0].model == "v1"

        # Force mtime to be older so next write triggers reload
        config._last_mtime = 0.0

        config_file.write_text(json.dumps({
            "routes": [
                {"platform": "telegram", "chat_id": "-100123", "thread_id": "5", "model": "v2"},
            ]
        }))

        reloaded = maybe_reload(config)
        assert reloaded.routes[0].model == "v2"

    def test_no_config_path_noop(self):
        config = RouterConfig()
        assert maybe_reload(config) is config


class TestAddRoute:
    """Test adding/updating routes."""

    def test_add_new_route(self):
        config = RouterConfig()
        route = Route(platform="telegram", chat_id="-100123", thread_id="5", model="kimi-k2.5")
        updated = add_route(config, route)
        assert len(updated.routes) == 1
        assert updated.routes[0].model == "kimi-k2.5"

    def test_update_existing_route(self):
        config = RouterConfig(routes=[
            Route(platform="telegram", chat_id="-100123", thread_id="5", model="old-model"),
        ])
        route = Route(platform="telegram", chat_id="-100123", thread_id="5", model="new-model")
        updated = add_route(config, route)
        assert len(updated.routes) == 1
        assert updated.routes[0].model == "new-model"

    def test_add_preserves_other_routes(self):
        config = RouterConfig(routes=[
            Route(platform="telegram", chat_id="-100123", thread_id="5", model="kimi"),
        ])
        route = Route(platform="telegram", chat_id="-100123", thread_id="12", model="qwen")
        updated = add_route(config, route)
        assert len(updated.routes) == 2

    def test_add_preserves_config_settings(self):
        config = RouterConfig(default_model="fallback", log_routing=False)
        route = Route(platform="telegram", chat_id="-100123", thread_id="5", model="kimi")
        updated = add_route(config, route)
        assert updated.default_model == "fallback"
        assert updated.log_routing is False


class TestRemoveRoute:
    """Test removing routes."""

    def test_remove_existing(self):
        config = RouterConfig(routes=[
            Route(platform="telegram", chat_id="-100123", thread_id="5", model="kimi"),
            Route(platform="telegram", chat_id="-100123", thread_id="12", model="qwen"),
        ])
        updated = remove_route(config, "telegram", "-100123", "5")
        assert len(updated.routes) == 1
        assert updated.routes[0].thread_id == "12"

    def test_remove_nonexistent(self):
        config = RouterConfig(routes=[
            Route(platform="telegram", chat_id="-100123", thread_id="5", model="kimi"),
        ])
        updated = remove_route(config, "telegram", "-100123", "99")
        assert len(updated.routes) == 1


class TestSaveConfig:
    """Test saving config to disk."""

    def test_save_and_reload(self, monkeypatch, tmp_path):
        config_file = tmp_path / "config.json"
        monkeypatch.setenv("TOPIC_ROUTER_CONFIG", str(config_file))

        config = RouterConfig(
            routes=[
                Route(platform="telegram", chat_id="-100123", thread_id="5", model="kimi-k2.5", label="Kimi"),
            ],
            default_model="fallback",
            log_routing=True,
        )
        config._config_path = config_file
        save_config(config)

        assert config_file.exists()
        reloaded = load_config()
        assert len(reloaded.routes) == 1
        assert reloaded.routes[0].model == "kimi-k2.5"
        assert reloaded.default_model == "fallback"

    def test_save_creates_parent_dirs(self, tmp_path):
        config_file = tmp_path / "deep" / "nested" / "config.json"
        config = RouterConfig(routes=[])
        config._config_path = config_file
        save_config(config)
        assert config_file.exists()
