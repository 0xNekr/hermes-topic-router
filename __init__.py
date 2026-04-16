"""hermes-topic-router - Auto-route LLM models per chat topic/thread.

Hooks into Hermes Agent's pre_llm_call to swap the model based on
which Telegram topic (or Discord channel) the message originated from.
"""

from __future__ import annotations

import json
import logging
import os
import sys

try:
    from .config import Route, RouterConfig, add_route, load_config, maybe_reload, remove_route, save_config
    from .router import format_route_table, get_model_for_topic
except ImportError:
    from config import Route, RouterConfig, add_route, load_config, maybe_reload, remove_route, save_config
    from router import format_route_table, get_model_for_topic

__version__ = "0.2.0"

logger = logging.getLogger(__name__)

# Module-level state
_config: RouterConfig = RouterConfig()
_session_ctx: dict = {"platform": "", "chat_id": "", "thread_id": ""}

_KNOWN_PLATFORMS = {"telegram", "discord", "slack", "whatsapp", "signal"}


def _read_session_context(platform_hint: str = "") -> tuple[str, str, str]:
    """Read session context from HERMES_SESSION_KEY and cache it for tools.

    HERMES_SESSION_KEY format: "agent:main:telegram:group:-1003957589238:325"
    """
    session_key = os.environ.get("HERMES_SESSION_KEY", "")

    if session_key:
        parts = session_key.split(":")
        platform_idx = None
        for i, part in enumerate(parts):
            if part in _KNOWN_PLATFORMS:
                platform_idx = i
                break

        if platform_idx is not None and len(parts) > platform_idx + 2:
            _session_ctx["platform"] = parts[platform_idx]
            _session_ctx["thread_id"] = parts[-1]
            _session_ctx["chat_id"] = parts[-2]

    if platform_hint and not _session_ctx["platform"]:
        _session_ctx["platform"] = platform_hint

    return (
        _session_ctx["platform"],
        _session_ctx["chat_id"],
        _session_ctx["thread_id"],
    )


def _find_agent_from_stack():
    """Walk up the call stack to find the AIAgent instance."""
    frame = sys._getframe(2)
    while frame:
        self_obj = frame.f_locals.get("self")
        if self_obj and hasattr(self_obj, "switch_model"):
            return self_obj
        frame = frame.f_back
    return None


def _on_pre_llm_call(session_id: str, user_message: str, conversation_history: list,
                     is_first_turn: bool, model: str, platform: str, **kwargs):
    """Switch model for routed topics and inject model identity context."""
    global _config
    _config = maybe_reload(_config)
    _read_session_context(platform_hint=platform)

    platform_ctx = _session_ctx["platform"]
    chat_id = _session_ctx["chat_id"]
    thread_id = _session_ctx["thread_id"]

    target_model = get_model_for_topic(_config, platform_ctx, chat_id, thread_id) if thread_id else None

    if not target_model or target_model == model:
        return

    route = next(
        (r for r in _config.routes
         if r.platform == platform_ctx and r.chat_id == chat_id and r.thread_id == thread_id),
        None,
    )
    target_provider = route.provider if route else ""

    agent = _find_agent_from_stack()
    if not agent:
        return

    try:
        current_provider = getattr(agent, "provider", "") or ""
        same_provider = (not target_provider) or (target_provider == current_provider)

        if same_provider:
            agent.model = target_model
            logger.info("topic-router: %s -> %s (same provider: %s)", model, target_model, current_provider)
        else:
            from hermes_cli.model_switch import switch_model as resolve_switch
            result = resolve_switch(
                raw_input=target_model,
                current_provider=current_provider,
                current_model=model,
                current_base_url=getattr(agent, "base_url", "") or "",
                current_api_key=getattr(agent, "api_key", "") or "",
                explicit_provider=target_provider,
            )
            agent.switch_model(
                new_model=result.new_model,
                new_provider=result.target_provider,
                api_key=result.api_key,
                base_url=result.base_url,
                api_mode=getattr(result, "api_mode", ""),
            )
            logger.info("topic-router: %s -> %s (provider: %s -> %s)", model, result.new_model, current_provider, result.target_provider)
    except Exception as exc:
        logger.warning("topic-router: switch failed: %s", exc)
        return

    provider_info = f" via {target_provider}" if target_provider else ""
    return {"context": f"[System: You are currently running as {target_model}{provider_info}. If asked about your model, answer {target_model}.]"}


