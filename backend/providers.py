"""Provider configuration for multi-provider LLM routing."""
import asyncio
import ipaddress
import os
import json
from typing import Any, Dict
from urllib.parse import urlparse
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


# Private IP ranges to block for SSRF protection
PRIVATE_IP_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_private_ip(url: str) -> bool:
    """Check if a URL resolves to a private/internal IP address."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        
        # Allow localhost/127.0.0.1 in development but log warning
        if host in ("localhost", "127.0.0.1", "::1"):
            return True
            
        # Try to resolve to IP
        import socket
        try:
            ips = socket.getaddrinfo(host, None)
            for ip_info in ips:
                ip_str = ip_info[4][0]
                ip = ipaddress.ip_address(ip_str)
                for private_range in PRIVATE_IP_RANGES:
                    if ip in private_range:
                        return True
        except socket.gaierror:
            # If we can't resolve, allow it (might be a domain that resolves later)
            pass
        return False
    except Exception:
        return True  # On error, be safe and block


async def _load_providers_async() -> Dict[str, Dict[str, Any]]:
    """Load providers from file asynchronously."""
    try:
        import aiofiles
        if os.path.exists(PROVIDERS_FILE):
            async with aiofiles.open(PROVIDERS_FILE, "r") as f:
                content = await f.read()
                return json.loads(content)
    except (json.JSONDecodeError, OSError) as e:
        pass
    return dict(DEFAULT_PROVIDERS)


async def _save_providers_async(providers: Dict[str, Dict[str, Any]]) -> None:
    """Save providers to file asynchronously."""
    parent = os.path.dirname(PROVIDERS_FILE)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = PROVIDERS_FILE + ".tmp"
    import aiofiles
    async with aiofiles.open(tmp, "w") as f:
        await f.write(json.dumps(providers, indent=2))
    os.replace(tmp, PROVIDERS_FILE)


# In-memory cache with lazy initialization
_providers_cache: Dict[str, Dict[str, Any]] = {}
_providers_lock = asyncio.Lock()
_providers_loaded = False


# Synchronous versions for backwards compatibility
def _load_providers_sync() -> Dict[str, Dict[str, Any]]:
    if os.path.exists(PROVIDERS_FILE):
        try:
            with open(PROVIDERS_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_PROVIDERS)


def _save_providers_sync(providers: Dict[str, Dict[str, Any]]) -> None:
    parent = os.path.dirname(PROVIDERS_FILE)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = PROVIDERS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(providers, f, indent=2)
    os.replace(tmp, PROVIDERS_FILE)


# For backwards compatibility - synchronous access to providers
# This will be populated on first async access or can be loaded manually
PROVIDERS = _load_providers_sync()


async def get_providers() -> Dict[str, Dict[str, Any]]:
    """Get providers with lazy loading."""
    global _providers_cache, _providers_loaded, PROVIDERS
    if not _providers_loaded:
        async with _providers_lock:
            if not _providers_loaded:
                _providers_cache = await _load_providers_async()
                PROVIDERS = _providers_cache
                _providers_loaded = True
    return _providers_cache


async def get_provider(name: str) -> Dict[str, Any] | None:
    """Get a specific provider by name."""
    providers = await get_providers()
    return providers.get(name)


def get_provider_api_key(provider: Dict[str, Any]) -> str:
    """Get API key for a provider (never returns the key if it's from env)."""
    if "api_key_env" in provider:
        return os.getenv(provider["api_key_env"], "")
    return provider.get("api_key", "")


def list_providers() -> Dict[str, Dict]:
    """Return all configured providers with API keys masked."""
    # Synchronous version for backwards compatibility
    providers = _load_providers_sync()
    masked = {}
    for name, provider in providers.items():
        masked[name] = {
            "base_url": provider.get("base_url", ""),
            "api_key_env": provider.get("api_key_env", ""),
            "stream": provider.get("stream", True),
            "description": provider.get("description", ""),
            "api_key_set": bool(provider.get("api_key") or os.getenv(provider.get("api_key_env", ""), "")),
        }
    return masked


async def list_providers_async() -> Dict[str, Dict]:
    """Return all configured providers with API keys masked (async version)."""
    providers = await get_providers()
    masked = {}
    for name, provider in providers.items():
        masked[name] = {
            "base_url": provider.get("base_url", ""),
            "api_key_env": provider.get("api_key_env", ""),
            "stream": provider.get("stream", True),
            "description": provider.get("description", ""),
            "api_key_set": bool(provider.get("api_key") or os.getenv(provider.get("api_key_env", ""), "")),
        }
    return masked


async def create_provider(name: str, provider_data: Dict[str, Any]) -> None:
    """Create a new provider."""
    # Validate base_url for SSRF protection
    base_url = provider_data.get("base_url", "")
    if base_url and _is_private_ip(base_url):
        raise ValueError(f"Provider base_url '{base_url}' points to a private/internal IP address. This is not allowed for security reasons.")
    
    global PROVIDERS
    providers = await get_providers()
    if name in providers:
        raise ValueError(f"Provider '{name}' already exists")
    providers[name] = provider_data
    await _save_providers_async(providers)
    PROVIDERS = providers


async def update_provider(name: str, updates: Dict[str, Any]) -> None:
    """Update an existing provider."""
    # Validate base_url for SSRF protection
    base_url = updates.get("base_url", "")
    if base_url and _is_private_ip(base_url):
        raise ValueError(f"Provider base_url '{base_url}' points to a private/internal IP address. This is not allowed for security reasons.")
    
    global PROVIDERS
    providers = await get_providers()
    if name not in providers:
        raise ValueError(f"Provider '{name}' not found")
    providers[name].update(updates)
    await _save_providers_async(providers)
    PROVIDERS = providers


async def delete_provider(name: str) -> None:
    """Delete a provider."""
    global PROVIDERS
    providers = await get_providers()
    if name not in providers:
        raise ValueError(f"Provider '{name}' not found")
    if name == "openrouter":
        raise ValueError("Cannot delete built-in 'openrouter' provider")
    del providers[name]
    await _save_providers_async(providers)
    PROVIDERS = providers


# Synchronous versions for backwards compatibility
def _load_providers_sync() -> Dict[str, Dict[str, Any]]:
    if os.path.exists(PROVIDERS_FILE):
        try:
            with open(PROVIDERS_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_PROVIDERS)


def _save_providers_sync(providers: Dict[str, Dict[str, Any]]) -> None:
    parent = os.path.dirname(PROVIDERS_FILE)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = PROVIDERS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(providers, f, indent=2)
    os.replace(tmp, PROVIDERS_FILE)