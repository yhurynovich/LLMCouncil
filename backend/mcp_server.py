"""MCP Server for LLM Council.

This module implements a Model Context Protocol (MCP) server that exposes
the LLM Council's capabilities as MCP tools and resources.
"""

import json
import asyncio
from typing import Any, Dict, List, Optional

from mcp.server.lowlevel import Server
from mcp.server.models import InitializationOptions
from mcp.types import (
    Resource,
    Tool,
    TextContent,
    ImageContent,
    EmbeddedResource,
    LoggingLevel,
    ListResourcesRequest,
    ListResourcesResult,
    ListToolsRequest,
    ListToolsResult,
    CallToolRequest,
    CallToolResult,
    ReadResourceRequest,
    ReadResourceResult,
    TextResourceContents,
    Tool,
    Resource,
    TextContent,
)

import mcp.types as types

from .config import get_model_sets, get_active_model_set
from .providers import get_providers
from .council import run_full_council, calculate_aggregate_rankings
from .llm_client import query_model
from .storage import list_conversations_async, get_conversation_async, create_conversation_async


# Create the MCP server
server = Server("llm-council")


# ============================================================================
# Resource Handlers (defined first, before registration)
# ============================================================================

async def handle_list_resources(request: types.ListResourcesRequest) -> types.ListResourcesResult:
    """List available resources."""
    resources = []

    # Add model sets as resources
    model_sets = await get_model_sets()
    active_set = await get_active_model_set()
    for set_id, ms in model_sets.items():
        resources.append(
            Resource(
                uri=f"llm-council://model-sets/{set_id}",
                name=f"Model Set: {ms['label']}",
                description=ms.get("description", ""),
                mimeType="application/json",
            )
        )

    # Add providers as resources
    providers = await get_providers()
    for provider_name, provider in providers.items():
        resources.append(
            Resource(
                uri=f"llm-council://providers/{provider_name}",
                name=f"Provider: {provider_name}",
                description=provider.get("description", ""),
                mimeType="application/json",
            )
        )

    # Add conversations as resources
    conversations = await list_conversations_async()
    for conv in conversations:
        resources.append(
            Resource(
                uri=f"llm-council://conversations/{conv['id']}",
                name=f"Conversation: {conv['title']}",
                description=f"Created: {conv['created_at']}, Messages: {conv['message_count']}",
                mimeType="application/json",
            )
        )

    return types.ListResourcesResult(resources=resources)


async def handle_read_resource(request: types.ReadResourceRequest) -> types.ReadResourceResult:
    """Read a specific resource."""
    uri = request.params.uri

    if uri.startswith("llm-council://model-sets/"):
        set_id = uri.replace("llm-council://model-sets/", "")
        model_sets = await get_model_sets()
        if set_id in model_sets:
            ms = model_sets[set_id]
            content = json.dumps({
                "id": set_id,
                "label": ms["label"],
                "icon": ms.get("icon", ""),
                "description": ms.get("description", ""),
                "council": ms["council"],
                "chairman": ms["chairman"],
            }, indent=2)
            return types.ReadResourceResult(
                contents=[TextResourceContents(uri=uri, mimeType="application/json", text=content)]
            )
        raise ValueError(f"Model set not found: {set_id}")

    elif uri.startswith("llm-council://providers/"):
        provider_name = uri.replace("llm-council://providers/", "")
        providers = await get_providers()
        if provider_name in providers:
            provider = providers[provider_name].copy()
            # Mask sensitive info
            if "api_key" in provider:
                provider["api_key"] = "***"
            if "api_key_env" in provider:
                provider["api_key_env"] = "***"
            content = json.dumps(provider, indent=2)
            return types.ReadResourceResult(
                contents=[TextResourceContents(uri=uri, mimeType="application/json", text=content)]
            )
        raise ValueError(f"Provider not found: {provider_name}")

    elif uri.startswith("llm-council://conversations/"):
        conv_id = uri.replace("llm-council://conversations/", "")
        conversation = await get_conversation_async(conv_id)
        if conversation:
            content = json.dumps(conversation, indent=2)
            return types.ReadResourceResult(
                contents=[TextResourceContents(uri=uri, mimeType="application/json", text=content)]
            )
        raise ValueError(f"Conversation not found: {conv_id}")

    raise ValueError(f"Unknown resource URI: {uri}")


# ============================================================================
# Tool Handlers (defined before registration)
# ============================================================================

async def handle_list_tools(request: types.ListToolsRequest) -> types.ListToolsResult:
    """List available tools."""
    return types.ListToolsResult(tools=[
        Tool(
            name="run_council",
            description="Run the full 3-stage LLM Council deliberation on a question",
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question to ask the council",
                    },
                    "model_set": {
                        "type": "string",
                        "description": "Model set to use (optional, uses active set if not specified)",
                    },
                    "temperature": {
                        "type": "number",
                        "description": "Temperature for model responses (0.0-1.0)",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                    "max_tokens": {
                        "type": "integer",
                        "description": "Maximum tokens for responses",
                        "minimum": 1,
                    },
                    "quick": {
                        "type": "boolean",
                        "description": "Skip stages 2 and 3 for faster response",
                        "default": False,
                    },
                },
                "required": ["question"],
            },
        ),
        Tool(
            name="list_model_sets",
            description="List all available model sets",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="list_providers",
            description="List all configured providers",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_conversation",
            description="Get a conversation by ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "conversation_id": {
                        "type": "string",
                        "description": "UUID of the conversation",
                    },
                },
                "required": ["conversation_id"],
            },
        ),
        Tool(
            name="create_conversation",
            description="Create a new conversation",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="query_model",
            description="Query a specific model directly",
            inputSchema={
                "type": "object",
                "properties": {
                    "model": {
                        "type": "string",
                        "description": "Model identifier (e.g., 'openrouter/anthropic/claude-3.5-sonnet')",
                    },
                    "messages": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {"type": "string", "enum": ["user", "assistant", "system"]},
                                "content": {"type": "string"},
                            },
                            "required": ["role", "content"],
                        },
                    },
                    "temperature": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                    "max_tokens": {
                        "type": "integer",
                        "minimum": 1,
                    },
                },
                "required": ["model", "messages"],
            },
        ),
    ])


