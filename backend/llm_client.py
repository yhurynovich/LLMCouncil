"""Generic LLM client — routes queries to the correct provider."""
import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any
from urllib.parse import urlparse

import ipaddress

from .providers import get_provider, get_provider_api_key
from .web_search import SEARCH_TOOL, handle_tool_call
from .http_client import get_shared_client, create_shared_client, _is_ip_allowed, PRIVATE_IP_RANGES

logger = logging.getLogger(__name__)

STAGGER_DELAY = 0.5


def _should_verify_ssl(base_url: str) -> bool:
    """Check if SSL verification should be enabled for the given URL.
    
    Disable SSL verification for HTTP URLs to private/allowed IPs to avoid
    SSL errors when connecting to local HTTP endpoints.
    """
    try:
        parsed = urlparse(base_url)
        if parsed.scheme != "http":
            return True  # Verify SSL for HTTPS
        host = parsed.hostname or ""
        if not host:
            return True
        
        # Allow explicitly whitelisted subnets
        if _is_ip_allowed(host):
            return False
        
        # Check if host is a private IP
        try:
            ip = ipaddress.ip_address(host)
            for private_range in PRIVATE_IP_RANGES:
                if ip in private_range:
                    return False
        except ValueError:
            # Not an IP address (could be hostname), verify SSL
            pass
    except Exception:
        pass
    return True


def _get_proxy_url() -> str | None:
    """Resolve proxy URL from env, preferring HTTP/HTTPS proxy."""
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        val = os.environ.get(var, "").strip()
        if not val:
            continue
        if not val.startswith(("http://", "https://")):
            continue
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
    session_id: str = None,
    **kwargs,
) -> dict[str, Any] | None:
    provider_name, model_id = _parse_model_id(model)
    provider = await get_provider(provider_name)
    if provider is None:
        logger.error("Unknown provider '%s' for model '%s'", provider_name, model)
        return None

    # Extract timeout from kwargs, default to 120.0
    timeout = kwargs.get("timeout", 120.0)

    api_key = get_provider_api_key(provider)
    base_url = provider["base_url"]

    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    if provider_name == "openrouter":
        headers["HTTP-Referer"] = "https://llm-council.local"
        headers["X-Title"] = "LLM Council"
        # Client-driven session tracking headers (forwarded to OpenRouter for observability)
        if session_id:
            headers["X-Session-ID"] = session_id
            headers["X-Conversation-ID"] = session_id
        # Request ID for distributed tracing
        headers["X-Request-ID"] = str(uuid.uuid4())

    api_model = model_id
    payload: dict[str, Any] = {
        "model": api_model,
        "messages": messages,
    }

    # Add session_id if provider supports sessions and we have one
    if provider.get("session_support") and session_id:
        session_param = provider.get("session_param", "session_id")
        payload[session_param] = session_id

    if enable_search and provider_name == "openrouter":
        payload["tools"] = [SEARCH_TOOL]
        payload["tool_choice"] = "auto"

    # Pass temperature and max_tokens if provided
    if "temperature" in kwargs:
        payload["temperature"] = kwargs["temperature"]
    if "max_tokens" in kwargs:
        payload["max_tokens"] = kwargs["max_tokens"]

    # Determine SSL verification based on URL (disable for HTTP to private IPs)
    verify_ssl = _should_verify_ssl(base_url)
    
    # Use shared HTTP client for connection pooling (with appropriate SSL verification)
    client = get_shared_client(verify_ssl=verify_ssl)
    if client is None:
        raise RuntimeError("Shared HTTP client not initialized. Ensure the FastAPI lifespan has started.")
    
    try:
        t0 = time.monotonic()
        resp = await client.post(base_url, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()

        choices = data.get("choices")
        if not choices:
            logger.error("API returned no choices for model %s", model)
            return {"error": "No choices in response"}
        msg = choices[0].get("message", {})
        if not isinstance(msg, dict):
            return {"error": "Invalid message field in response"}

        # Handle tool calls (OpenRouter only)
        if enable_search and provider_name == "openrouter" and msg.get("tool_calls"):
            tool_results = []
            for tc in msg["tool_calls"]:
                func = tc.get("function", {})
                tool_name = func.get("name")
                raw_args = func.get("arguments")
                if not tool_name or not isinstance(raw_args, str):
                    continue
                try:
                    tool_args = json.loads(raw_args)
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning("Failed to parse tool args for %s: %s", model, e)
                    return {"error": f"Invalid tool call arguments: {e}"}

                logger.info("[%s] search_web(%s)", model, tool_args.get('query', ''))
                search_result = await handle_tool_call(tool_name, tool_args)
                tool_results.append({
                    "tool_call_id": tc["id"],
                    "name": tool_name,
                    "content": search_result,
                })

            second_messages = list(messages) + [
                {"role": "assistant", "content": None, "tool_calls": msg["tool_calls"]},
                *[{"role": "tool", **tr} for tr in tool_results],
            ]
            second_payload = {"model": api_model, "messages": second_messages}
            # Also include session_id in tool callback if supported
            if provider.get("session_support") and session_id:
                session_param = provider.get("session_param", "session_id")
                second_payload[session_param] = session_id
            
            # New request ID for the second call
            second_headers = dict(headers)
            second_headers["X-Request-ID"] = str(uuid.uuid4())
            resp2 = await client.post(base_url, headers=second_headers, json=second_payload, timeout=timeout)
            resp2.raise_for_status()
            data2 = resp2.json()
            choices2 = data2.get("choices")
            if not choices2:
                return {"error": "No choices in tool-callback response"}
            msg = choices2[0].get("message", {})
            if not isinstance(msg, dict):
                return {"error": "Invalid message in tool-callback response"}

        elapsed = round(time.monotonic() - t0, 2)
        content = msg.get("content") or ""
        if not content.strip():
            logger.warning("Empty response content from %s", model)
            return {"error": "Model returned empty response"}
        result: dict[str, Any] = {"content": content, "response_time": elapsed}
        if "reasoning_details" in msg:
            result["reasoning_details"] = msg["reasoning_details"]
        return result

    except Exception as e:
        # Sanitize error message to remove API keys
        import re
        error_msg = str(e)
        error_msg = re.sub(r'(Bearer\s+)\S+', r'\1[REDACTED]', error_msg)
        error_msg = re.sub(r'sk-[a-zA-Z0-9]{20,}', '[REDACTED]', error_msg)
        logger.error("Error querying %s: %s", model, error_msg)
        return {"error": error_msg}


async def _staggered_query(model, messages, enable_search, delay, session_id=None, **kwargs):
    if delay > 0:
        await asyncio.sleep(delay)
    return await query_model(model, messages, enable_search=enable_search, session_id=session_id, **kwargs)


async def query_models_parallel(
    models: list[str],
    messages: list,
    enable_search: bool = True,
    session_ids: dict[str, str] = None,
    **kwargs,
) -> dict[str, Any]:
    if session_ids is None:
        session_ids = {}
    tasks = [
        _staggered_query(model, messages, enable_search, i * STAGGER_DELAY, session_ids.get(model), **kwargs)
        for i, model in enumerate(models)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return {
        model: (res if not isinstance(res, Exception) else {"error": str(res)})
        for model, res in zip(models, results)
    }