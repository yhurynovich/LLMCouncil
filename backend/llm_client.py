"""Generic LLM client — routes queries to the correct provider."""
import asyncio
import json
import os
import time
import httpx
from typing import Any

from .providers import get_provider, get_provider_api_key
from .web_search import SEARCH_TOOL, handle_tool_call

STAGGER_DELAY = 0.5


def _get_proxy_url() -> str | None:
    """Resolve proxy URL from env, preferring HTTP/HTTPS proxy over unsupported schemes."""
    # Prefer protocol-specific proxies (httpx handles these natively)
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        val = os.environ.get(var)
        if val:
            return val
    return None


def _parse_model_id(model: str) -> tuple[str, str]:
    """Split 'provider/model_name' into (provider, model_name).
    If no slash, defaults to 'openrouter' provider."""
    if "/" in model:
        parts = model.split("/", 1)
        return parts[0], parts[1]
    return "openrouter", model


async def query_model(
    model: str,
    messages: list,
    enable_search: bool = True,
    **kwargs,
) -> dict[str, Any] | None:
    provider_name, model_id = _parse_model_id(model)
    provider = get_provider(provider_name)
    if provider is None:
        print(f"Error: Unknown provider '{provider_name}' for model '{model}'")
        return None

    api_key = get_provider_api_key(provider)
    base_url = provider["base_url"]

    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # OpenRouter-specific headers
    if provider_name == "openrouter":
        headers["HTTP-Referer"] = "https://llm-council.local"
        headers["X-Title"] = "LLM Council"

    payload: dict[str, Any] = {
        "model": model_id,
        "messages": messages,
    }

    # Only add tools for OpenRouter (custom providers may not support them)
    if enable_search and provider_name == "openrouter":
        payload["tools"] = [SEARCH_TOOL]
        payload["tool_choice"] = "auto"

    proxy = _get_proxy_url()
    async with httpx.AsyncClient(timeout=120.0, proxy=proxy, trust_env=False) as client:
        try:
            t0 = time.monotonic()
            resp = await client.post(base_url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

            choice = data["choices"][0]
            msg = choice["message"]

            # Handle tool calls (OpenRouter only)
            if enable_search and provider_name == "openrouter" and msg.get("tool_calls"):
                tc = msg["tool_calls"][0]
                tool_name = tc["function"]["name"]
                tool_args = json.loads(tc["function"]["arguments"])

                print(f"[{model}] search_web({tool_args.get('query', '')})")
                search_result = handle_tool_call(tool_name, tool_args)

                second_messages = list(messages) + [
                    {"role": "assistant", "content": None, "tool_calls": [tc]},
                    {"role": "tool", "tool_call_id": tc["id"], "name": tool_name, "content": search_result},
                ]
                second_payload = {"model": model_id, "messages": second_messages}
                resp2 = await client.post(base_url, headers=headers, json=second_payload)
                resp2.raise_for_status()
                msg = resp2.json()["choices"][0]["message"]

            elapsed = round(time.monotonic() - t0, 2)
            result: dict[str, Any] = {"content": msg.get("content", ""), "response_time": elapsed}
            if "reasoning_details" in msg:
                result["reasoning_details"] = msg["reasoning_details"]
            return result

        except Exception as e:
            print(f"Error querying {model}: {e}", flush=True)
            return {"error": str(e)}


async def _staggered_query(model, messages, enable_search, delay):
    if delay > 0:
        await asyncio.sleep(delay)
    return await query_model(model, messages, enable_search=enable_search)


async def query_models_parallel(
    models: list[str],
    messages: list,
    enable_search: bool = True,
    **kwargs,
) -> dict[str, Any]:
    tasks = [
        _staggered_query(model, messages, enable_search, i * STAGGER_DELAY)
        for i, model in enumerate(models)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return {
        model: (None if isinstance(res, Exception) else res)
        for model, res in zip(models, results)
    }
