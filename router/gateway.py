"""
API Gateway for Ollama Router.
Full Anthropic API compatibility layer for Ollama models.

Handles:
- Tool/function calling conversion (Anthropic ↔ Ollama)
- Image/multimodal content forwarding
- SSE streaming matching Anthropic's exact spec
- Automatic API failover on errors
"""

import asyncio
import json
import uuid
from datetime import datetime
from typing import Any, Optional, Dict, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import httpx

from .config import RouterConfig
from .rotation import ApiRotator, AllApisExhaustedError

# Timeout for connecting to an API (seconds). Short so failover is fast.
CONNECT_TIMEOUT = 10
# Timeout for reading the response (seconds). Long for model inference.
READ_TIMEOUT = 300

# Models that Claude Code checks for during its pre-flight validation
VALID_MODELS = {
    "claude-3-5-sonnet-20241022", "claude-3-5-sonnet-20240620",
    "claude-3-opus-20240229", "claude-3-haiku-20240307",
    "claude-3-sonnet-20240229", "sonnet", "opus", "haiku",
    "claude-sonnet-4-6", "claude-sonnet-4-20250514",
    "claude-3-7-sonnet-20250219", "claude-3-7-sonnet",
    "claude-3-5-sonnet", "claude-3-opus", "claude-3-haiku",
}

# ==================== Request/Response Models ====================

class MessageRequest(BaseModel):
    model: str
    messages: list[dict[str, Any]]
    max_tokens: int = 4096
    system: Optional[Any] = None
    stream: bool = False
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    tools: Optional[list[dict[str, Any]]] = None
    tool_choice: Optional[dict[str, Any]] = None
    metadata: Optional[dict[str, Any]] = None
    stop_sequences: Optional[list[str]] = None

class TokenCountRequest(BaseModel):
    messages: list[dict[str, Any]]
    model: Optional[str] = None

class TokenCountResponse(BaseModel):
    input_tokens: int

# ==================== App Setup ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global config, rotator, client
    cfg = RouterConfig()
    config = cfg
    rotator = ApiRotator(cfg)
    client = httpx.AsyncClient(timeout=httpx.Timeout(
        connect=CONNECT_TIMEOUT,
        read=READ_TIMEOUT,
        write=READ_TIMEOUT,
        pool=CONNECT_TIMEOUT
    ))
    api_count = len(cfg.list_apis())
    print(f"═══ Gateway initialized with {api_count} API(s) ═══")
    print(f"    Tool calling:      ENABLED")
    print(f"    Image forwarding:  ENABLED")
    if api_count > 1:
        print(f"    Automatic failover: ENABLED ({api_count} APIs in rotation)")
    yield
    if client:
        await client.aclose()

app = FastAPI(
    title="Ollama Router Gateway",
    version="3.0.0",
    lifespan=lifespan
)

config: Optional[RouterConfig] = None
rotator: Optional[ApiRotator] = None
client: Optional[httpx.AsyncClient] = None

# ==================== Info Endpoints ====================

@app.get("/")
async def root():
    return {"status": "ok", "gateway": "ollama-router", "version": "3.0.0",
            "features": ["tool_calling", "image_support", "auto_failover", "streaming"]}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/v1/models")
async def list_models():
    """Returns an exhaustive list of models to bypass client-side validation."""
    return {
        "object": "list",
        "data": [
            {"id": m, "object": "model", "created": 1743481200, "owned_by": "anthropic"}
            for m in VALID_MODELS
        ]
    }

@app.get("/v1/user")
async def get_user():
    return {"id": "user-ollama-router", "email": "user@local", "name": "Local User"}

@app.get("/v1/account/limits")
async def get_account_limits():
    return {
        "rate_limit": {"max_requests_per_minute": 10000, "max_tokens_per_minute": 1000000},
        "usage": {"requests_today": 0, "tokens_today": 0}
    }

@app.get("/v1/status")
async def get_gateway_status():
    """Show current gateway status including active API and rotation info."""
    if not config or not rotator:
        return {"status": "not_initialized"}
    stats = rotator.get_rotation_stats()
    current = rotator.get_current_api()
    return {
        "status": "running",
        "current_api": current.name if current else None,
        "current_model": current.model_name if current else None,
        "total_apis": stats["total_apis"],
        "active_apis": stats["active_apis"],
        "on_cooldown": stats["on_cooldown"],
        "total_rotations": stats["total_rotations"],
        "apis": stats["apis"],
    }

# ==================== Format Conversion: Anthropic → Ollama ====================

def _convert_tools_to_ollama(tools: list[dict]) -> list[dict]:
    """Convert Anthropic tool definitions to Ollama/OpenAI function format.

    Anthropic: {name, description, input_schema: {type, properties, required}}
    Ollama:    {type: "function", function: {name, description, parameters: {type, properties, required}}}
    """
    ollama_tools = []
    for tool in tools:
        ollama_tools.append({
            "type": "function",
            "function": {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {})
            }
        })
    return ollama_tools