async def handle_call_tool(request: types.CallToolRequest) -> types.CallToolResult:
    """Handle tool calls."""
    try:
        name = request.params.name
        arguments = request.params.arguments or {}

        if name == "run_council":
            return await _run_council_tool(arguments)
        elif name == "list_model_sets":
            return await _list_model_sets_tool()
        elif name == "list_providers":
            return await _list_providers_tool()
        elif name == "get_conversation":
            return await _get_conversation_tool(arguments)
        elif name == "create_conversation":
            return await _create_conversation_tool()
        elif name == "query_model":
            return await _query_model_tool(arguments)
        else:
            raise ValueError(f"Unknown tool: {name}")
    except Exception as e:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Error: {str(e)}")],
            isError=True,
        )


async def _run_council_tool(arguments: Dict[str, Any]) -> types.CallToolResult:
    """Run the full council deliberation."""
    question = arguments.get("question", "")
    model_set = arguments.get("model_set")
    temperature = arguments.get("temperature", 0.7)
    max_tokens = arguments.get("max_tokens", 4096)
    quick = arguments.get("quick", False)

    if not question:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="Error: question is required")],
            isError=True,
        )

    # Prepare messages
    messages = [{"role": "user", "content": question}]

    try:
        stage1_results, stage2_results, stage3_result, metadata = await run_full_council(
            messages=messages,
            council_models=None,  # Will use model_set or active set
            temperature=temperature,
            max_tokens=max_tokens,
        )

        # Format the response
        result = {
            "question": question,
            "stage1": stage1_results,
            "stage2": stage2_results,
            "stage3": stage3_result,
            "metadata": metadata,
            "quick": quick,
        }

        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(result, indent=2))]
        )

    except Exception as e:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Error running council: {str(e)}")],
            isError=True,
        )


async def _list_model_sets_tool() -> types.CallToolResult:
    """List all model sets."""
    model_sets = await get_model_sets()
    active = await get_active_model_set()

    result = {
        "sets": model_sets,
        "active": active,
    }
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(result, indent=2))]
    )


async def _list_providers_tool() -> types.CallToolResult:
    """List all providers."""
    providers = await get_providers()

    # Mask sensitive info
    for provider in providers.values():
        if "api_key" in provider:
            provider["api_key"] = "***"
        if "api_key_env" in provider:
            provider["api_key_env"] = "***"

    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(providers, indent=2))]
    )


async def _get_conversation_tool(arguments: Dict[str, Any]) -> types.CallToolResult:
    """Get a conversation by ID."""
    conversation_id = arguments.get("conversation_id")
    if not conversation_id:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="Error: conversation_id is required")],
            isError=True,
        )

    conversation = await get_conversation_async(conversation_id)
    if not conversation:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Conversation not found: {conversation_id}")],
            isError=True,
        )

    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(conversation, indent=2))]
    )


async def _create_conversation_tool() -> types.CallToolResult:
    """Create a new conversation."""
    import uuid
    from .storage import create_conversation_async

    conversation_id = str(uuid.uuid4())
    conversation = await create_conversation_async(conversation_id)

    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(conversation, indent=2))]
    )


async def _query_model_tool(arguments: Dict[str, Any]) -> types.CallToolResult:
    """Query a specific model directly."""
    model = arguments.get("model")
    messages = arguments.get("messages", [])
    temperature = arguments.get("temperature", 0.7)
    max_tokens = arguments.get("max_tokens", 4096)

    if not model:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="Error: model is required")],
            isError=True,
        )
    if not messages:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="Error: messages are required")],
            isError=True,
        )

    try:
        result = await query_model(
            model=model,
            messages=messages,
            enable_search=False,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        if result is None:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="Error: Model returned no response")],
                isError=True,
            )

        content = result.get("content", "")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=content)]
        )

    except Exception as e:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Error querying model: {str(e)}")],
            isError=True,
        )


# ============================================================================
# Handler Registration (after all function definitions)
# ============================================================================

# Create the MCP server
server = Server("llm-council")

# Resource handlers
server.add_request_handler(
    "resources/list",
    types.ListResourcesRequest,
    handle_list_resources
)

server.add_request_handler(
    "resources/read",
    types.ReadResourceRequest,
    handle_read_resource
)

# Tool handlers
server.add_request_handler(
    "tools/list",
    types.ListToolsRequest,
    handle_list_tools
)

server.add_request_handler(
    "tools/call",
    types.CallToolRequest,
    handle_call_tool
)


async def run_mcp_server():
    """Run the MCP server."""
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="llm-council",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=None,
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(run_mcp_server())