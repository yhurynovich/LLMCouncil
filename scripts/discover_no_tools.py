#!/usr/bin/env python3
"""
Automated discovery of models that don't support function calling (tools).

Usage:
    python scripts/discover_no_tools.py          # Test all models, print results
    python scripts/discover_no_tools.py --update # Update .env with discovered models
    python scripts/discover_no_tools.py --check  # Quick check specific models
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Set

import httpx

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.llm_client import _parse_model_id
from backend.providers import get_provider, get_provider_api_key, get_providers
from backend.config import OPENROUTER_API_KEY

# Simple test tool for probing
TEST_TOOL = {
    "type": "function",
    "function": {
        "name": "test_tool",
        "description": "Test tool for probing function calling support",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"]
        }
    }
}

MODEL_SETS_FILE = Path("data/model_sets.json")
ENV_FILE = Path(".env")


def get_all_models() -> Set[str]:
    """Get all unique models from model_sets.json."""
    if not MODEL_SETS_FILE.exists():
        return set()

    with open(MODEL_SETS_FILE) as f:
        data = json.load(f)

    models = set()
    for set_data in data.values():
        models.update(set_data.get("council", []))
        if chairman := set_data.get("chairman"):
            models.add(chairman)
    return models


async def test_model_tools(model: str, provider_name: str, base_url: str, api_key: str) -> tuple[bool, bool]:
    """
    Test if a model supports function calling.
    Returns (baseline_works, tools_actually_used)
    """
    _, model_id = _parse_model_id(model)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if provider_name == "openrouter":
        headers["HTTP-Referer"] = "https://llm-council.local"
        headers["X-Title"] = "LLM Council"

    payload_without_tools = {
        "model": model_id,
        "messages": [{"role": "user", "content": "test"}],
        "max_tokens": 10,
    }

    payload_with_tools = {
        **payload_without_tools,
        "tools": [TEST_TOOL],
        "tool_choice": "auto",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Baseline test
            resp_base = await client.post(base_url, headers=headers, json=payload_without_tools)
            if resp_base.status_code != 200:
                print(f"  {model}: Baseline failed ({resp_base.status_code}) - skipping")
                return (False, False)

            # Test with tools
            resp_tools = await client.post(base_url, headers=headers, json=payload_with_tools)
            if resp_tools.status_code != 200:
                print(f"  {model}: Tools rejected ({resp_tools.status_code}) - no tool support")
                return (True, False)

            # Check if tool was actually called
            data = resp_tools.json()
            tool_called = False
            if "choices" in data and data["choices"]:
                msg = data["choices"][0].get("message", {})
                if msg.get("tool_calls"):
                    tool_called = True

            return (True, tool_called)
    except Exception as e:
        print(f"  {model}: Error - {e}")
        return (True, True)  # Assume supported on error


async def discover_no_tools(models: Set[str] = None, provider_name: str = "openrouter") -> Set[str]:
    """Discover models that don't support function calling."""
    if models is None:
        models = get_all_models()

    providers = await get_providers()
    provider = providers.get(provider_name)
    if not provider:
        print(f"Provider '{provider_name}' not found")
        return set()

    base_url = provider["base_url"]
    api_key = get_provider_api_key(provider)

    print(f"Testing {len(models)} models against {provider_name} ({base_url})...")

    no_tools = set()
    for model in sorted(models):
        if not model.startswith(f"{provider_name}/"):
            continue

        baseline_works, tools_used = await test_model_tools(model, provider_name, base_url, api_key)
        if not baseline_works:
            continue
        
        if tools_used:
            status = "✓ supports tools (actually uses them)"
        else:
            status = "✗ NO tools support (tools accepted but ignored)"
            _, model_id = _parse_model_id(model)
            no_tools.add(model_id)
        
        print(f"  {model}: {status}")

    return no_tools


def update_env_file(no_tools: Set[str]):
    """Update .env with discovered MODELS_NO_TOOLS."""
    if not ENV_FILE.exists():
        print(".env file not found")
        return

    with open(ENV_FILE) as f:
        lines = f.readlines()

    # Find or create MODELS_NO_TOOLS line
    new_lines = []
    found = False
    for line in lines:
        if line.startswith("MODELS_NO_TOOLS="):
            new_lines.append(f"MODELS_NO_TOOLS={','.join(sorted(no_tools))}\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        # Add after the Models without function calling section
        for i, line in enumerate(new_lines):
            if "Models without function calling" in line:
                new_lines.insert(i + 1, f"MODELS_NO_TOOLS={','.join(sorted(no_tools))}\n")
                break

    with open(ENV_FILE, "w") as f:
        f.writelines(new_lines)

    print(f"Updated .env with {len(no_tools)} models")


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Discover models without function calling support")
    parser.add_argument("--update", action="store_true", help="Update .env with discovered models")
    parser.add_argument("--provider", default="openrouter", help="Provider to test")
    parser.add_argument("--models", nargs="+", help="Specific models to test")
    args = parser.parse_args()

    if args.models:
        models = set(args.models)
    else:
        models = get_all_models()

    no_tools = await discover_no_tools(models, args.provider)

    print(f"\nDiscovered {len(no_tools)} models without tools support:")
    for model in sorted(no_tools):
        print(f"  {model}")

    if args.update:
        update_env_file(no_tools)
    elif no_tools:
        print("\nRun with --update to update .env")


if __name__ == "__main__":
    asyncio.run(main())