def _convert_messages(messages: list[dict], system: Any = None) -> list[dict]:
    """Convert Anthropic message format to Ollama format.

    Handles all content block types:
    - text        → plain text content
    - tool_use    → tool_calls array on assistant messages
    - tool_result → separate role:"tool" messages
    - image       → images array on the message
    """
    ollama_messages = []

    # --- System prompt ---
    system_text = system
    if isinstance(system, list):
        text_parts = [
            block.get("text", "") for block in system
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        system_text = " ".join(text_parts)
    if system_text:
        ollama_messages.append({"role": "system", "content": str(system_text)})

    # --- Convert each message ---
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Simple string content — no conversion needed
        if isinstance(content, str):
            ollama_messages.append({"role": role, "content": content})
            continue

        # Complex content: list of typed blocks
        if isinstance(content, list):
            text_parts = []
            tool_uses = []
            tool_results = []
            images = []

            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")

                if btype == "text":
                    text_parts.append(block.get("text", ""))

                elif btype == "tool_use":
                    # Assistant's tool call → Ollama tool_calls format
                    raw_input = block.get("input", {})
                    # Ollama expects arguments as a JSON object, not a string
                    if isinstance(raw_input, str):
                        try:
                            args_obj = json.loads(raw_input)
                        except json.JSONDecodeError:
                            args_obj = {"raw": raw_input}
                    else:
                        args_obj = raw_input
                    tool_uses.append({
                        "id": block.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": args_obj
                        }
                    })

                elif btype == "tool_result":
                    # User's tool result → Ollama role:"tool" message
                    result_content = block.get("content", "")
                    if isinstance(result_content, list):
                        result_text = " ".join(
                            b.get("text", "") for b in result_content
                            if isinstance(b, dict) and b.get("type") == "text"
                        )
                    else:
                        result_text = str(result_content)

                    # Prefix errors so the model knows it failed
                    if block.get("is_error"):
                        result_text = f"[ERROR] {result_text}"

                    tool_results.append({
                        "role": "tool",
                        "content": result_text
                    })

                elif btype == "image":
                    # Image content → Ollama images array
                    source = block.get("source", {})
                    if source.get("type") == "base64":
                        images.append(source.get("data", ""))

            # --- Build the Ollama message(s) ---

            if role == "assistant":
                # Assistant message with optional tool_calls
                msg_data = {"role": "assistant", "content": " ".join(text_parts)}
                if tool_uses:
                    msg_data["tool_calls"] = tool_uses
                ollama_messages.append(msg_data)

            elif tool_results:
                # User message with tool results → split into text + tool messages
                if text_parts:
                    user_msg = {"role": "user", "content": " ".join(text_parts)}
                    if images:
                        user_msg["images"] = images
                    ollama_messages.append(user_msg)
                for tr in tool_results:
                    ollama_messages.append(tr)

            else:
                # Regular user/other message
                msg_data = {"role": role, "content": " ".join(text_parts)}
                if images:
                    msg_data["images"] = images
                ollama_messages.append(msg_data)

    return ollama_messages

# ==================== Format Conversion: Ollama → Anthropic ====================

def _map_stop_reason(done_reason: str, has_tool_calls: bool) -> str:
    """Map Ollama's done_reason to Anthropic's stop_reason."""
    if has_tool_calls:
        return "tool_use"
    mapping = {
        "stop": "end_turn",
        "length": "max_tokens",
        "end_turn": "end_turn",
    }
    return mapping.get(done_reason, "end_turn")


def _convert_to_anthropic(ollama_data: dict, model: str) -> dict:
    """Convert a complete Ollama response to Anthropic message format.

    Handles text content, tool_calls, and usage stats.
    """
    message = ollama_data.get("message", {})
    text_content = message.get("content", "")
    tool_calls = message.get("tool_calls", [])

    content = []

    # Text content block
    if text_content:
        content.append({"type": "text", "text": text_content})

    # Tool use content blocks
    for tc in tool_calls:
        func = tc.get("function", {})
        raw_args = func.get("arguments", {})

        # Ollama returns arguments as dict or string depending on version
        if isinstance(raw_args, str):
            try:
                parsed_args = json.loads(raw_args)
            except json.JSONDecodeError:
                parsed_args = {"raw": raw_args}
        else:
            parsed_args = raw_args

        content.append({
            "type": "tool_use",
            "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:12]}"),
            "name": func.get("name", ""),
            "input": parsed_args
        })

    # Ensure at least one content block
    if not content:
        content.append({"type": "text", "text": ""})

    done_reason = ollama_data.get("done_reason", "stop")
    stop_reason = _map_stop_reason(done_reason, bool(tool_calls))

    return {
        "id": f"msg_{uuid.uuid4().hex[:12]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": ollama_data.get("prompt_eval_count", 0),
            "output_tokens": ollama_data.get("eval_count", 0)
        }
    }

