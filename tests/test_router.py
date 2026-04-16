"""Tests for the routing logic."""

from config import Route, RouterConfig
from router import format_route_table, get_model_for_topic


def _make_config(routes: list[Route], default_model: str | None = None) -> RouterConfig:
    return RouterConfig(routes=routes, default_model=default_model)


class TestGetModelForTopic:
    """Test the core routing lookup."""

    def test_exact_match(self):
        config = _make_config([
            Route(platform="telegram", chat_id="-100123", thread_id="5", model="kimi-k2.5"),
        ])
        assert get_model_for_topic(config, "telegram", "-100123", "5") == "kimi-k2.5"

    def test_no_match_returns_none(self):
        config = _make_config([
            Route(platform="telegram", chat_id="-100123", thread_id="5", model="kimi-k2.5"),
        ])
        assert get_model_for_topic(config, "telegram", "-100123", "99") is None

    def test_no_match_returns_default(self):
        config = _make_config(
            routes=[
                Route(platform="telegram", chat_id="-100123", thread_id="5", model="kimi-k2.5"),
            ],
            default_model="fallback-model",
        )
        assert get_model_for_topic(config, "telegram", "-100123", "99") == "fallback-model"

    def test_chat_wildcard(self):
        config = _make_config([
            Route(platform="telegram", chat_id="-100123", thread_id="*", model="catch-all"),
        ])
        assert get_model_for_topic(config, "telegram", "-100123", "42") == "catch-all"

    def test_platform_wildcard(self):
        config = _make_config([
            Route(platform="telegram", chat_id="*", thread_id="*", model="global-tg"),
        ])
        assert get_model_for_topic(config, "telegram", "-100999", "1") == "global-tg"

    def test_exact_match_beats_wildcard(self):
        config = _make_config([
            Route(platform="telegram", chat_id="-100123", thread_id="*", model="catch-all"),
            Route(platform="telegram", chat_id="-100123", thread_id="5", model="specific"),
        ])
        assert get_model_for_topic(config, "telegram", "-100123", "5") == "specific"

    def test_chat_wildcard_beats_platform_wildcard(self):
        config = _make_config([
            Route(platform="telegram", chat_id="*", thread_id="*", model="global"),
            Route(platform="telegram", chat_id="-100123", thread_id="*", model="chat-level"),
        ])
        assert get_model_for_topic(config, "telegram", "-100123", "5") == "chat-level"

    def test_platform_case_insensitive(self):
        config = _make_config([
            Route(platform="telegram", chat_id="-100123", thread_id="5", model="kimi-k2.5"),
        ])
        assert get_model_for_topic(config, "Telegram", "-100123", "5") == "kimi-k2.5"

    def test_empty_thread_id_returns_default(self):
        config = _make_config(
            routes=[
                Route(platform="telegram", chat_id="-100123", thread_id="5", model="kimi-k2.5"),
            ],
            default_model="fallback",
        )
        assert get_model_for_topic(config, "telegram", "-100123", "") == "fallback"

    def test_empty_platform_returns_default(self):
        config = _make_config(
            routes=[
                Route(platform="telegram", chat_id="-100123", thread_id="5", model="kimi-k2.5"),
            ],
            default_model="fallback",
        )
        assert get_model_for_topic(config, "", "-100123", "5") == "fallback"

    def test_discord_platform(self):
        config = _make_config([
            Route(platform="discord", chat_id="guild-123", thread_id="chan-456", model="gpt-4o"),
        ])
        assert get_model_for_topic(config, "discord", "guild-123", "chan-456") == "gpt-4o"

    def test_multiple_platforms(self):
        config = _make_config([
            Route(platform="telegram", chat_id="-100123", thread_id="5", model="kimi-k2.5"),
            Route(platform="discord", chat_id="guild-1", thread_id="chan-1", model="qwen3.6-plus"),
        ])
        assert get_model_for_topic(config, "telegram", "-100123", "5") == "kimi-k2.5"
        assert get_model_for_topic(config, "discord", "guild-1", "chan-1") == "qwen3.6-plus"

    def test_empty_routes(self):
        config = _make_config([])
        assert get_model_for_topic(config, "telegram", "-100123", "5") is None

    def test_empty_routes_with_default(self):
        config = _make_config([], default_model="fallback")
        assert get_model_for_topic(config, "telegram", "-100123", "5") == "fallback"


class TestFormatRouteTable:
    """Test the route table formatting."""

    def test_empty_routes(self):
        config = _make_config([])
        result = format_route_table(config)
        assert "No routes configured" in result

    def test_formatted_output(self):
        config = _make_config([
            Route(platform="telegram", chat_id="-100123", thread_id="5", model="kimi-k2.5", label="Kimi"),
        ])
        result = format_route_table(config)
        assert "kimi-k2.5" in result
        assert "Kimi" in result
        assert "telegram" in result

    def test_default_model_shown(self):
        config = _make_config(
            routes=[
                Route(platform="telegram", chat_id="-100123", thread_id="5", model="kimi-k2.5"),
            ],
            default_model="fallback",
        )
        result = format_route_table(config)
        assert "fallback" in result
