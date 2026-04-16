"""hermes-topic-router - Auto-route LLM models per chat topic/thread.

Hooks into Hermes Agent's pre_api_request to swap the model based on
which Telegram topic (or Discord channel) the message originated from.
"""

from __future__ import annotations

import logging
import os

from config import RouterConfig, load_config, maybe_reload
from router import format_route_table, get_model_for_topic

__version__ = "0.1.0"

logger = logging.getLogger(__name__)

# Module-level state - loaded once, hot-reloaded on config file change
_config: RouterConfig = RouterConfig()


def _on_session_start(session_id: str, model: str, platform: str, **kwargs) -> None:
    """Log which model will be used for this topic (informational)."""
    global _config
    _config = maybe_reload(_config)

    thread_id = os.environ.get("HERMES_SESSION_THREAD_ID", "")
    chat_id = os.environ.get("HERMES_SESSION_CHAT_ID", "")

    if not thread_id:
        return

    routed_model = get_model_for_topic(_config, platform, chat_id, thread_id)

    if routed_model and _config.log_routing:
        logger.info(
            "topic-router: session %s - topic %s:%s -> model %s",
            session_id,
            chat_id,
            thread_id,
            routed_model,
        )


def _on_pre_api_request(method: str, url: str, headers: dict, body: dict, **kwargs) -> None:
    """Intercept API request and swap model based on active topic.

    This mutates the body dict in-place. Since Python dicts are passed
    by reference, changes here affect the actual HTTP request - as long
    as Hermes doesn't deep-copy the body before calling this hook.
    """
    global _config
    _config = maybe_reload(_config)

    platform = os.environ.get("HERMES_SESSION_PLATFORM", "")
    chat_id = os.environ.get("HERMES_SESSION_CHAT_ID", "")
    thread_id = os.environ.get("HERMES_SESSION_THREAD_ID", "")

    if not thread_id or not platform:
        return

    target_model = get_model_for_topic(_config, platform, chat_id, thread_id)

    if target_model is None:
        return

    current_model = body.get("model", "")
    if current_model == target_model:
        return

    if _config.log_routing:
        logger.info(
            "topic-router: routing %s:%s - %s -> %s",
            chat_id,
            thread_id,
            current_model,
            target_model,
        )

    body["model"] = target_model


def _handle_routes(raw_args: str) -> str:
    """Handler for /routes slash command - display active routing table."""
    global _config
    _config = maybe_reload(_config)
    return format_route_table(_config)


def register(ctx) -> None:
    """Wire hooks and commands into Hermes."""
    global _config
    _config = load_config()

    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("pre_api_request", _on_pre_api_request)
    ctx.register_command("routes", handler=_handle_routes, description="Show topic-router routing table")

    logger.info("topic-router: registered (%d routes)", len(_config.routes))
