"""Configuration for the LLM Council."""
import os
import json
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DATA_DIR = "data/conversations"

# Built-in default model sets (used if no persisted file exists)
DEFAULT_MODEL_SETS = {
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


def _load_model_sets():
    """Load model sets from persisted file, falling back to defaults."""
    if os.path.exists(MODEL_SETS_FILE):
        try:
            with open(MODEL_SETS_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_MODEL_SETS)


def _save_model_sets(sets: dict):
    """Persist model sets to disk."""
    parent = os.path.dirname(MODEL_SETS_FILE)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(MODEL_SETS_FILE, "w") as f:
        json.dump(sets, f, indent=2)


from .providers import PROVIDERS


def _ensure_provider_prefix(model_sets: dict) -> dict:
    """Auto-prefix model IDs with 'openrouter/' if no provider prefix present."""
    needs_save = False
    for set_id, ms in model_sets.items():
        for field in ("council", "chairman"):
            val = ms[field]
            if isinstance(val, list):
                new_val = []
                for m in val:
                    if "/" not in m or m.split("/")[0] not in PROVIDERS:
                        new_val.append(f"openrouter/{m}")
                        needs_save = True
                    else:
                        new_val.append(m)
                ms[field] = new_val
            elif isinstance(val, str) and val:
                if "/" not in val or val.split("/")[0] not in PROVIDERS:
                    ms[field] = f"openrouter/{val}"
                    needs_save = True
    if needs_save:
        _save_model_sets(model_sets)
    return model_sets


MODEL_SETS = _ensure_provider_prefix(_load_model_sets())

ACTIVE_MODEL_SET = "free"

BUILTIN_SET_IDS = {"free", "smart", "reasonable", "privacy"}


def get_active_set():
    return MODEL_SETS[ACTIVE_MODEL_SET]

def get_council_models():
    return get_active_set()["council"]

def get_chairman_model():
    return get_active_set()["chairman"]

COUNCIL_MODELS = MODEL_SETS["free"]["council"]
CHAIRMAN_MODEL = MODEL_SETS["free"]["chairman"]