# ==================== Core Message Endpoint with Failover ====================

@app.post("/v1/messages")
async def create_message(request: MessageRequest):
    """Handle Anthropic-compatible message requests with automatic API failover.

    Supports: text, tool calling, images, streaming.
    If the current API fails, automatically tries the next one.
    """
    if not config or not rotator or not client:
        raise HTTPException(status_code=500, detail="Gateway not initialized")

    active_apis = config.get_active_apis()
    max_retries = max(len(active_apis), 1)
    last_error = None

    for attempt in range(max_retries):
        api = None
        response = None
        try:
            api = await rotator.get_next_api()
            has_tools = bool(request.tools)
            print(f"[Request] Attempt {attempt + 1}/{max_retries} → API '{api.name}' "
                  f"(model: {api.model_name}, tools: {has_tools})")

            # Build the Ollama request
            ollama_url = f"{api.api_base}/chat"
            ollama_request = {
                "model": api.model_name,
                "messages": _convert_messages(request.messages, request.system),
                "stream": request.stream,
                "options": {}
            }

            # Convert and attach tools if present
            if request.tools:
                ollama_request["tools"] = _convert_tools_to_ollama(request.tools)

            # Model parameters
            if request.temperature is not None:
                ollama_request["options"]["temperature"] = request.temperature
            if request.top_p is not None:
                ollama_request["options"]["top_p"] = request.top_p
            if request.top_k is not None:
                ollama_request["options"]["top_k"] = request.top_k
            if request.max_tokens:
                ollama_request["options"]["num_predict"] = request.max_tokens
            if request.stop_sequences:
                ollama_request["options"]["stop"] = request.stop_sequences

            # Auth injection
            headers = {"Content-Type": "application/json"}
            if api.api_key:
                headers["Authorization"] = f"Bearer {api.api_key}"

            # Send request with manual lifecycle management
            req = client.build_request("POST", ollama_url, json=ollama_request, headers=headers)
            response = await client.send(req, stream=True)

            # --- Handle error status codes ---
            if response.status_code == 429:
                await response.aclose()
                response = None
                await rotator.mark_failed(api.api_key, "Rate limit (429)")
                last_error = "Rate limit exceeded (429)"
                print(f"  ↳ 429 Rate Limited → rotating to next API...")
                continue

            if response.status_code != 200:
                status_code = response.status_code
                try:
                    error_body = (await response.aread()).decode(errors="replace")
                except Exception:
                    error_body = "Could not read response body"
                await response.aclose()
                response = None
                await rotator.mark_failed(api.api_key, f"HTTP {status_code}: {error_body}")
                last_error = f"HTTP {status_code}: {error_body}"
                print(f"  ↳ Error {status_code} → rotating to next API...")
                continue

            # --- Success! Return the response ---
            print(f"  ↳ ✓ Success (stream={request.stream})")

            if request.stream:
                captured_response = response
                captured_api_key = api.api_key
                response = None  # Prevent cleanup in finally block
                return StreamingResponse(
                    _stream_with_cleanup(captured_response, captured_api_key, request.model),
                    media_type="text/event-stream",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache", "Connection": "keep-alive"}
                )
            else:
                # Non-streaming: read full response
                try:
                    full_text = ""
                    all_tool_calls = []
                    final_data = {}

                    async for line in response.aiter_lines():
                        if not line or not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                            full_text += data.get("message", {}).get("content", "")

                            # Collect tool calls
                            tc = data.get("message", {}).get("tool_calls", [])
                            if tc:
                                all_tool_calls.extend(tc)

                            if data.get("done"):
                                final_data = data
                        except json.JSONDecodeError:
                            continue
                finally:
                    await response.aclose()
                    response = None

                await rotator.mark_success(api.api_key)

                # Build the combined response
                final_data["message"] = final_data.get("message", {})
                final_data["message"]["content"] = full_text
                if all_tool_calls:
                    final_data["message"]["tool_calls"] = all_tool_calls

                return _convert_to_anthropic(final_data, request.model)

        except AllApisExhaustedError:
            raise HTTPException(
                status_code=503,
                detail="All APIs are on cooldown. Wait or use 'ollama-router reset-cooldowns'."
            )
        except HTTPException:
            raise
        except Exception as e:
            if response:
                await response.aclose()
                response = None
            if api:
                await rotator.mark_failed(api.api_key, str(e))
            last_error = str(e)
            print(f"  ↳ Exception: {type(e).__name__}: {e} → rotating to next API...")
            continue

    raise HTTPException(
        status_code=502,
        detail=f"All {max_retries} API(s) failed. Last error: {last_error}"
    )

