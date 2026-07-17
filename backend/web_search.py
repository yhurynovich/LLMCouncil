"""
web_search.py — Free web search via self-hosted SearXNG.
SearXNG runs as a sibling Docker container on the llm-council network.
Internal container-to-container communication always uses port 8080.
"""

import httpx

SEARXNG_URL = "http://searxng:8080/search"  # internal Docker network port, always 8080

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


def searxng_search(query: str, max_results: int = 5) -> str:
    """Query the local SearXNG instance and return plain-text results."""
    try:
        resp = httpx.get(
            SEARXNG_URL,
            params={
                "q": query,
                "format": "json",
                "language": "en",
                "time_range": "",
                "safesearch": "0",
            },
            timeout=15.0,
            headers={"User-Agent": "llm-council/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])[:max_results]
        if not results:
            return "No results found."

        hits = []
        for r in results:
            title   = r.get("title", "No title")
            url     = r.get("url", "")
            content = r.get("content", "")
            hits.append(f"**{title}**\n{content}\n{url}")

        return "\n\n---\n\n".join(hits)

    except Exception as exc:
        return f"Search failed: {exc}"


def handle_tool_call(tool_name: str, arguments: dict) -> str:
    if tool_name == "search_web":
        return searxng_search(arguments.get("query", ""))
    return f"Unknown tool: {tool_name}"