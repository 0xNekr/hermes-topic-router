# hermes-topic-router

> One bot, multiple models, auto-route LLM models per chat topic.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Hermes Agent](https://img.shields.io/badge/hermes--agent-plugin-purple.svg)](https://hermes-agent.nousresearch.com)

## The Problem

Hermes Agent doesn't support per-topic model overrides ([#4431](https://github.com/NousResearch/hermes-agent/issues/4431)). If you have a Telegram group with forum topics dedicated to different models (e.g. "Kimi K2.5", "Qwen 3.6+"), you need to manually `/model` in each topic every new session.

## The Solution

This plugin intercepts API requests via Hermes' `pre_api_request` hook and automatically swaps the model based on which topic the message came from.

```
Telegram topic "Kimi K2.5" (thread_id=5)
  -> Gateway sets HERMES_SESSION_THREAD_ID=5
  -> Agent processes message
  -> pre_api_request hook fires
  -> Plugin reads thread_id -> looks up config -> model = kimi-k2.5
  -> Mutates request body with correct model
  -> API request goes out with the right model
```

No extra bots needed. No manual `/model` switching. Just configure once.

## Installation

### Method 1: Hermes CLI (recommended)

```bash
hermes plugins install 0xNekr/hermes-topic-router
```

### Method 2: Git clone

```bash
git clone https://github.com/0xNekr/hermes-topic-router.git \
  ~/.hermes/plugins/topic-router
```

## Configuration

### 1. Find your topic thread IDs

Open each forum topic in **Telegram Web** or **Desktop**. The URL looks like:

```
https://t.me/c/1234567890/5
                          ^-- this is the thread_id
```

For the **chat_id**, you can use `/chatid` bots or check the gateway logs -- Hermes logs the chat_id on incoming messages.

### 2. Create your config

```bash
cp ~/.hermes/plugins/topic-router/config.example.json \
   ~/.hermes/plugins/topic-router/config.json
```

Edit `config.json`:

```json
{
  "routes": [
    {
      "platform": "telegram",
      "chat_id": "-1001234567890",
      "thread_id": "5",
      "model": "kimi-k2.5",
      "label": "Kimi K2.5"
    },
    {
      "platform": "telegram",
      "chat_id": "-1001234567890",
      "thread_id": "12",
      "model": "qwen3.6-plus",
      "label": "Qwen 3.6+"
    }
  ],
  "default_model": null,
  "log_routing": true
}
```

### Config fields

| Field | Type | Description |
|-------|------|-------------|
| `routes[].platform` | string | `"telegram"`, `"discord"`, `"slack"`, etc. |
| `routes[].chat_id` | string | Group/channel ID. Use `"*"` for wildcard. |
| `routes[].thread_id` | string | Topic/thread ID. Use `"*"` for wildcard. |
| `routes[].model` | string | Model name (same format as `hermes config` / `/model`) |
| `routes[].label` | string | (Optional) Human-readable label for `/routes` display |
| `default_model` | string\|null | Fallback model when no route matches |
| `log_routing` | bool | Log model switches (default: `true`) |

### Route resolution order

1. **Exact match**: platform + chat_id + thread_id
2. **Chat wildcard**: platform + chat_id + thread_id=`"*"`
3. **Platform wildcard**: platform + chat_id=`"*"` + thread_id=`"*"`
4. **Default model**: `default_model` from config
5. **No override**: Hermes uses its configured model

### Environment variable

You can override the config path:

```bash
export TOPIC_ROUTER_CONFIG=/path/to/my/config.json
```

## Usage

Once installed and configured, the plugin works transparently. Send a message in any mapped topic and the model is automatically swapped.

### Check active routes

Type `/routes` in any Hermes chat to see the current routing table.

### Hot reload

Edit `config.json` while the gateway is running -- changes are picked up automatically on the next message (no restart needed).

## Platform support

| Platform | Status | chat_id | thread_id |
|----------|--------|---------|-----------|
| Telegram | **Tested** | Supergroup ID (negative number) | Forum topic thread ID |
| Discord | Untested (should work) | Guild/server ID | Channel/thread ID |
| Slack | Untested (should work) | Workspace ID | Channel/thread ID |
| CLI | Safe no-op | - | - |
| DMs / non-threaded groups | Safe no-op | - | - |

> The plugin relies on `HERMES_SESSION_THREAD_ID` which the gateway sets for all threaded platforms. When no thread_id is present, the plugin does nothing and won't interfere with non-threaded conversations.

## How it works

The plugin registers a `pre_api_request` hook that fires before every LLM API call. It reads `HERMES_SESSION_THREAD_ID` and `HERMES_SESSION_CHAT_ID` from environment variables (set by the Hermes gateway), looks up the matching route, and mutates the request body's `model` field in-place.

This works because:
- Python dicts are mutable and passed by reference
- All models share the same provider (same API key, same base URL)
- Only the model name in the request body needs to change

## Limitations

- **Undocumented behavior**: The `pre_api_request` body mutation is not officially documented by Hermes. If a future version deep-copies the body before passing it to hooks, this plugin will stop working. Tested with Hermes Agent v0.9.x.
- **Concurrent message race condition**: Hermes uses `os.environ` for session context ([#7358](https://github.com/NousResearch/hermes-agent/issues/7358)). Under heavy concurrent load, two messages processed simultaneously might read each other's thread_id. Low risk for small groups.
- **Same provider only**: All routed models must be accessible through the same provider (e.g. all on OpenCode Go, or all on OpenRouter). Cross-provider routing is not supported.
- **Telegram-tested only**: Discord and Slack should work (same `HERMES_SESSION_THREAD_ID` mechanism) but have not been tested yet. Reports welcome!

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
