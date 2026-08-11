"""MCP Server for LLM Council.

Exposes the LLM Council's capabilities (running a council deliberation,
inspecting model sets/providers, managing conversations, querying a model
directly) as MCP tools and resources.

Two transports are supported, selected via the MCP_TRANSPORT env var:
  - "stdio" (default): for local use, e.g. a desktop MCP client that
    launches this module as a subprocess.
  - "http": Streamable HTTP transport for remote/networked use. This is
    the transport a browser-based or containerized MCP client talks to
    over plain HTTP(S) — see run_http_server() below.
"""

import asyncio
import json
import os
from typing import Any, Dict, List, Optional

import anyio
from mcp.server.fastmcp import Context, FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import get_active_model_set, get_model_sets
from .council import run_full_council, stage1_collect_responses
from .http_client import close_shared_client, create_shared_client
from .llm_client import _get_proxy_url, query_model
from .providers import get_providers
from .storage import create_conversation_async, get_conversation_async
from .web_search import close_searxng_client

# ============================================================================
# Server setup
# ============================================================================

MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", "8002"))

mcp_app = FastMCP(
    "llm-council",
    instructions=(
        "Tools for running and inspecting an LLM Council multi-model "
        "deliberation: run_council queries a set of models in parallel, "
        "has them rank each other's answers, then has a chairman model "
        "synthesize a final response."
    ),
    host=MCP_HOST,
    port=MCP_PORT,
    # Every request is handled independently (no server-side session
    # tied to an Mcp-Session-Id header). Simpler to run behind a reverse
    # proxy and matches this deployment's single-instance backend.
    stateless_http=True,
)


# ============================================================================
# Tools
# ============================================================================

