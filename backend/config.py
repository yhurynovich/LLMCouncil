"""Configuration for the LLM Council."""
import asyncio
import logging
import os
import json
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DATA_DIR = "data/conversations"

# Built-in default model sets (used if no persisted file exists)
DEFAULT_MODEL_SETS = {
    "search": {
        "label": "Internet search",
        "icon": "WWW",
        "description": "Models with web search capabilities.",
        "council": [
            "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
            "deepseek-free/deepseek-reasoner-search",
            "qwen-free/qwen3.7-plus",
            "openrouter/google/gemma-4-31b-it:free",
            "glmkimi-free/glm-5-deepresearch",
        ],
        "chairman": "qwen-free/qwen3.7-max",
    },
    "free": {
        "label": "Free Tier",
        "icon": "FREE",
        "description": "100% free models on OpenRouter. May be rate-limited.",
        "council": [
            "openrouter/openai/gpt-oss-120b:free",
            "openrouter/meta-llama/llama-3.3-70b-instruct:free",
            "openrouter/google/gemma-4-31b-it:free",
            "openrouter/deepseek/deepseek-v4-flash:free",
        ],
        "chairman": "openrouter/openai/gpt-oss-120b:free",
    },
    "smart": {
        "label": "Smartest",
        "icon": "SMART",
        "description": "Best available models. Requires OpenRouter credits.",
        "council": [
            "openrouter/openai/gpt-4o",
            "openrouter/anthropic/claude-sonnet-4-5",
            "openrouter/google/gemini-2.5-flash",
            "openrouter/x-ai/grok-3-mini",
        ],
        "chairman": "openrouter/anthropic/claude-sonnet-4-5",
    },
    "reasonable": {
        "label": "Reasonable",
        "icon": "OK",
        "description": "Good balance of quality and cost.",
        "council": [
            "openrouter/openai/gpt-4o-mini",
            "openrouter/anthropic/claude-haiku-4-5",
            "openrouter/google/gemini-2.5-flash",
            "openrouter/meta-llama/llama-3.3-70b-instruct",
        ],
        "chairman": "openrouter/openai/gpt-4o-mini",
    },
    "privacy": {
        "label": "Privacy First",
        "icon": "PRIV",
        "description": "EU-based or privacy-focused providers. No US Big Tech.",
        "council": [
            "openrouter/mistralai/mistral-large",
            "openrouter/mistralai/mistral-small",
            "openrouter/qwen/qwen-2.5-72b-instruct",
            "openrouter/deepseek/deepseek-chat",
        ],
        "chairman": "openrouter/mistralai/mistral-large",
    },
}

MODEL_SETS_FILE = "data/model_sets.json"
ACTIVE_MODEL_SET_FILE = "data/active_model_set.json"

# Locks for thread-safe access to mutable global state
_model_sets_lock = asyncio.Lock()
_active_set_lock = asyncio.Lock()

# Cached state
_model_sets: Optional[Dict[str, Any]] = None
_active_model_set: Optional[str] = None


def _get_providers() -> Dict[str, Any]:
    """Lazy import to avoid circular dependency with providers.py."""
    from .providers import PROVIDERS
    return PROVIDERS


async def _load_model_sets_async() -> Dict[str, Any]:
    """Load model sets from persisted file, falling back to defaults."""
    try:
        if os.path.exists(MODEL_SETS_FILE):
            import aiofiles
            async with aiofiles.open(MODEL_SETS_FILE, "r") as f:
                content = await f.read()
                return json.loads(content)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Error loading model_sets: %s", e)
    return dict(DEFAULT_MODEL_SETS)


async def _save_model_sets_async(sets: Dict[str, Any]) -> None:
    """Persist model sets to disk atomically using async I/O with fallback for filesystems that don't support os.replace."""
    parent = os.path.dirname(MODEL_SETS_FILE)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = MODEL_SETS_FILE + ".tmp"
    import aiofiles
    async with aiofiles.open(tmp, "w") as f:
        await f.write(json.dumps(sets, indent=2))
    try:
        os.replace(tmp, MODEL_SETS_FILE)
    except OSError:
        import shutil
        shutil.move(tmp, MODEL_SETS_FILE)


async def get_model_sets() -> Dict[str, Any]:
    """Get model sets with lazy initialization and thread-safe access."""
    global _model_sets
    if _model_sets is None:
        async with _model_sets_lock:
            if _model_sets is None:
                _model_sets = await _load_model_sets_async()
    return _model_sets


async def reload_model_sets() -> Dict[str, Any]:
    """Force reload model sets from disk."""
    global _model_sets
    async with _model_sets_lock:
        _model_sets = await _load_model_sets_async()
    return _model_sets


async def set_model_sets(sets: Dict[str, Any]) -> Dict[str, Any]:
    """Update model sets atomically."""
    global _model_sets
    async with _model_sets_lock:
        await _save_model_sets_async(sets)
        _model_sets = sets
    return _model_sets


