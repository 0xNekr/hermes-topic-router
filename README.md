# hermes-topic-router

> One bot, multiple models, auto-route LLM models per chat topic.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Hermes Agent](https://img.shields.io/badge/hermes--agent-plugin-purple.svg)](https://hermes-agent.nousresearch.com)

## The Problem

Hermes Agent doesn't support per-topic model overrides ([#4431](https://github.com/NousResearch/hermes-agent/issues/4431)). If you have a Telegram group with forum topics dedicated to different models (e.g. "Kimi K2.5", "Qwen 3.6+"), you need to manually `/model` in each topic every new session.

## The Solution

This plugin hooks into `pre_llm_call` to swap the model (and optionally the provider) before each LLM call, based on which topic the message came from. It also injects context so the LLM knows which model it's running as.

```
Telegram topic "Kimi K2.5" (thread_id=325)
  -> pre_llm_call fires
  -> Plugin reads HERMES_SESSION_KEY -> extracts thread_id
  -> Looks up config -> target model = kimi-k2.5
  -> Swaps agent.model (same provider) or agent.switch_model() (cross-provider)
  -> Injects "[You are running as kimi-k2.5]" into context
  -> LLM responds as kimi-k2.5
```

No extra bots needed. No manual `/model` switching. Configure once, works forever.

## Installation

### Method 1: Hermes CLI

```bash
hermes plugins install 0xNekr/hermes-topic-router
```

### Method 2: Git clone

```bash
git clone https://github.com/0xNekr/hermes-topic-router.git \
  ~/.hermes/plugins/topic-router
```

## Quick start

After installing, go to any Telegram forum topic and say:

```
Route this topic
```

A two-step keyboard appears:
1. **Pick a provider** (OpenAI Codex, OpenCode Go, Anthropic, etc.)
2. **Pick a model** from that provider

The route is saved automatically. All future messages in this topic use the selected model.

The provider/model list is pulled dynamically from Hermes internals. You can supplement it with `available_models` in config.json for models Hermes doesn't list yet.

## Manual configuration

```bash
cp ~/.hermes/plugins/topic-router/config.example.json \
   ~/.hermes/plugins/topic-router/config.json
```

```json
{
  "routes": [
    {
      "platform": "telegram",
      "chat_id": "-1001234567890",
      "thread_id": "325",
      "model": "kimi-k2.5",
      "provider": "opencode-go",
      "label": "Kimi K2.5"
    },
    {
      "platform": "telegram",
      "chat_id": "-1001234567890",
      "thread_id": "412",
      "model": "qwen3.6-plus",
      "provider": "opencode-go",
      "label": "Qwen 3.6+"
    }
  ],
  "default_model": null,
  "log_routing": true
}
```

### Finding your IDs

Open a topic in **Telegram Web**, the URL looks like:

```
https://t.me/c/1234567890/325
                           ^-- thread_id
```

For the **chat_id**, check the gateway logs or use a `/chatid` bot.

### Config fields

| Field | Type | Description |
|-------|------|-------------|
| `routes[].platform` | string | `"telegram"`, `"discord"`, `"slack"`, etc. |
| `routes[].chat_id` | string | Group/channel ID. Use `"*"` for wildcard. |
| `routes[].thread_id` | string | Topic/thread ID. Use `"*"` for wildcard. |
| `routes[].model` | string | Model name (same format as `/model`) |
| `routes[].provider` | string | (Optional) Provider name for cross-provider routing |
| `routes[].label` | string | (Optional) Human-readable label |
| `default_model` | string\|null | Fallback model when no route matches |
| `default_provider` | string | Default provider for routes created via Telegram selector |
| `available_models` | string[] | Extra models to add to the selector (supplements Hermes list) |
| `log_routing` | bool | Log model switches (default: `true`) |

### Route resolution order

1. **Exact match**: platform + chat_id + thread_id
2. **Chat wildcard**: platform + chat_id + thread_id=`"*"`
3. **Platform wildcard**: platform + chat_id=`"*"` + thread_id=`"*"`
4. **Default model** from config
5. **No override**: Hermes uses its configured model

### Hot reload

Edit `config.json` while the gateway is running. Changes are picked up on the next message.

## How it works

The plugin uses two mechanisms depending on the routing scenario:

**Same provider** (e.g. both models on OpenCode Go):
- Directly sets `agent.model` to the target model name
- No client rebuild needed, instant swap

**Cross-provider** (e.g. OpenAI Codex -> OpenCode Go):
- Calls `hermes_cli.model_switch.switch_model()` to resolve credentials
- Then calls `agent.switch_model()` to rebuild the client with the new provider

Both paths are triggered from the `pre_llm_call` hook via frame inspection to access the AIAgent instance. The plugin also injects context so the LLM correctly identifies itself.

## Platform support

| Platform | Status | chat_id | thread_id |
|----------|--------|---------|-----------|
| Telegram | **Tested** | Supergroup ID (negative number) | Forum topic thread ID |
| Discord | Untested (should work) | Guild/server ID | Channel/thread ID |
| Slack | Untested (should work) | Workspace ID | Channel/thread ID |
| CLI | Safe no-op | - | - |
| DMs / non-threaded groups | Safe no-op | - | - |

> The plugin reads `HERMES_SESSION_KEY` to detect the platform and thread. When no thread is present, the plugin does nothing and won't interfere.

## Limitations

- **Frame inspection**: The plugin walks the call stack to find the AIAgent instance. This works on Hermes v0.9.x but could break if Hermes restructures its agent loop.
- **Concurrent message race condition**: `HERMES_SESSION_KEY` is set via `os.environ` ([#7358](https://github.com/NousResearch/hermes-agent/issues/7358)). Low risk for small groups.
- **`/model` display**: The `/new` and `/model` commands show the default model, not the routed model. The actual routing is correct.
- **Telegram-tested only**: Discord and Slack should work (same `HERMES_SESSION_KEY` mechanism) but have not been tested. Reports welcome!

## Development

```bash
git clone https://github.com/0xNekr/hermes-topic-router.git
cd hermes-topic-router

# Symlink for local testing
ln -s "$(pwd)" ~/.hermes/plugins/topic-router

# Run tests
uv run --with pytest pytest
```

## Contributing

Contributions welcome! Please open an issue first to discuss what you'd like to change.

## License

[MIT](LICENSE)
