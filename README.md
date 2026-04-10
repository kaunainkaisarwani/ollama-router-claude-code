# Ollama Router

<div align="center">

**Run Claude Code with any Ollama model — with automatic API key rotation and failover.**

[Quick Start](#-quick-start) • [Installation](#installation) • [Commands](#commands) • [How It Works](#how-it-works) • [Troubleshooting](#troubleshooting)

</div>

---

## What Is This?

Ollama Router is a **drop-in proxy** that lets you use [Claude Code](https://docs.anthropic.com/claude-code) with **any Ollama model** — Gemma, Qwen, LLaMA, Mistral, and more. It translates between Anthropic's API format and Ollama's API format in real-time, so Claude Code thinks it's talking to Anthropic, but it's actually talking to your Ollama model.

### Key Features

| Feature | Description |
|---------|-------------|
| 🔄 **API Key Rotation** | Multiple API keys with automatic round-robin rotation |
| ⚡ **Auto-Failover** | If one API fails, instantly tries the next one |
| 🛠️ **Full Tool Support** | File editing, command running — everything Claude Code does |
| 🖼️ **Image Support** | Multimodal image forwarding for vision models |
| 📡 **Real-time Streaming** | Token-by-token output, not buffered |
| ⏱️ **Smart Cooldowns** | Rate-limited APIs auto-recover after cooldown |
| 🎨 **Claude Code TUI** | Full interactive terminal UI preserved |

---

## 🚀 Quick Start

### 1. Install

```bash
cd ollama-router
pip install .
```

### 2. Add your Ollama API key

```bash
ollama-router add my-key -k YOUR_API_KEY -m gemma4:31b
```

> **Finding your model name:** Run `curl https://ollama.com/api/tags -H "Authorization: Bearer YOUR_KEY"` to see available models. Use the exact name (e.g., `gemma4:31b`, not `gemma4`).

### 3. Start the proxy

```bash
ollama-router proxy
```

A new terminal will automatically open with Claude Code.
If it doesn't, open a new terminal and run:

```bash
ollama-router launch
```

> **Tip:** You can also run `ollama-router` and select option **1** from the interactive menu.

---

## Installation

### Prerequisites

- **Python 3.9+**
- **pip** (Python package manager)
- **Claude Code CLI** — `npm install -g @anthropic-ai/claude-code`
- **Ollama API key** — from [ollama.com](https://ollama.com)

### Install from Source

```bash
git clone https://github.com/kaunainkaisarwani/ollama-router-claude-code.git
cd ollama-router-claude-code
pip install .

# Verify
ollama-router --version
```

---

## Commands

### `ollama-router add` — Add an API key

```bash
# Interactive (prompts for everything)
ollama-router add

# One-liner
ollama-router add my-account -k sk-ollama-xxx -m gemma4:31b

# With custom API base
ollama-router add local -k my-key -m llama3.1:8b --base http://localhost:11434/api
```

### `ollama-router proxy` — Start the gateway

```bash
# Start proxy only
ollama-router proxy

# Start proxy + auto-launch Claude Code in a new terminal
ollama-router proxy --launch

# Custom port
ollama-router proxy --port 4000
```

The `--launch` / `-l` flag automatically opens a new terminal and runs `ollama-router launch` for you. Works on macOS, Windows, and Linux.

### `ollama-router launch` — Start Claude Code

```bash
# Interactive mode (default)
ollama-router launch

# With initial prompt
ollama-router launch -p "Help me refactor this code"

# Pass args to Claude Code
ollama-router launch -- --verbose
```

> **Note:** The `launch` command auto-sets `ANTHROPIC_BASE_URL` — no `export` needed.

### `ollama-router list` — Show all APIs

```bash
ollama-router list
```

```
                         3 API(s) configured
┏━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┓
┃ Index ┃ Name       ┃ API Key      ┃ Model     ┃ Status   ┃ Requests ┃ Cooldown ┃
┡━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━┩
│     0 │ → key-1 ←  │ edd3...j_nj  │ gemma4:31b│ ACTIVE   │       42 │    -     │
│     1 │ key-2      │ a1b2...c3d4  │ gemma4:31b│ ACTIVE   │       38 │    -     │
│     2 │ key-3      │ x9y8...w7v6  │ gemma4:31b│ COOLDOWN │       51 │   2m 30s │
└───────┴────────────┴──────────────┴───────────┴──────────┴──────────┴──────────┘
```

### Other Commands

| Command | Description |
|---------|-------------|
| `ollama-router status` | Detailed router stats |
| `ollama-router check` | Health-check all APIs |
| `ollama-router use <name\|index>` | Switch to a specific API |
| `ollama-router remove <name\|index>` | Remove an API |
| `ollama-router reset-cooldowns --yes` | Clear all cooldowns |
| `ollama-router reset-stats --yes` | Reset all stats |

---

## How It Works

### Architecture

```
┌──────────────┐     ┌─────────────────────────┐     ┌──────────────┐
│  Claude Code │────▶│   Ollama Router Proxy   │────▶│  Ollama API  │
│   (Client)   │◀────│  (Anthropic → Ollama)   │◀────│  (Server)    │
└──────────────┘     └─────────────────────────┘     └──────────────┘
                              │
                     Translates formats:
                     • Messages ↔ Chat
                     • Tools ↔ Functions
                     • SSE ↔ NDJSON
                     • Images ↔ Base64
```

1. **Claude Code** sends requests in Anthropic's API format to `http://localhost:8082`
2. **The Proxy** converts them to Ollama's format and forwards to `ollama.com/api`
3. **Ollama** processes the request with your chosen model
4. **The Proxy** converts the response back to Anthropic's format
5. **Claude Code** receives the response as if it came from Anthropic

### Tool Calling Flow

Claude Code relies heavily on tool calling for file editing, command execution, etc. The proxy handles the full conversion:

```
Claude Code sends:                    Proxy converts to Ollama:
─────────────────                    ────────────────────────
tools: [{                           tools: [{
  name: "read_file",                  type: "function",
  input_schema: {                     function: {
    properties: {                       name: "read_file",
      path: {type: "string"}            parameters: {
    }                                     properties: {
  }                                         path: {type: "string"}
}]                                        }
                                        }
                                      }
                                    }]
```

```
Ollama responds:                     Proxy converts to Anthropic:
────────────────                     ────────────────────────────
message: {                           content: [{
  tool_calls: [{                       type: "tool_use",
    function: {                         id: "toolu_abc123",
      name: "read_file",               name: "read_file",
      arguments: {path: "..."}          input: {path: "..."}
    }                                }]
  }]                                 stop_reason: "tool_use"
}
```

### API Rotation & Failover

```
Request comes in
       │
       ▼
  ┌─ Try API 1 ──▶ Success? ──▶ Return response
  │       │
  │    Failure (429, timeout, error)
  │       │
  │       ▼
  │  Set cooldown on API 1
  │       │
  ├─ Try API 2 ──▶ Success? ──▶ Return response
  │       │
  │    Failure
  │       │
  └─ Try API 3 ──▶ ... and so on
          │
       All failed
          │
          ▼
    Return 502 error
```

### Cooldown Durations

| Error Type | Cooldown | Examples |
|------------|----------|----------|
| Temporary | 2 min | Timeout, connection error |
| Rate Limit | 5 min | HTTP 429, "too many requests" |
| Session Limit | 15 min | "session limit reached" |
| Quota Exhausted | 60 min | "quota exceeded" |

---

## Compatible Models

Works with **any Ollama model**. For Claude Code's tool calling to work, the model must support **function calling**.

### Recommended Models (tool calling support)

| Model | Size | Tool Calling |
|-------|------|:------------:|
| `gemma4:31b` | 31B | ✅ |
| `qwen3:72b` | 72B | ✅ |
| `qwen3-coder:480b` | 480B | ✅ |
| `llama3.1:70b` | 70B | ✅ |
| `mistral-large-3:675b` | 675B | ✅ |
| `deepseek-v3.2` | 671B | ✅ |
| `gemma3:27b` | 27B | ✅ |

### Text-only Models (chat works, no tool calling)

| Model | Size | Notes |
|-------|------|-------|
| `gemma3:4b` | 4B | Fast, text-only |
| `phi3:mini` | 3.8B | Lightweight |
| `tinyllama` | 1.1B | Very small |

> **Tip:** Use `ollama-router check` to verify your API key and model are working.

---

## Configuration

### Config File

Stored at: `~/.ollama-router/config.json`

```json
{
  "apis": [
    {
      "name": "my-key",
      "api_key": "your-ollama-api-key",
      "api_base": "https://ollama.com/api",
      "model_name": "gemma4:31b",
      "cooldown_until": null,
      "failed_count": 0,
      "total_requests": 42,
      "is_active": true
    }
  ],
  "state": {
    "current_index": 0,
    "total_rotations": 15
  }
}
```

### Gateway Endpoints

When the proxy is running, these endpoints are available:

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /v1/models` | List models (for Claude Code validation) |
| `GET /v1/status` | Current API, rotation stats, per-API health |
| `POST /v1/messages` | Main chat endpoint (with tool calling) |
| `POST /v1/messages/count_tokens` | Token counting |

### Environment Variables

| Variable | Set By | Description |
|----------|--------|-------------|
| `ANTHROPIC_BASE_URL` | Auto | Gateway URL (default: `http://localhost:8082`) |
| `ANTHROPIC_API_KEY` | Auto | Dummy key for Claude Code validation |
| `OLLAMA_ROUTER_ACTIVE` | Auto | Set to `"1"` when router is active |

---

## Troubleshooting

### "Gateway is not running!"

Start the proxy first in a separate terminal:
```bash
ollama-router proxy
```

### "model 'xxx' not found"

Your configured model name doesn't match what's available on Ollama. Check available models:
```bash
curl -s https://ollama.com/api/tags \
  -H "Authorization: Bearer YOUR_API_KEY" | python3 -c \
  "import json,sys; [print(m['name']) for m in json.load(sys.stdin)['models']]"
```
Then update your config or re-add the API with the correct model name.

### "All APIs are on cooldown"

```bash
ollama-router reset-cooldowns --yes
```

### Claude Code shows text all at once (not streaming)

Make sure you're using the latest version with the fixed SSE streaming format:
```bash
pip install .  # Reinstall
```

### "Auth conflict" error

```bash
unset ANTHROPIC_AUTH_TOKEN
unset CLAUDE_AUTH_TOKEN
claude /logout
```

### Tool calling not working

Make sure your model supports function calling. Try a model like `gemma4:31b`, `qwen3:72b`, or `llama3.1:70b`.

### Claude Code CLI not found

```bash
npm install -g @anthropic-ai/claude-code
claude --version
```

---

## Tips

1. **Start with one API key** — Test it works before adding more
2. **Use descriptive names** — `ollama-router add primary -k ...` is clearer than `ollama-router add 1 -k ...`
3. **Monitor with `list`** — The `→ ← ` markers show the currently active API
4. **Use `check` before `launch`** — Verify your APIs are healthy first
5. **Multiple keys = uninterrupted coding** — Add 2-3 keys so you never hit rate limits

---

## License

MIT License — Feel free to use and modify.

---

<div align="center">

**Use any Ollama model with Claude Code — seamlessly.** 🚀

</div>
