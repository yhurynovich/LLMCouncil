"""OpenRouter API client for making LLM requests."""

import asyncio
import json
import httpx
from typing import Any

from .config import OPENROUTER_API_KEY, OPENROUTER_API_URL
from .web_search import SEARCH_TOOL, handle_tool_call

STAGGER_DELAY = 0.5  # seconds between each model request


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

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://llm-council.local",
        "X-Title": "LLM Council",
    }

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    if enable_search:
        payload["tools"] = [SEARCH_TOOL]
        payload["tool_choice"] = "auto"

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            # ── First request ────────────────────────────────────────────────
            resp = await client.post(OPENROUTER_API_URL, headers=headers, json=payload)
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
                search_result = handle_tool_call(tool_name, tool_args)

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

                second_payload = {
                    "model": model,
                    "messages": second_messages,
                }
                resp2 = await client.post(
                    OPENROUTER_API_URL, headers=headers, json=second_payload
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
            print(f"Error querying {model}: {e}")
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
    return {
        model: (None if isinstance(res, Exception) else res)
        for model, res in zip(models, results)
    }
