"""hermes-topic-router - Auto-route LLM models per chat topic/thread.

Hooks into Hermes Agent's pre_llm_call to swap the model based on
which Telegram topic (or Discord channel) the message originated from.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.request

try:
    from .config import Route, RouterConfig, add_route, load_config, maybe_reload, remove_route, save_config
    from .router import format_route_table, get_model_for_topic
except ImportError:
    from config import Route, RouterConfig, add_route, load_config, maybe_reload, remove_route, save_config
    from router import format_route_table, get_model_for_topic

__version__ = "0.3.0"

logger = logging.getLogger(__name__)

# Module-level state
_config: RouterConfig = RouterConfig()
_session_ctx: dict = {"platform": "", "chat_id": "", "thread_id": ""}

_KNOWN_PLATFORMS = {"telegram", "discord", "slack", "whatsapp", "signal"}

# Persist pending selections to survive gateway restarts
_PENDING_FILE = os.path.join(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")), "plugins", "topic-router", ".pending.json")


def _load_pending() -> dict[str, dict]:
    try:
        with open(_PENDING_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_pending(pending: dict[str, dict]) -> None:
    try:
        os.makedirs(os.path.dirname(_PENDING_FILE), exist_ok=True)
        with open(_PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump(pending, f)
    except OSError:
        pass


def _clear_pending(key: str) -> None:
    pending = _load_pending()
    pending.pop(key, None)
    _save_pending(pending)


def _set_pending(key: str, state: dict) -> None:
    pending = _load_pending()
    pending[key] = state
    _save_pending(pending)


def _read_session_context(platform_hint: str = "") -> tuple[str, str, str]:
    """Read session context from HERMES_SESSION_KEY and cache it for tools."""
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

    return _session_ctx["platform"], _session_ctx["chat_id"], _session_ctx["thread_id"]


def _find_agent_from_stack():
    """Walk up the call stack to find the AIAgent instance."""
    frame = sys._getframe(2)
    while frame:
        self_obj = frame.f_locals.get("self")
        if self_obj and hasattr(self_obj, "switch_model"):
            return self_obj
        frame = frame.f_back
    return None


def _get_providers_and_models() -> dict[str, dict]:
    """Fetch available providers and their models from Hermes internals + config."""
    result = {}
    try:
        from hermes_cli.models import _PROVIDER_MODELS, _PROVIDER_LABELS
        for slug, models in _PROVIDER_MODELS.items():
            if not models or slug in ("custom", "copilot-acp"):
                continue
            label = _PROVIDER_LABELS.get(slug, slug)
            result[slug] = {"label": label, "models": list(models)}
    except Exception:
        pass

    # Merge available_models from config (supplements incomplete Hermes lists)
    if _config.available_models:
        provider = _config.default_provider or "custom"
        if provider not in result:
            result[provider] = {"label": provider, "models": []}
        existing = set(m.lower() for m in result[provider]["models"])
        for m in _config.available_models:
            if m.lower() not in existing:
                result[provider]["models"].append(m)

    return result


def _send_telegram_keyboard(chat_id: str, thread_id: str, buttons: list[list[str]], text: str) -> bool:
    """Send a reply keyboard via Telegram Bot API."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not bot_token or not buttons:
        return False

    keyboard = [[{"text": b} for b in row] for row in buttons]
    keyboard.append([{"text": "cancel"}])

    payload = json.dumps({
        "chat_id": int(chat_id),
        "message_thread_id": int(thread_id),
        "text": text,
        "reply_markup": {
            "keyboard": keyboard,
            "one_time_keyboard": True,
            "selective": True,
            "resize_keyboard": True,
        },
    })

    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        req = urllib.request.Request(url, data=payload.encode(), headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception as exc:
        logger.warning("topic-router: telegram keyboard failed: %s", exc)
        return False


def _remove_telegram_keyboard(chat_id: str, thread_id: str, text: str) -> bool:
    """Remove the reply keyboard."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not bot_token:
        return False

    payload = json.dumps({
        "chat_id": int(chat_id),
        "message_thread_id": int(thread_id),
        "text": text,
        "reply_markup": {"remove_keyboard": True, "selective": True},
    })

    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        req = urllib.request.Request(url, data=payload.encode(), headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception:
        return False


def _strip_username_prefix(msg: str) -> str:
    """Strip [Username] prefix added by Hermes gateway."""
    msg = msg.strip()
    if msg.startswith("[") and "] " in msg:
        msg = msg.split("] ", 1)[1]
    return msg.strip().lower()


def _on_pre_llm_call(session_id: str, user_message: str, conversation_history: list,
                     is_first_turn: bool, model: str, platform: str, **kwargs):
    """Switch model for routed topics OR handle keyboard selection flow."""
    global _config
    _config = maybe_reload(_config)
    _read_session_context(platform_hint=platform)

    platform_ctx = _session_ctx["platform"]
    chat_id = _session_ctx["chat_id"]
    thread_id = _session_ctx["thread_id"]

    # -- Handle pending keyboard selection --
    pending_key = f"{chat_id}:{thread_id}"
    pending = _load_pending().get(pending_key)

    if pending:
        msg = _strip_username_prefix(user_message or "")

        if msg == "cancel":
            _clear_pending(pending_key)
            _remove_telegram_keyboard(chat_id, thread_id, "Selection cancelled.")
            return {"context": "[System: Model selection cancelled.]"}

        if pending["step"] == "provider":
            # User picked a provider -> show models
            providers = _get_providers_and_models()
            # Strip trailing "(N)" count from button text
            import re
            clean_msg = re.sub(r"\s*\(\d+\)\s*$", "", msg).strip()
            # Match by label or slug
            matched_slug = None
            for slug, info in providers.items():
                if clean_msg == slug.lower() or clean_msg == info["label"].lower():
                    matched_slug = slug
                    break

            if matched_slug:
                models = providers[matched_slug]["models"]
                # Build model keyboard: 2 per row, strip provider prefix
                display_models = [m.split("/")[-1] if "/" in m else m for m in models]
                rows = [display_models[i:i + 2] for i in range(0, len(display_models), 2)]
                _set_pending(pending_key, {"step": "model", "provider": matched_slug, "models": dict(zip(display_models, models))})
                _send_telegram_keyboard(chat_id, thread_id, rows, f"Select a model ({providers[matched_slug]['label']}):")
                return {"context": "[System: User is picking a model from the keyboard. Wait for their selection.]"}

        elif pending["step"] == "model":
            # User picked a model
            provider = pending["provider"]
            models_map = pending.get("models", {})
            # Match exact or by display name
            full_model = models_map.get(msg) or msg

            if msg in models_map or msg in [m.lower() for m in models_map]:
                # Find the correct key
                for display, full in models_map.items():
                    if display.lower() == msg:
                        full_model = full
                        break

                _clear_pending(pending_key)

                # Save route with display name (without provider/ prefix)
                model_name = full_model.split("/")[-1] if "/" in full_model else full_model
                route = Route(
                    platform=platform_ctx.lower(),
                    chat_id=chat_id,
                    thread_id=thread_id,
                    model=model_name,
                    provider=provider,
                    label="",
                )
                _config = add_route(_config, route)
                save_config(_config)

                _remove_telegram_keyboard(chat_id, thread_id, f"Routed to {model_name} ({provider}).")
                logger.info("topic-router: route set via keyboard: %s:%s -> %s (%s)", chat_id, thread_id, model_name, provider)
                return {"context": f"[System: Route saved: {model_name} via {provider}. Confirm to the user.]"}

    # -- Normal routing: swap model if topic has a route --
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
        from hermes_cli.model_switch import switch_model as resolve_switch
        result = resolve_switch(
            raw_input=target_model,
            current_provider=current_provider,
            current_model=model,
            current_base_url=getattr(agent, "base_url", "") or "",
            current_api_key=getattr(agent, "api_key", "") or "",
            explicit_provider=target_provider or current_provider,
        )
        agent.switch_model(
            new_model=result.new_model,
            new_provider=result.target_provider,
            api_key=result.api_key,
            base_url=result.base_url,
            api_mode=getattr(result, "api_mode", ""),
        )
        logger.info("topic-router: %s -> %s (provider: %s)", model, result.new_model, result.target_provider)
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


# -- Tools --

SCHEMA_ROUTE_SELECT = {
    "type": "object",
    "description": (
        "Open the model selector for the current topic. Shows a two-step keyboard: "
        "first pick a provider, then pick a model. No parameters needed. "
        "Call this when the user asks to route/assign/set/change a model for this topic."
    ),
    "properties": {},
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


def _tool_route_select(args: dict, **kwargs) -> str:
    """Show provider selector keyboard."""
    global _config
    _config = maybe_reload(_config)

    platform, chat_id, thread_id = _read_session_context()

    if not platform or not thread_id:
        return json.dumps({"error": "No topic detected. Only works inside a threaded chat."})

    if platform != "telegram":
        return json.dumps({"error": f"Keyboard selector only works on Telegram (current: {platform})."})

    providers = _get_providers_and_models()
    if not providers:
        return json.dumps({"error": "Could not load provider list from Hermes."})

    # Build provider keyboard: 2 per row, show label + model count
    rows = []
    row = []
    for slug, info in providers.items():
        label = f"{info['label']} ({len(info['models'])})"
        row.append(label)
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    pending_key = f"{chat_id}:{thread_id}"
    _set_pending(pending_key, {"step": "provider"})

    sent = _send_telegram_keyboard(chat_id, thread_id, rows, "Select a provider:")
    if not sent:
        _clear_pending(pending_key)
        return json.dumps({"error": "Failed to send keyboard. Check TELEGRAM_BOT_TOKEN."})

    return json.dumps({"success": True, "message": "Provider selector sent. Waiting for user to pick."})


def _tool_route_remove(args: dict, **kwargs) -> str:
    """Remove model route for the current topic."""
    global _config
    _config = maybe_reload(_config)

    platform, chat_id, thread_id = _read_session_context()

    if not platform or not thread_id:
        return json.dumps({"error": "No topic detected."})

    current = get_model_for_topic(_config, platform, chat_id, thread_id)
    if current is None:
        return json.dumps({"error": "No route configured for this topic."})

    _config = remove_route(_config, platform.lower(), chat_id, thread_id)
    save_config(_config)

    return json.dumps({"success": True, "message": f"Route removed (was {current})."})


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

    ctx.register_tool(name="topic_route_select", toolset="topic-router", schema=SCHEMA_ROUTE_SELECT, handler=_tool_route_select)
    ctx.register_tool(name="topic_route_remove", toolset="topic-router", schema=SCHEMA_ROUTE_REMOVE, handler=_tool_route_remove)
    ctx.register_tool(name="topic_route_list", toolset="topic-router", schema=SCHEMA_ROUTE_LIST, handler=_tool_route_list)

    logger.info("topic-router: registered (%d routes)", len(_config.routes))
