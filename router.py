"""Core routing logic - pure functions, no side effects."""

from __future__ import annotations

from config import RouterConfig


def get_model_for_topic(
    config: RouterConfig,
    platform: str,
    chat_id: str,
    thread_id: str,
) -> str | None:
    """Look up the model for a given platform/chat/thread combination.

    Resolution order (first match wins):
    1. Exact match: platform + chat_id + thread_id
    2. Chat-level wildcard: platform + chat_id + thread_id="*"
    3. Platform wildcard: platform + chat_id="*" + thread_id="*"
    4. default_model from config
    5. None (no override, use Hermes default)
    """
    if not platform or not thread_id:
        return config.default_model

    platform = platform.lower()

    # Single pass: collect best match by specificity
    chat_wildcard = None
    platform_wildcard = None

    for route in config.routes:
        if route.platform != platform:
            continue

        if route.chat_id == chat_id and route.thread_id == thread_id:
            return route.model  # exact match, return immediately

        if route.chat_id == chat_id and route.thread_id == "*" and chat_wildcard is None:
            chat_wildcard = route.model

        if route.chat_id == "*" and route.thread_id == "*" and platform_wildcard is None:
            platform_wildcard = route.model

    return chat_wildcard or platform_wildcard or config.default_model


def format_route_table(config: RouterConfig) -> str:
    """Format the current routing config as a readable table."""
    if not config.routes:
        return "No routes configured. Copy config.example.json to config.json."

    lines = [
        "**Topic Router - Active Routes**",
        "",
        "| Platform | Chat ID | Thread ID | Model | Label |",
        "|----------|---------|-----------|-------|-------|",
    ]

    for r in config.routes:
        label = r.label or "-"
        lines.append(f"| {r.platform} | `{r.chat_id}` | `{r.thread_id}` | `{r.model}` | {label} |")

    if config.default_model:
        lines.append("")
        lines.append(f"**Default model:** `{config.default_model}`")

    lines.append("")
    lines.append(f"**Routing logs:** {'enabled' if config.log_routing else 'disabled'}")

    return "\n".join(lines)
