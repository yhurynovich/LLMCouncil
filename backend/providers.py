"""Provider configuration for multi-provider LLM routing."""
import os
import json
from typing import Any
from dotenv import load_dotenv

load_dotenv()

PROVIDERS_FILE = "data/providers.json"

DEFAULT_PROVIDERS = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1/chat/completions",
        "api_key_env": "OPENROUTER_API_KEY",
        "stream": True,
        "description": "OpenRouter marketplace",
    },
}


def _load_providers() -> dict[str, dict[str, Any]]:
    if os.path.exists(PROVIDERS_FILE):
        try:
            with open(PROVIDERS_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_PROVIDERS)


def _save_providers(providers: dict):
    parent = os.path.dirname(PROVIDERS_FILE)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(PROVIDERS_FILE, "w") as f:
        json.dump(providers, f, indent=2)


PROVIDERS = _load_providers()


def get_provider(name: str) -> dict[str, Any] | None:
    return PROVIDERS.get(name)


def get_provider_api_key(provider: dict) -> str:
    if "api_key_env" in provider:
        return os.getenv(provider["api_key_env"], "")
    return provider.get("api_key", "")


def list_providers() -> dict[str, dict]:
    return dict(PROVIDERS)
