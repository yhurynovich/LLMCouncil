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
            "openai/gpt-oss-120b:free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "google/gemma-4-31b-it:free",
            "deepseek/deepseek-v4-flash:free",
        ],
        "chairman": "openai/gpt-oss-120b:free",
    },
    "smart": {
        "label": "Smartest",
        "icon": "SMART",
        "description": "Best available models. Requires OpenRouter credits.",
        "council": [
            "openai/gpt-4o",
            "anthropic/claude-sonnet-4-5",
            "google/gemini-2.5-flash",
            "x-ai/grok-3-mini",
        ],
        "chairman": "anthropic/claude-sonnet-4-5",
    },
    "reasonable": {
        "label": "Reasonable",
        "icon": "OK",
        "description": "Good balance of quality and cost.",
        "council": [
            "openai/gpt-4o-mini",
            "anthropic/claude-haiku-4-5",
            "google/gemini-2.5-flash",
            "meta-llama/llama-3.3-70b-instruct",
        ],
        "chairman": "openai/gpt-4o-mini",
    },
    "privacy": {
        "label": "Privacy First",
        "icon": "PRIV",
        "description": "EU-based or privacy-focused providers. No US Big Tech.",
        "council": [
            "mistralai/mistral-large",
            "mistralai/mistral-small",
            "qwen/qwen-2.5-72b-instruct",
            "deepseek/deepseek-chat",
        ],
        "chairman": "mistralai/mistral-large",
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


MODEL_SETS = _load_model_sets()

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
