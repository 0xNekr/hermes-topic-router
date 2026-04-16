"""Configuration loader for topic-router plugin."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "config.json"

# Search paths for config.json (first match wins)
_SEARCH_PATHS = [
    Path.home() / ".hermes" / "plugins" / "topic-router" / CONFIG_FILENAME,
    Path(__file__).parent / CONFIG_FILENAME,
]


@dataclass(frozen=True)
class Route:
    """A single topic → model routing rule."""

    platform: str
    chat_id: str
    thread_id: str
    model: str
    provider: str = ""
    label: str = ""


@dataclass
class RouterConfig:
    """Full plugin configuration."""

    routes: list[Route] = field(default_factory=list)
    default_model: str | None = None
    log_routing: bool = True

    # Internal: track file for hot-reload
    _config_path: Path | None = field(default=None, repr=False)
    _last_mtime: float = field(default=0.0, repr=False)


def _find_config_file() -> Path | None:
    """Locate config.json in known paths."""
    # Allow override via env var
    env_path = os.environ.get("TOPIC_ROUTER_CONFIG")
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p
        logger.warning("TOPIC_ROUTER_CONFIG=%s not found, searching defaults", env_path)

    for path in _SEARCH_PATHS:
        if path.is_file():
            return path

    return None


def _parse_route(raw: dict) -> Route | None:
    """Parse a single route entry, returning None on invalid data."""
    try:
        return Route(
            platform=str(raw["platform"]).lower(),
            chat_id=str(raw["chat_id"]),
            thread_id=str(raw["thread_id"]),
            model=str(raw["model"]),
            provider=str(raw.get("provider", "")),
            label=str(raw.get("label", "")),
        )
    except (KeyError, TypeError) as e:
        logger.warning("Skipping invalid route entry: %s (%s)", raw, e)
        return None


def load_config() -> RouterConfig:
    """Load and validate config from disk."""
    config_path = _find_config_file()

    if config_path is None:
        logger.warning(
            "topic-router: no config.json found. "
            "Copy config.example.json to ~/.hermes/plugins/topic-router/config.json"
        )
        return RouterConfig()

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.error("topic-router: failed to read %s: %s", config_path, e)
        return RouterConfig()

    routes = []
    for entry in raw.get("routes", []):
        route = _parse_route(entry)
        if route:
            routes.append(route)

    config = RouterConfig(
        routes=routes,
        default_model=raw.get("default_model"),
        log_routing=raw.get("log_routing", True),
    )
    config._config_path = config_path
    config._last_mtime = config_path.stat().st_mtime

    logger.info(
        "topic-router: loaded %d route(s) from %s",
        len(routes),
        config_path,
    )
    return config


def _get_or_create_config_path() -> Path:
    """Get the config file path, creating it if needed."""
    env_path = os.environ.get("TOPIC_ROUTER_CONFIG")
    if env_path:
        return Path(env_path)
    return _SEARCH_PATHS[0]


def save_config(config: RouterConfig) -> None:
    """Write the current config to disk."""
    config_path = config._config_path or _get_or_create_config_path()

    data = {
        "routes": [
            {
                "platform": r.platform,
                "chat_id": r.chat_id,
                "thread_id": r.thread_id,
                "model": r.model,
                "provider": r.provider,
                "label": r.label,
            }
            for r in config.routes
        ],
        "default_model": config.default_model,
        "log_routing": config.log_routing,
    }

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    config._config_path = config_path
    config._last_mtime = config_path.stat().st_mtime

    logger.info("topic-router: saved %d route(s) to %s", len(config.routes), config_path)


def add_route(config: RouterConfig, route: Route) -> RouterConfig:
    """Add or update a route. Returns a new config."""
    # Remove existing route for same platform/chat/thread
    filtered = [
        r for r in config.routes
        if not (r.platform == route.platform and r.chat_id == route.chat_id and r.thread_id == route.thread_id)
    ]
    filtered.append(route)

    new_config = RouterConfig(
        routes=filtered,
        default_model=config.default_model,
        log_routing=config.log_routing,
    )
    new_config._config_path = config._config_path
    new_config._last_mtime = config._last_mtime
    return new_config


def remove_route(config: RouterConfig, platform: str, chat_id: str, thread_id: str) -> RouterConfig:
    """Remove a route. Returns a new config."""
    filtered = [
        r for r in config.routes
        if not (r.platform == platform and r.chat_id == chat_id and r.thread_id == thread_id)
    ]

    new_config = RouterConfig(
        routes=filtered,
        default_model=config.default_model,
        log_routing=config.log_routing,
    )
    new_config._config_path = config._config_path
    new_config._last_mtime = config._last_mtime
    return new_config


def maybe_reload(config: RouterConfig) -> RouterConfig:
    """Reload config if the file has changed on disk."""
    if config._config_path is None:
        return config

    try:
        current_mtime = config._config_path.stat().st_mtime
    except OSError:
        return config

    if current_mtime > config._last_mtime:
        logger.info("topic-router: config changed, reloading")
        return load_config()

    return config