def _on_session_start(session_id: str, model: str, platform: str, **kwargs) -> None:
    """Cache session context on new sessions."""
    global _config
    _config = maybe_reload(_config)
    _read_session_context(platform_hint=platform)


SCHEMA_ROUTE_SET = {
    "type": "object",
    "description": (
        "Assign a specific LLM model to the current chat topic/thread. "
        "You MUST extract the model name from the user's message and pass it as the 'model' parameter. "
        "Example: if user says 'route this topic to kimi-k2.5', call this tool with model='kimi-k2.5'. "
        "The topic context (platform, chat_id, thread_id) is auto-detected."
    ),
    "properties": {
        "model": {
            "type": "string",
            "description": (
                "The exact model name to assign. Extract this from the user's message. "
                "Examples: 'kimi-k2.5', 'qwen3.6-plus', 'glm-5.1', 'mimo-v2-pro'. "
                "This parameter is REQUIRED, do not call this tool without it."
            ),
        },
    },
    "required": ["model"],
}

SCHEMA_ROUTE_REMOVE = {
    "type": "object",
    "description": "Remove the model route for the current chat topic/thread, reverting to the default model.",
    "properties": {},
}

SCHEMA_ROUTE_LIST = {
    "type": "object",
    "description": "List all configured topic-to-model routes and their current status.",
    "properties": {},
}


def _tool_route_set(args: dict, **kwargs) -> str:
    """Set model route for the current topic."""
    global _config
    _config = maybe_reload(_config)

    platform, chat_id, thread_id = _read_session_context()
    model_name = args.get("model", "")

    if not platform or not thread_id:
        return json.dumps({"error": "No topic detected. This only works inside a threaded chat."})

    if not model_name:
        return json.dumps({"error": "Model name is required."})

    route = Route(
        platform=platform.lower(),
        chat_id=chat_id,
        thread_id=thread_id,
        model=model_name,
        label="",
    )
    _config = add_route(_config, route)
    save_config(_config)

    return json.dumps({
        "success": True,
        "message": f"Route saved: this topic -> {model_name}",
        "platform": platform,
        "chat_id": chat_id,
        "thread_id": thread_id,
        "model": model_name,
    })


def _tool_route_remove(args: dict, **kwargs) -> str:
    """Remove model route for the current topic."""
    global _config
    _config = maybe_reload(_config)

    platform, chat_id, thread_id = _read_session_context()

    if not platform or not thread_id:
        return json.dumps({"error": "No topic detected. This only works inside a threaded chat."})

    current = get_model_for_topic(_config, platform, chat_id, thread_id)
    if current is None:
        return json.dumps({"error": "No route configured for this topic."})

    _config = remove_route(_config, platform.lower(), chat_id, thread_id)
    save_config(_config)

    return json.dumps({
        "success": True,
        "message": f"Route removed (was {current}).",
    })


def _tool_route_list(args: dict, **kwargs) -> str:
    """Show all configured routes."""
    global _config
    _config = maybe_reload(_config)
    return json.dumps({
        "routes": [
            {"platform": r.platform, "chat_id": r.chat_id, "thread_id": r.thread_id, "model": r.model, "provider": r.provider, "label": r.label}
            for r in _config.routes
        ],
        "default_model": _config.default_model,
    })


def register(ctx) -> None:
    """Wire hooks and tools into Hermes."""
    global _config
    _config = load_config()

    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)

    ctx.register_tool(name="topic_route_set", toolset="topic-router", schema=SCHEMA_ROUTE_SET, handler=_tool_route_set)
    ctx.register_tool(name="topic_route_remove", toolset="topic-router", schema=SCHEMA_ROUTE_REMOVE, handler=_tool_route_remove)
    ctx.register_tool(name="topic_route_list", toolset="topic-router", schema=SCHEMA_ROUTE_LIST, handler=_tool_route_list)

    logger.info("topic-router: registered (%d routes)", len(_config.routes))