async def _load_active_model_set_async() -> str:
    """Load active model set from disk."""
    try:
        if os.path.exists(ACTIVE_MODEL_SET_FILE):
            import aiofiles
            async with aiofiles.open(ACTIVE_MODEL_SET_FILE, "r") as f:
                content = await f.read()
                data = json.loads(content)
                model_sets = await get_model_sets()
                if data.get("set_id") in model_sets:
                    return data["set_id"]
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Cannot read active_model_set: %s", e)
    return "free"


async def _save_active_model_set_async(set_id: str) -> None:
    """Persist active model set to disk atomically with fallback for filesystems that don't support os.replace."""
    parent = os.path.dirname(ACTIVE_MODEL_SET_FILE)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = ACTIVE_MODEL_SET_FILE + ".tmp"
    import aiofiles
    async with aiofiles.open(tmp, "w") as f:
        await f.write(json.dumps({"set_id": set_id}))
    try:
        os.replace(tmp, ACTIVE_MODEL_SET_FILE)
    except OSError:
        # Fallback for filesystems that don't support atomic replace (e.g., some network mounts)
        import shutil
        shutil.move(tmp, ACTIVE_MODEL_SET_FILE)


async def get_active_model_set() -> str:
    """Get active model set with lazy initialization."""
    global _active_model_set
    if _active_model_set is None:
        async with _active_set_lock:
            if _active_model_set is None:
                _active_model_set = await _load_active_model_set_async()
    return _active_model_set


async def set_active_model_set(set_id: str) -> str:
    """Set active model set atomically."""
    global _active_model_set
    model_sets = await get_model_sets()
    if set_id not in model_sets:
        raise ValueError(f"Unknown model set: {set_id}")
    async with _active_set_lock:
        await _save_active_model_set_async(set_id)
        _active_model_set = set_id
    return _active_model_set


BUILTIN_SET_IDS = {"free", "smart", "reasonable", "privacy"}


async def get_active_set() -> Dict[str, Any]:
    """Get the currently active model set configuration."""
    active_id = await get_active_model_set()
    model_sets = await get_model_sets()
    return model_sets[active_id]


async def get_council_models() -> List[str]:
    """Get the council models for the active set."""
    active = await get_active_set()
    return active["council"]


async def get_chairman_model() -> str:
    """Get the chairman model for the active set."""
    active = await get_active_set()
    return active["chairman"]


# Backwards compatibility - synchronous wrappers (for non-async contexts)
def _load_model_sets_sync() -> Dict[str, Any]:
    """Synchronous version for backwards compatibility."""
    if os.path.exists(MODEL_SETS_FILE):
        try:
            with open(MODEL_SETS_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Corrupt model_sets file: %s", e)
    return dict(DEFAULT_MODEL_SETS)


def _save_model_sets_sync(sets: Dict[str, Any]) -> None:
    """Synchronous version for backwards compatibility."""
    parent = os.path.dirname(MODEL_SETS_FILE)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = MODEL_SETS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(sets, f, indent=2)
    os.replace(tmp, MODEL_SETS_FILE)


# Module-level cached values for sync access (initialized on first access)
_MODEL_SETS_SYNC: Optional[Dict[str, Any]] = None
_ACTIVE_MODEL_SET_SYNC: Optional[str] = None


def _get_model_sets_sync() -> Dict[str, Any]:
    """Get model sets synchronously (for backwards compatibility)."""
    global _MODEL_SETS_SYNC
    if _MODEL_SETS_SYNC is None:
        _MODEL_SETS_SYNC = _load_model_sets_sync()
    return _MODEL_SETS_SYNC


def _get_active_model_set_sync() -> str:
    """Get active model set synchronously (for backwards compatibility)."""
    global _ACTIVE_MODEL_SET_SYNC
    if _ACTIVE_MODEL_SET_SYNC is None:
        try:
            if os.path.exists(ACTIVE_MODEL_SET_FILE):
                with open(ACTIVE_MODEL_SET_FILE, "r") as f:
                    data = json.load(f)
                    if data.get("set_id") in _get_model_sets_sync():
                        _ACTIVE_MODEL_SET_SYNC = data["set_id"]
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Cannot read active_model_set: %s", e)
    if _ACTIVE_MODEL_SET_SYNC is None:
        _ACTIVE_MODEL_SET_SYNC = "free"
    return _ACTIVE_MODEL_SET_SYNC


# For backwards compatibility with existing code
def get_active_set_sync() -> Dict[str, Any]:
    """Synchronous getter for active set."""
    return _get_model_sets_sync()[_get_active_model_set_sync()]


def get_council_models_sync() -> List[str]:
    """Synchronous getter for council models."""
    return get_active_set_sync()["council"]


def get_chairman_model_sync() -> str:
    """Synchronous getter for chairman model."""
    return get_active_set_sync()["chairman"]