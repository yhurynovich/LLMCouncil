"""
web_search.py — Free web search via self-hosted SearXNG.
SearXNG runs as a sibling Docker container on the llm-council network.
Internal container-to-container communication always uses port 8080.
"""

import os
import httpx

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080/search")

# Shared client for connection pooling
_searxng_client: httpx.AsyncClient | None = None
_searxng_client_lock = __import__("threading").Lock()


def _get_searxng_client() -> httpx.AsyncClient:
    """Get or create shared SearXNG client with connection pooling."""
    global _searxng_client
    with _searxng_client_lock:
        if _searxng_client is None:
            _searxng_client = httpx.AsyncClient(
                timeout=15.0,
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
                trust_env=False,
            )
        return _searxng_client


async def close_searxng_client() -> None:
    """Close the shared SearXNG client."""
    global _searxng_client
    with _searxng_client_lock:
        if _searxng_client is not None:
            import asyncio
            asyncio.create_task(_searxng_client.aclose())
            _searxng_client = None

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_web",
        "description": (
            "Perform a live web search and return current results. "
            "Use this whenever the question requires current, real-time, "
            "or up-to-date information."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to look up on the web"
                }
            },
            "required": ["query"]
        }
    }
}


async def searxng_search(query: str, max_results: int = 5) -> str:
    """Query the local SearXNG instance and return plain-text results."""
    try:
        client = _get_searxng_client()
        resp = await client.get(
            SEARXNG_URL,
            params={
                "q": query,
                "format": "json",
                "language": "en",
                "time_range": "",
                "safesearch": "0",
            },
            headers={"User-Agent": "llm-council/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])[:max_results]
        if not results:
            return "No results found."

        hits = []
        for r in results:
            title = r.get("title", "No title")
            url = r.get("url", "")
            content = r.get("content", "")
            hits.append(f"**{title}**\n{content}\n{url}")

        return "\n\n---\n\n".join(hits)

    except Exception as exc:
        return f"Search failed: {exc}"


async def handle_tool_call(tool_name: str, arguments: dict) -> str:
    """Handle tool calls from LLMs (async version)."""
    if tool_name == "search_web":
        return await searxng_search(arguments.get("query", ""))
    return f"Unknown tool: {tool_name}"