"""OpenRouter API client for making LLM requests."""

import asyncio
import json
import re
import httpx
from typing import Any

from .config import OPENROUTER_API_KEY, OPENROUTER_API_URL
from .web_search import SEARCH_TOOL, handle_tool_call
from .llm_client import _get_proxy_url, MODELS_NO_TOOLS
from .http_client import get_shared_client

STAGGER_DELAY = 0.5  # seconds between each model request


class BearerAuth(httpx.Auth):
    """Custom auth to avoid exposing API key in request objects/logs."""
    
    def __init__(self, token: str):
        self.token = token
    
    def auth_flow(self, request: httpx.Request) -> httpx.Response:
        request.headers["Authorization"] = f"Bearer {self.token}"
        yield request


async def query_model(
    model: str,
    messages: list,
    enable_search: bool = True,
    **kwargs,  # absorbs 'timeout' and any other future args from council.py
) -> dict[str, Any] | None:
    """
    Query a single OpenRouter model.

    If enable_search=True the model is given the search_web tool and any
    tool-call it makes is automatically resolved before the final answer
    is returned — so callers get a plain {'content': '...'} dict either way.
    """
    if not OPENROUTER_API_KEY:
        print(f"Error: No API key configured")
        return None

    # Use BearerAuth to avoid exposing API key in request objects/logs
    auth = BearerAuth(OPENROUTER_API_KEY)

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    # Disable tools for models that don't support function calling
    model_supports_tools = model not in MODELS_NO_TOOLS
    if enable_search and model_supports_tools:
        payload["tools"] = [SEARCH_TOOL]
        payload["tool_choice"] = "auto"
    elif enable_search and not model_supports_tools:
        # Log that search is disabled for this model
        print(f"[{model}] Web search disabled - model doesn't support function calling")

    # Pass temperature and max_tokens if provided (and not None)
    if kwargs.get("temperature") is not None:
        payload["temperature"] = kwargs["temperature"]
    if kwargs.get("max_tokens") is not None:
        payload["max_tokens"] = kwargs["max_tokens"]

    proxy_url = _get_proxy_url()
    # Use shared client from app state for connection pooling
    client = get_shared_client()
    if client is None:
        raise RuntimeError("Shared HTTP client not initialized. Ensure the FastAPI lifespan has started.")
    
    try:
        # ── First request ────────────────────────────────────────────────
        resp = await client.post(
            OPENROUTER_API_URL, 
            json=payload, 
            auth=auth, 
            timeout=kwargs.get("timeout", 120.0),
            proxy=proxy_url
        )
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        msg    = choice["message"]

        # ── Did the model call the search tool? ──────────────────────────
        if enable_search and msg.get("tool_calls"):
            tc        = msg["tool_calls"][0]
            tool_name = tc["function"]["name"]
            tool_args = json.loads(tc["function"]["arguments"])

            print(f"[{model}] search_web({tool_args.get('query', '')})")
            search_result = await handle_tool_call(tool_name, tool_args)

            second_messages = list(messages) + [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tc],
                },
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": tool_name,
                    "content": search_result,
                },
            ]

            # Copy over temperature and max_tokens for the second request
            second_payload = {
                "model": model,
                "messages": second_messages,
            }
            if "temperature" in kwargs:
                second_payload["temperature"] = kwargs["temperature"]
            if "max_tokens" in kwargs:
                second_payload["max_tokens"] = kwargs["max_tokens"]

            resp2 = await client.post(
                OPENROUTER_API_URL, json=second_payload, auth=auth, timeout=kwargs.get("timeout", 120.0), proxy=proxy_url
            )
            resp2.raise_for_status()
            data2  = resp2.json()
            msg    = data2["choices"][0]["message"]

        # ── Return normalised result ─────────────────────────────────────
        result: dict[str, Any] = {"content": msg.get("content", "")}
        if "reasoning_details" in msg:
            result["reasoning_details"] = msg["reasoning_details"]
        return result

    except Exception as e:
        # Sanitize error message to remove API keys
        import re
        error_msg = str(e)
        error_msg = re.sub(r'(Bearer\s+)\S+', r'\1[REDACTED]', error_msg)
        error_msg = re.sub(r'sk-[a-zA-Z0-9]{20,}', '[REDACTED]', error_msg)
        print(f"Error querying {model}: {error_msg}")
        return None


async def _staggered_query(
    model: str,
    messages: list,
    enable_search: bool,
    delay: float,
) -> Any:
    """Wait `delay` seconds then query the model."""
    if delay > 0:
        await asyncio.sleep(delay)
    return await query_model(model, messages, enable_search=enable_search)


async def query_models_parallel(
    models: list[str],
    messages: list,
    enable_search: bool = True,
    **kwargs,
) -> dict[str, Any]:
    """
    Query multiple models with a staggered start to avoid simultaneous
    rate-limit hits on OpenRouter's free tier.

    Models are launched STAGGER_DELAY seconds apart but still run
    concurrently — total extra wait is (n-1) * STAGGER_DELAY, i.e.
    1.5s for 4 models, which is negligible compared to LLM response time.
    """
    tasks = [
        _staggered_query(model, messages, enable_search, i * STAGGER_DELAY)
        for i, model in enumerate(models)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    output = {}
    for model, res in zip(models, results):
        if isinstance(res, Exception):
            output[model] = {"error": str(res)}
        elif res is None:
            output[model] = {"error": "Model failed to respond"}
        else:
            output[model] = res
    return output