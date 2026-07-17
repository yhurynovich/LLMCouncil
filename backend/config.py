"""Configuration for the LLM Council."""
import os
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DATA_DIR = "data/conversations"

MODEL_SETS = {
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

ACTIVE_MODEL_SET = "free"

def get_active_set():
    return MODEL_SETS[ACTIVE_MODEL_SET]

def get_council_models():
    return get_active_set()["council"]

def get_chairman_model():
    return get_active_set()["chairman"]

COUNCIL_MODELS = MODEL_SETS["free"]["council"]
CHAIRMAN_MODEL = MODEL_SETS["free"]["chairman"]