@app.post("/v1/messages/count_tokens")
async def count_tokens(request: TokenCountRequest):
    return TokenCountResponse(input_tokens=len(request.messages) * 15)

# ==================== Streaming with Tool Call Support ====================

async def _stream_with_cleanup(response, api_key: str, model: str = "claude-3-5-sonnet-20241022"):
    """Stream Anthropic-compatible SSE events with full tool calling support.

    Handles the complete event sequence:
      1. message_start       → full message object
      2. content_block_start → text or tool_use block header
      3. ping                → keepalive
      4. content_block_delta → text_delta or input_json_delta
      5. content_block_stop  → end of block
      6. message_delta       → stop_reason and usage
      7. message_stop        → end of message

    Tool calls from Ollama arrive in the final 'done' chunk and are
    emitted as separate content blocks after the text block.
    """
    msg_id = f"msg_{uuid.uuid4().hex[:12]}"
    output_tokens = 0
    content_block_index = 0
    text_block_open = False
    accumulated_tool_calls = []

    try:
        # 1. message_start
        message_start = {
            "type": "message_start",
            "message": {
                "id": msg_id, "type": "message", "role": "assistant",
                "content": [], "model": model,
                "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0}
            }
        }
        yield f"event: message_start\ndata: {json.dumps(message_start)}\n\n"

        # 2. ping
        yield f"event: ping\ndata: {json.dumps({'type': 'ping'})}\n\n"

        # 3. Process streamed tokens from Ollama
        async for line in response.aiter_lines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                message = data.get("message", {})
                content = message.get("content", "")
                tool_calls = message.get("tool_calls", [])
                done = data.get("done", False)

                # --- Text content ---
                if content:
                    if not text_block_open:
                        block_start = {
                            "type": "content_block_start",
                            "index": content_block_index,
                            "content_block": {"type": "text", "text": ""}
                        }
                        yield f"event: content_block_start\ndata: {json.dumps(block_start)}\n\n"
                        text_block_open = True

                    output_tokens += 1
                    delta = {
                        "type": "content_block_delta",
                        "index": content_block_index,
                        "delta": {"type": "text_delta", "text": content}
                    }
                    yield f"event: content_block_delta\ndata: {json.dumps(delta)}\n\n"

                # --- Accumulate tool calls ---
                if tool_calls:
                    accumulated_tool_calls.extend(tool_calls)

                # --- Done: emit closing events ---
                if done:
                    # Close text block if it was opened
                    if text_block_open:
                        yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': content_block_index})}\n\n"
                        content_block_index += 1

                    # Emit tool_use content blocks
                    for tc in accumulated_tool_calls:
                        func = tc.get("function", {})
                        tool_id = tc.get("id", f"toolu_{uuid.uuid4().hex[:12]}")
                        tool_name = func.get("name", "")
                        raw_args = func.get("arguments", {})

                        # Parse arguments
                        if isinstance(raw_args, str):
                            args_str = raw_args
                            try:
                                parsed_args = json.loads(raw_args)
                            except json.JSONDecodeError:
                                parsed_args = {"raw": raw_args}
                        else:
                            parsed_args = raw_args
                            args_str = json.dumps(raw_args)

                        # content_block_start for tool_use
                        tool_block_start = {
                            "type": "content_block_start",
                            "index": content_block_index,
                            "content_block": {
                                "type": "tool_use",
                                "id": tool_id,
                                "name": tool_name,
                                "input": {}
                            }
                        }
                        yield f"event: content_block_start\ndata: {json.dumps(tool_block_start)}\n\n"

                        # content_block_delta with the full JSON input
                        input_delta = {
                            "type": "content_block_delta",
                            "index": content_block_index,
                            "delta": {"type": "input_json_delta", "partial_json": args_str}
                        }
                        yield f"event: content_block_delta\ndata: {json.dumps(input_delta)}\n\n"

                        # content_block_stop
                        yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': content_block_index})}\n\n"
                        content_block_index += 1

                    # message_delta with stop_reason
                    done_reason = data.get("done_reason", "stop")
                    stop_reason = _map_stop_reason(done_reason, bool(accumulated_tool_calls))
                    msg_delta = {
                        "type": "message_delta",
                        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                        "usage": {"output_tokens": output_tokens}
                    }
                    yield f"event: message_delta\ndata: {json.dumps(msg_delta)}\n\n"

                    # message_stop
                    yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"

            except json.JSONDecodeError:
                continue

        # Mark success after full stream completes
        if rotator:
            await rotator.mark_success(api_key)
    finally:
        await response.aclose()

# ==================== Entry Point ====================

def run_gateway(host: str = "0.0.0.0", port: int = 8082):
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info")

if __name__ == "__main__":
    run_gateway()