@mcp_app.tool()
async def run_council(
    question: str,
    model_set: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    quick: bool = False,
    ctx: Optional[Context] = None,
) -> str:
    """Run the LLM Council on a question.

    Sends the question to every model in the council in parallel (Stage 1),
    has each model rank the others' anonymized responses (Stage 2), then
    has the chairman model synthesize a final answer from the discussion
    (Stage 3). This can take a while since it involves several sequential
    rounds of model calls — progress is reported as each stage completes.

    Args:
        question: The question to ask the council.
        model_set: Which configured model set to use (see list_model_sets
            for available IDs). Defaults to the currently active set.
        temperature: Sampling temperature (0.0-1.0).
        max_tokens: Maximum tokens per model response.
        quick: If true, skip ranking and synthesis and return only the raw
            Stage 1 responses (faster, no chairman synthesis).
    """
    if not question:
        raise ValueError("question is required")

    model_sets = await get_model_sets()
    set_id = model_set if model_set in model_sets else await get_active_model_set()
    council_models = model_sets[set_id]["council"]
    messages = [{"role": "user", "content": question}]

    async def _heartbeat():
        """Keep the connection alive with progress pings during long tool calls."""
        elapsed = 0
        while True:
            await asyncio.sleep(15)
            elapsed += 15
            if ctx is not None:
                await ctx.report_progress(progress=elapsed, message="Council still deliberating...")

    heartbeat_task = asyncio.create_task(_heartbeat())
    try:
        if quick:
            stage1_results, _session_ids = await stage1_collect_responses(
                messages,
                council_models=council_models,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if ctx is not None:
                await ctx.report_progress(progress=1, total=1, message="Stage 1 complete")
            result = {
                "question": question,
                "model_set": set_id,
                "stage1": stage1_results,
                "quick": True,
            }
        else:
            stage1_results, stage2_results, stage3_result, metadata = await run_full_council(
                messages=messages,
                council_models=council_models,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if ctx is not None:
                await ctx.report_progress(progress=1, total=1, message="Council deliberation complete")
            result = {
                "question": question,
                "model_set": set_id,
                "stage1": stage1_results,
                "stage2": stage2_results,
                "stage3": stage3_result,
                "metadata": metadata,
                "quick": False,
            }
    finally:
        heartbeat_task.cancel()

    return json.dumps(result, indent=2)


@mcp_app.tool()
async def list_model_sets() -> str:
    """List all configured model sets and which one is currently active."""
    model_sets = await get_model_sets()
    active = await get_active_model_set()
    return json.dumps({"sets": model_sets, "active": active}, indent=2)


@mcp_app.tool()
async def list_providers() -> str:
    """List all configured LLM providers (API keys are masked)."""
    providers = await get_providers()
    masked = {}
    for name, provider in providers.items():
        p = dict(provider)
        if "api_key" in p:
            p["api_key"] = "***"
        if "api_key_env" in p:
            p["api_key_env"] = "***"
        masked[name] = p
    return json.dumps(masked, indent=2)


@mcp_app.tool()
async def get_conversation(conversation_id: str) -> str:
    """Get a stored conversation by its ID.

    Args:
        conversation_id: UUID of the conversation.
    """
    conversation = await get_conversation_async(conversation_id)
    if not conversation:
        raise ValueError(f"Conversation not found: {conversation_id}")
    return json.dumps(conversation, indent=2)


@mcp_app.tool()
async def create_conversation() -> str:
    """Create a new, empty conversation and return its ID."""
    import uuid

    conversation_id = str(uuid.uuid4())
    conversation = await create_conversation_async(conversation_id)
    return json.dumps(conversation, indent=2)


@mcp_app.tool()
async def query_model_tool(
    model: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> str:
    """Query a single model directly, bypassing the council process.

    Args:
        model: Model identifier, e.g. "openrouter/anthropic/claude-3.5-sonnet".
        messages: Chat messages, each with "role" and "content".
        temperature: Sampling temperature (0.0-1.0).
        max_tokens: Maximum tokens in the response.
    """
    if not messages:
        raise ValueError("messages is required")

    result = await query_model(
        model=model,
        messages=messages,
        enable_search=False,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if result is None:
        raise ValueError(f"Model '{model}' returned no response (unknown model or provider error)")
    return result.get("content", "")


# ============================================================================
# Resources
#
# Model sets, providers, and conversations are exposed as resource
# templates rather than a flat enumerated list, since the set of
# conversations grows over time. Discover valid IDs via the list_* tools
# above, then read the corresponding resource URI.
# ============================================================================

@mcp_app.resource("llm-council://model-sets/{set_id}")
async def model_set_resource(set_id: str) -> str:
    """A single model set's configuration (label, council models, chairman model)."""
    model_sets = await get_model_sets()
    if set_id not in model_sets:
        raise ValueError(f"Model set not found: {set_id}")
    ms = model_sets[set_id]
    return json.dumps(
        {
            "id": set_id,
            "label": ms["label"],
            "icon": ms.get("icon", ""),
            "description": ms.get("description", ""),
            "council": ms["council"],
            "chairman": ms["chairman"],
        },
        indent=2,
    )


@mcp_app.resource("llm-council://providers/{provider_name}")
async def provider_resource(provider_name: str) -> str:
    """A single provider's configuration (API key masked)."""
    providers = await get_providers()
    if provider_name not in providers:
        raise ValueError(f"Provider not found: {provider_name}")
    provider = dict(providers[provider_name])
    if "api_key" in provider:
        provider["api_key"] = "***"
    if "api_key_env" in provider:
        provider["api_key_env"] = "***"
    return json.dumps(provider, indent=2)


@mcp_app.resource("llm-council://conversations/{conversation_id}")
async def conversation_resource(conversation_id: str) -> str:
    """A single stored conversation, including its full message history."""
    conversation = await get_conversation_async(conversation_id)
    if not conversation:
        raise ValueError(f"Conversation not found: {conversation_id}")
    return json.dumps(conversation, indent=2)


# ============================================================================
# HTTP extras (health check) and server runners
# ============================================================================

@mcp_app.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "llm-council-mcp"})


async def _serve(run_async) -> None:
    """Set up shared outbound-HTTP resources once, run the server until it
    exits, then tear them down.

    This process is separate from backend.main's FastAPI app, so it needs
    its own copy of the shared httpx client — query_model() fails without
    one. Created once here at process startup (not per-request) so
    connections are actually pooled and reused.
    """
    create_shared_client(
        timeout=120.0,
        proxy=_get_proxy_url(),
        max_keepalive_connections=20,
        max_connections=100,
    )
    try:
        await run_async()
    finally:
        await close_shared_client()
        await close_searxng_client()


def run_stdio_server() -> None:
    """Run with stdio transport (for a local MCP client that spawns this process)."""
    anyio.run(lambda: _serve(mcp_app.run_stdio_async))


def run_http_server() -> None:
    """Run with Streamable HTTP transport (for remote/networked MCP clients).

    Serves the MCP endpoint at http://<host>:<port>/mcp and a plain health
    check at http://<host>:<port>/health.
    """
    anyio.run(lambda: _serve(mcp_app.run_streamable_http_async))


if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio").lower()
    if transport in ("http", "streamable-http", "streamable_http"):
        print(f"Starting MCP server with Streamable HTTP transport on {MCP_HOST}:{MCP_PORT} (path: /mcp)")
        run_http_server()
    else:
        print("Starting MCP server with stdio transport")
        run_stdio_server()
