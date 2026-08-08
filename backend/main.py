"""FastAPI backend for LLM Council."""

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, UploadFile, File, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import uuid
import json
import asyncio
import traceback
import re
import time
from collections import defaultdict

# UUID v4 regex for validation
UUID_V4_REGEX = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$', re.IGNORECASE)

# Simple in-memory rate limiter for auth endpoints
_login_attempts: Dict[str, List[float]] = defaultdict(list)
_login_lock = asyncio.Lock()
MAX_LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300  # 5 minutes

async def check_rate_limit(client_ip: str) -> bool:
    """Check if client has exceeded login attempt rate limit."""
    async with _login_lock:
        now = time.time()
        # Clean old attempts
        _login_attempts[client_ip] = [t for t in _login_attempts[client_ip] if now - t < LOGIN_WINDOW_SECONDS]
        if len(_login_attempts[client_ip]) >= MAX_LOGIN_ATTEMPTS:
            return False
        _login_attempts[client_ip].append(now)
        return True

def _is_valid_uuid(uuid_str: str) -> bool:
    """Check if string is a valid UUID v4."""
    return bool(UUID_V4_REGEX.match(uuid_str))


def sanitize_error_message(error_msg: str) -> str:
    """Sanitize error messages to remove sensitive information like API keys."""
    if not error_msg:
        return ""
    # Remove Bearer tokens
    error_msg = re.sub(r'(Bearer\s+)[^\s]+', r'\1[REDACTED]', error_msg)
    # Remove API keys in URLs (basic pattern)
    error_msg = re.sub(r'(api[_-]?key[=:]["\']?)[^"\'\s&]+', r'\1[REDACTED]', error_msg, flags=re.IGNORECASE)
    # Remove potential sk- prefixed keys
    error_msg = re.sub(r'sk-[a-zA-Z0-9]{20,}', '[REDACTED]', error_msg)
    return error_msg


async def _fetch_provider_models(provider_name: str, provider: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Fetch models from a single provider.
    Returns list of model dicts with id, name, provider, pricing, context_length.
    """
    api_key = prov.get_provider_api_key(provider)
    if not api_key and provider_name != "openrouter":
        return []

    try:
        base = provider["base_url"]
        if base.endswith("/chat/completions"):
            models_url = base.replace("/chat/completions", "/models")
        elif base.endswith("/v1/chat/completions"):
            models_url = base.replace("/v1/chat/completions", "/v1/models")
        else:
            models_url = base.rstrip("/") + "/models"

        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # Use shared client with SSRF protection
        client = get_shared_client()
        if client is None:
            print(f"Shared HTTP client not initialized for {provider_name}")
            raise RuntimeError("Shared HTTP client not initialized")

        resp = await client.get(models_url, headers=headers, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            models = []
            for m in data.get("data", []):
                model_id = m.get("id", "")
                if model_id:
                    models.append({
                        "id": f"{provider_name}/{model_id}",
                        "name": m.get("name", model_id),
                        "provider": provider_name,
                        "pricing": m.get("pricing", {}),
                        "context_length": m.get("context_length"),
                    })
            return models
        else:
            # Fallback: use configured model if models endpoint fails
            model_id = provider.get("model", "")
            if model_id:
                return [{
                    "id": f"{provider_name}/{model_id}",
                    "name": model_id,
                    "provider": provider_name,
                    "pricing": {},
                    "context_length": None,
                }]
            return []
    except Exception as e:
        print(f"Error fetching models from {provider_name}: {sanitize_error_message(str(e))}")
        # Fallback: use configured model
        model_id = provider.get("model", "")
        if model_id:
            return [{
                "id": f"{provider_name}/{model_id}",
                "name": model_id,
                "provider": provider_name,
                "pricing": {},
                "context_length": None,
            }]
        return []


# Simple in-memory rate limiter for auth endpoints
from . import config as cfg
from . import providers as prov
from . import uploads
from .llm_client import _get_proxy_url
from .http_client import create_shared_client, close_shared_client, get_shared_client
from .council import (
    run_full_council,
    generate_conversation_title,
    stage1_collect_responses,
    stage2_collect_rankings,
    stage3_synthesize_final,
    calculate_aggregate_rankings,
)
from .metrics import get_metrics_summary
from .feedback import add_feedback, get_feedback_for_conversation
from .reliability import update_from_feedback
from . import storage
from .web_search import close_searxng_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for shared resources."""
    # Startup: create shared HTTP client with connection pooling
    create_shared_client(
        timeout=120.0,
        proxy=_get_proxy_url(),
        max_keepalive_connections=20,
        max_connections=100,
    )
    yield
    # Shutdown: close shared HTTP client
    await close_shared_client()
    # Shutdown: close SearXNG client
    await close_searxng_client()


app = FastAPI(
    title="LLM Council API",
    description="A multi-model LLM council system with OpenAI-compatible endpoints for Hermes integration.",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# Restrict CORS to specific origins in production
import os
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000,http://192.168.31.66:5173,http://192.168.31.66:5174").split(",")

# allow_credentials=False is correct here because auth uses Basic Auth header (not cookies).
# The frontend sends credentials via Authorization: Basic <base64> header, not cookies.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)



# ── Pydantic models ───────────────────────────────────────────────────────────

class CreateConversationRequest(BaseModel):
    pass

class FileAttachment(BaseModel):
    file_id: str
    filename: str
    type: str  # "text" or "image"
    ext: str

class SendMessageRequest(BaseModel):
    content: str
    model_set: Optional[str] = None  # if provided, overrides active set for this request
    quick: bool = False  # skip Stage 2 & 3, return Stage 1 only
    files: List[FileAttachment] = []
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None

class SetModelSetRequest(BaseModel):
    set_id: str

class CreateModelSetRequest(BaseModel):
    set_id: str
    label: str
    icon: str = ""
    description: str = ""
    council: List[str] = []
    chairman: str = ""

class UpdateModelSetRequest(BaseModel):
    label: Optional[str] = None
    icon: Optional[str] = None
    description: Optional[str] = None
    council: Optional[List[str]] = None
    chairman: Optional[str] = None

class ConversationMetadata(BaseModel):
    id: str
    created_at: str
    title: str
    message_count: int

class Conversation(BaseModel):
    id: str
    created_at: str
    title: str
    messages: List[Dict[str, Any]]

class OpenAIChatMessage(BaseModel):
    role: str
    content: str

class OpenAIChatCompletionsRequest(BaseModel):
    model: str
    messages: List[OpenAIChatMessage]
    stream: bool = False

class CreateProviderRequest(BaseModel):
    name: str
    base_url: str
    api_key: str = ""
    api_key_env: str = ""
    stream: bool = False
    description: str = ""

class UpdateProviderRequest(BaseModel):
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    api_key_env: Optional[str] = None
    stream: Optional[bool] = None
    description: Optional[str] = None


class ClaimCorrection(BaseModel):
    claim: str
    correction: str


class FeedbackRequest(BaseModel):
    rating: str  # "up" or "down"
    claim_corrections: Optional[List[ClaimCorrection]] = None
    user_id: Optional[str] = None

class RenameConversationRequest(BaseModel):
    title: str


# ── OpenAI-compatible models ─────────────────────────────────────────────────

class OpenAIMessage(BaseModel):
    role: str
    content: str

class OpenAIChatCompletionRequest(BaseModel):
    model: str
    messages: List[OpenAIMessage]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: bool = False

class OpenAIChoice(BaseModel):
    index: int
    message: Dict[str, str]
    finish_reason: str

class OpenAIUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

class OpenAIChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[OpenAIChoice]
    usage: OpenAIUsage

class OpenAIModel(BaseModel):
    id: str
    object: str = "model"
    created: int
    owned_by: str

class OpenAIModelList(BaseModel):
    object: str = "list"
    data: List[OpenAIModel]


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "ok", "service": "LLM Council API"}


# ── Model Sets ────────────────────────────────────────────────────────────────

@app.get("/api/model-sets", tags=["Model Sets"])
async def list_model_sets():
    """Return all model sets and the currently active one."""
    model_sets = await cfg.get_model_sets()
    active_set = await cfg.get_active_model_set()
    sets = {}
    for key, val in model_sets.items():
        sets[key] = {
            "label": val["label"],
            "icon": val["icon"],
            "description": val["description"],
            "council": val["council"],
            "chairman": val["chairman"],
        }
    return {"sets": sets, "active": active_set}


@app.post("/api/model-sets/active", tags=["Model Sets"])
async def set_active_model_set(request: SetModelSetRequest):
    """Switch the active model set."""
    model_sets = await cfg.get_model_sets()
    if request.set_id not in model_sets:
        raise HTTPException(status_code=400, detail=f"Unknown model set: {request.set_id}")
    await cfg.set_active_model_set(request.set_id)
    active = model_sets[request.set_id]
    return {
        "active": request.set_id,
        "label": active["label"],
        "council": active["council"],
        "chairman": active["chairman"],
    }


@app.post("/api/model-sets", tags=["Model Sets"])
async def create_model_set(request: CreateModelSetRequest):
    """Create a new model set."""
    set_id = request.set_id.strip().lower().replace(" ", "-")
    if not set_id:
        raise HTTPException(status_code=400, detail="set_id is required")
    
    # Validate council models
    if not request.council or not isinstance(request.council, list):
        raise HTTPException(status_code=400, detail="council must be a non-empty list of model IDs")
    if len(request.council) == 0:
        raise HTTPException(status_code=400, detail="council must contain at least one model")
    
    # Validate chairman
    if not request.chairman:
        raise HTTPException(status_code=400, detail="chairman is required")
    
    # Validate all model IDs exist in providers
    model_sets = await cfg.get_model_sets()
    providers = await prov.get_providers()
    all_model_ids = set()
    for p in providers.values():
        if "model" in p:
            all_model_ids.add(p["model"])
    
    for model in request.council:
        if model not in all_model_ids:
            raise HTTPException(status_code=400, detail=f"Invalid model ID in council: {model}")
    if request.chairman not in all_model_ids:
        raise HTTPException(status_code=400, detail=f"Invalid chairman model ID: {request.chairman}")
    
    if set_id in model_sets:
        raise HTTPException(status_code=409, detail=f"Model set '{set_id}' already exists")
    
    new_set = {
        "label": request.label,
        "icon": request.icon or request.label[:4].upper(),
        "description": request.description,
        "council": request.council,
        "chairman": request.chairman,
    }
    model_sets[set_id] = new_set
    await cfg.set_model_sets(model_sets)
    return {"ok": True, "set_id": set_id}


@app.put("/api/model-sets/{set_id}", tags=["Model Sets"])
async def update_model_set(set_id: str, request: UpdateModelSetRequest):
    """Update an existing model set."""
    model_sets = await cfg.get_model_sets()
    if set_id not in model_sets:
        raise HTTPException(status_code=404, detail=f"Model set '{set_id}' not found")

    ms = model_sets[set_id]
    
    # Validate council if provided
    if request.council is not None:
        if not isinstance(request.council, list) or len(request.council) == 0:
            raise HTTPException(status_code=400, detail="council must be a non-empty list")
        providers = await prov.get_providers()
        all_model_ids = set()
        for p in providers.values():
            if "model" in p:
                all_model_ids.add(p["model"])
        for model in request.council:
            if model not in all_model_ids:
                raise HTTPException(status_code=400, detail=f"Invalid model ID in council: {model}")
        ms["council"] = request.council
    
    if request.chairman is not None:
        if not request.chairman:
            raise HTTPException(status_code=400, detail="chairman cannot be empty")
        providers = await prov.get_providers()
        all_model_ids = set()
        for p in providers.values():
            if "model" in p:
                all_model_ids.add(p["model"])
        if request.chairman not in all_model_ids:
            raise HTTPException(status_code=400, detail=f"Invalid chairman model ID: {request.chairman}")
        ms["chairman"] = request.chairman
    
    if request.label is not None:
        ms["label"] = request.label
    if request.icon is not None:
        ms["icon"] = request.icon
    if request.description is not None:
        ms["description"] = request.description

    await cfg.set_model_sets(model_sets)
    return {"ok": True, "set_id": set_id}


@app.delete("/api/model-sets/{set_id}", tags=["Model Sets"])
async def delete_model_set(set_id: str):
    """Delete a model set. Built-in sets cannot be deleted."""
    model_sets = await cfg.get_model_sets()
    if set_id not in model_sets:
        raise HTTPException(status_code=404, detail=f"Model set '{set_id}' not found")
    if set_id in cfg.BUILTIN_SET_IDS:
        raise HTTPException(status_code=400, detail=f"Cannot delete built-in set '{set_id}'")

    del model_sets[set_id]
    active = await cfg.get_active_model_set()
    if active == set_id:
        # Fall back to first available model set instead of hardcoded "free"
        remaining_sets = list(model_sets.keys())
        if remaining_sets:
            await cfg.set_active_model_set(remaining_sets[0])
        else:
            # This should not happen since built-in sets cannot be deleted
            await cfg.set_active_model_set("free")
    await cfg.set_model_sets(model_sets)
    return {"ok": True}


# ── Metrics & Observability ───────────────────────────────────────────────────

@app.get("/api/metrics/summary", tags=["Metrics"])
async def metrics_summary():
    """Return aggregated metrics summary for all stages and models."""
    return get_metrics_summary()


# ── Feedback ──────────────────────────────────────────────────────────────────

@app.post("/api/conversations/{conversation_id}/messages/{message_index}/feedback", tags=["Feedback"])
async def submit_feedback(
    conversation_id: str,
    message_index: int,
    request: FeedbackRequest,
):
    """Submit feedback for a council response.
    
    Args:
        conversation_id: UUID of the conversation
        message_index: Index of the assistant message (0-based)
        request: FeedbackRequest with rating and optional claim corrections
    
    Returns:
        The created feedback entry
    """
    try:
        entry = add_feedback(
            conversation_id=conversation_id,
            message_index=message_index,
            rating=request.rating,
            claim_corrections=[c.model_dump() for c in request.claim_corrections] if request.claim_corrections else None,
            user_id=request.user_id,
        )
        # Update model reliability from feedback
        try:
            conversation = await storage.get_conversation_async(conversation_id)
            if conversation and message_index < len(conversation.get("messages", [])):
                message = conversation["messages"][message_index]
                if message.get("metadata") and message["metadata"].get("aggregate_rankings"):
                    for ranking in message["metadata"]["aggregate_rankings"]:
                        model = ranking.get("model")
                        if model:
                            update_from_feedback(
                                model=model,
                                rating=request.rating,
                                claim_corrections=[c.model_dump() for c in request.claim_corrections] if request.claim_corrections else [],
                            )
        except Exception as e:
            # Log but don't fail the feedback submission
            print(f"Warning: Failed to update reliability from feedback: {e}")
        
        return {"ok": True, "feedback": entry}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save feedback: {e}")


@app.get("/api/conversations/{conversation_id}/feedback", tags=["Feedback"])
async def get_conversation_feedback(conversation_id: str):
    """Get all feedback for a conversation."""
    feedback = get_feedback_for_conversation(conversation_id)
    return {"conversation_id": conversation_id, "feedback": feedback}


# ── Providers ──────────────────────────────────────────────────────────────

@app.get("/api/providers", tags=["Providers"])
async def list_providers():
    """Return all configured providers with masked API keys."""
    return {"providers": await prov.list_providers_async()}


@app.post("/api/providers", tags=["Providers"])
async def create_provider(request: CreateProviderRequest):
    """Add a new provider."""
    name = request.name.strip().lower().replace(" ", "-")
    if not name:
        raise HTTPException(status_code=400, detail="Provider name is required")
    providers = await prov.get_providers()
    if name in providers:
        raise HTTPException(status_code=409, detail=f"Provider '{name}' already exists")

    await prov.create_provider(name, {
        "base_url": request.base_url,
        "api_key": request.api_key,
        "api_key_env": request.api_key_env,
        "stream": request.stream,
        "description": request.description,
    })
    return {"ok": True, "name": name}


@app.put("/api/providers/{name}", tags=["Providers"])
async def update_provider(name: str, request: UpdateProviderRequest):
    """Update an existing provider."""
    providers = await prov.get_providers()
    if name not in providers:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")

    updates = {}
    if request.base_url is not None:
        updates["base_url"] = request.base_url
    if request.api_key is not None:
        updates["api_key"] = request.api_key
    if request.api_key_env is not None:
        updates["api_key_env"] = request.api_key_env
    if request.stream is not None:
        updates["stream"] = request.stream
    if request.description is not None:
        updates["description"] = request.description

    await prov.update_provider(name, updates)
    return {"ok": True, "name": name}


@app.delete("/api/providers/{name}", tags=["Providers"])
async def delete_provider(name: str):
    """Delete a provider. Cannot delete 'openrouter'."""
    providers = await prov.get_providers()
    if name not in providers:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    if name == "openrouter":
        raise HTTPException(status_code=400, detail="Cannot delete built-in 'openrouter' provider")

    await prov.delete_provider(name)
    return {"ok": True}


@app.get("/api/available-models", tags=["Models"])
async def list_available_models():
    """Fetch available models from all providers."""
    all_models = []

    for provider_name, provider in prov.PROVIDERS.items():
        models = await _fetch_provider_models(provider_name, provider)
        all_models.extend(models)

    # Build a lookup of individual model context_lengths
    model_ctx = {m["id"]: m["context_length"] for m in all_models if m["context_length"] is not None}

    # Add model sets as selectable virtual models
    model_sets = await cfg.get_model_sets()
    for set_id, ms in model_sets.items():
        set_model_ids = ms["council"] + [ms["chairman"]]
        ctx_lengths = [model_ctx[mid] for mid in set_model_ids if mid in model_ctx]
        all_models.append({
            "id": f"set/{set_id}",
            "name": f"{ms['label']} ({len(ms['council'])} models)",
            "provider": "set",
            "pricing": {},
            "context_length": min(ctx_lengths) if ctx_lengths else None,
        })

    return {"models": all_models}


# ── OpenAI-compatible endpoints ──────────────────────────────────────────────

@app.get("/v1/models", response_model=OpenAIModelList, tags=["OpenAI Compatible"])
async def openai_list_models():
    """List available models in OpenAI-compatible format."""
    import time
    
    models = []
    current_time = int(time.time())
    
    # Add model sets as models
    model_sets = await cfg.get_model_sets()
    for set_id, ms in model_sets.items():
        models.append(OpenAIModel(
            id=f"set/{set_id}",
            object="model",
            created=current_time,
            owned_by="llm-council"
        ))
    
    # Add individual models from providers
    for provider_name, provider in prov.PROVIDERS.items():
        provider_models = await _fetch_provider_models(provider_name, provider)
        current_time = int(time.time())
        for m in provider_models:
            models.append(OpenAIModel(
                id=m["id"],
                object="model",
                created=current_time,
                owned_by=provider_name
            ))
    
    return OpenAIModelList(object="list", data=models)


@app.post("/v1/chat/completions", tags=["OpenAI Compatible"])
async def openai_chat_completions(request: OpenAIChatCompletionRequest, x_session_id: str | None = Header(default=None)):
    """Create a chat completion using the LLM Council in OpenAI-compatible format.

    Supports both stream=false (single JSON response) and stream=true
    (SSE). The council itself has no token-level streaming — the whole
    answer is sent as one delta chunk once the full 3-stage run finishes —
    but this satisfies OpenAI-compatible clients (like Hermes) that always
    request stream=true regardless of what a provider config says.
    """
    import time
    import uuid

    # Extract ALL messages (not just the first user message) to preserve conversation history
    if not request.messages:
        raise HTTPException(status_code=400, detail="No messages provided")

    # Resolve model set
    set_id = request.model
    if set_id.startswith("set/"):
        set_id = set_id[4:]  # Remove "set/" prefix

    model_sets = await cfg.get_model_sets()
    if set_id not in model_sets:
        raise HTTPException(status_code=400, detail=f"Unknown model set: {request.model}")

    model_set = model_sets[set_id]
    council_models = model_set["council"]
    chairman_model = model_set["chairman"]

    async def run_council() -> str:
        # Convert Pydantic models to dicts for JSON serialization
        msgs = [msg.model_dump() for msg in request.messages]
        # Use X-Session-ID as conversation_id for MODEL SESSION TRACKING ONLY.
        # This is used for model session continuity (e.g., local models that maintain state).
        # It does NOT grant access to conversation data or storage operations.
        # The session ID is only passed to get_or_create_model_session_async for model session tracking.
        conversation_id = x_session_id if x_session_id and _is_valid_uuid(x_session_id) else None
        stage1_results, session_ids = await stage1_collect_responses(
            msgs, council_models,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            conversation_id=conversation_id
        )

        if not stage1_results or all(r.get("response") is None for r in stage1_results):
            return "All models failed to respond. Please try again."

        responding_models = [r["model"] for r in stage1_results if r.get("response") is not None]
        stage2_results, label_to_model, ranking_session_ids = await stage2_collect_rankings(
            msgs, stage1_results, responding_models,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            conversation_id=conversation_id,
            session_ids=session_ids
        )
        all_session_ids = {**session_ids, **ranking_session_ids}
        stage3_result = await stage3_synthesize_final(
            msgs, stage1_results, stage2_results, chairman_model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            conversation_id=conversation_id,
            session_ids=all_session_ids
        )
        return stage3_result.get("response", "") if stage3_result else ""

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    if request.stream:
        async def event_stream():
            try:
                final_response = await run_council()
            except Exception as e:
                print(f"[OPENAI] Error: {sanitize_error_message(str(e))}\n{traceback.format_exc()}", flush=True)
                final_response = f"Error: council run failed ({sanitize_error_message(str(e))})"

            content_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": request.model,
                "choices": [
                    {"index": 0, "delta": {"role": "assistant", "content": final_response}, "finish_reason": None}
                ],
            }
            yield f"data: {json.dumps(content_chunk)}\n\n"

            stop_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": request.model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(stop_chunk)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # Non-streaming path
    try:
        final_response = await run_council()
    except HTTPException:
        raise
    except Exception as e:
        print(f"[OPENAI] Error: {sanitize_error_message(str(e))}\n{traceback.format_exc()}", flush=True)
        raise HTTPException(status_code=500, detail="Internal server error")

    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": request.model,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": final_response}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


# ── File Uploads ────────────────────────────────────────────────────────────

@app.post("/api/upload", tags=["Files"])
async def upload_file(file: UploadFile = File(...)):
    """Upload a file for chat attachment."""
    try:
        result = await uploads.save_upload(file)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Conversations ─────────────────────────────────────────────────────────────

@app.get("/api/conversations", response_model=List[ConversationMetadata], tags=["Conversations"])
async def list_conversations():
    return await storage.list_conversations_async()


@app.post("/api/conversations", response_model=Conversation, tags=["Conversations"])
async def create_conversation(request: CreateConversationRequest):
    conversation_id = str(uuid.uuid4())
    conversation = await storage.create_conversation_async(conversation_id)
    return conversation


@app.get("/api/conversations/{conversation_id}", response_model=Conversation, tags=["Conversations"])
async def get_conversation(conversation_id: str):
    conversation = await storage.get_conversation_async(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.put("/api/conversations/{conversation_id}", tags=["Conversations"])
async def rename_conversation(conversation_id: str, request: RenameConversationRequest):
    """Rename a conversation."""
    conversation = await storage.get_conversation_async(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await storage.update_conversation_title_async(conversation_id, request.title)
    return {"ok": True, "title": request.title}


@app.delete("/api/conversations/{conversation_id}", tags=["Conversations"])
async def delete_conversation(conversation_id: str):
    if not await storage.delete_conversation_async(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


async def _with_heartbeat(coro, interval: float = 15.0):
    """Run `coro` in the background, yielding an SSE comment line every
    `interval` seconds while it's still working, then yielding the
    completed asyncio.Task as the final item.

    Stage 1/2/3 can legitimately take well over a minute (multiple LLM
    calls, retries, slow/rate-limited free models) during which the
    endpoint below sends zero bytes. Any HTTP proxy that idle-times-out a
    connection (nginx's proxy_read_timeout, Synology's Reverse Proxy
    "Proxy read timeout", corporate proxies, etc.) will kill a silent
    connection like that well before it kills a connection that's still
    receiving bytes. Emitting a small `: keep-alive` comment periodically
    keeps every hop convinced the connection is alive, independent of how
    any particular proxy in front of us is configured.

    SSE comment lines (starting with ':') are part of the spec and are
    already ignored by the frontend's parser (it only reacts to lines
    starting with 'data:'), so this is invisible to the UI.

    Usage:
        result = None
        async for item in _with_heartbeat(some_coro(...)):
            if isinstance(item, asyncio.Task):
                result = item.result()
            else:
                yield item  # forward the heartbeat comment to the client
    """
    task = asyncio.create_task(coro)
    while not task.done():
        done, _ = await asyncio.wait({task}, timeout=interval)
        if not done:
            yield ": keep-alive\n\n"
    yield task


@app.post("/api/conversations/{conversation_id}/message/stream", tags=["Conversations"])
async def send_message_stream(conversation_id: str, request: SendMessageRequest):
    conversation = await storage.get_conversation_async(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    is_first_message = len(conversation["messages"]) == 0

    # Resolve which model set to use for this request
    set_id = request.model_set if request.model_set else await cfg.get_active_model_set()
    model_sets = await cfg.get_model_sets()
    if set_id not in model_sets:
        set_id = await cfg.get_active_model_set()
    model_set = model_sets[set_id]
    council_models = model_set["council"]
    chairman_model = model_set["chairman"]

    # Build full message history including the new user message
    messages = conversation["messages"] + [{"role": "user", "content": request.content}]

    async def event_generator():
        stage1_results = []
        stage2_results = []
        stage3_result = {}
        try:
            await storage.add_user_message_async(conversation_id, request.content)

            # Emit which model set is being used
            yield f"data: {json.dumps({'type': 'model_set', 'data': {'set_id': set_id, 'label': model_set['label'], 'council': council_models, 'chairman': chairman_model}})}\n\n"

            title_task = None
            if is_first_message:
                title_task = asyncio.create_task(
                    generate_conversation_title(request.content)
                )

            # Stage 1
            print(f"[STREAM] Stage 1 starting — set={set_id}, models={council_models}, quick={request.quick}, files={len(request.files)}", flush=True)
            yield f"data: {json.dumps({'type': 'stage1_start'})}\n\n"
            async for item in _with_heartbeat(
                stage1_collect_responses(messages, council_models, files=request.files, conversation_id=conversation_id)
            ):
                if isinstance(item, asyncio.Task):
                    stage1_results, session_ids = item.result()
                else:
                    yield item
            print(f"[STREAM] Stage 1 complete: {len(stage1_results)} responses", flush=True)
            yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results})}\n\n"

            if not request.quick:
                # Stage 2
                print(f"[STREAM] Stage 2 starting", flush=True)
                yield f"data: {json.dumps({'type': 'stage2_start'})}\n\n"
                responding_models = [r["model"] for r in stage1_results if r.get("response") is not None]

                # If all models failed, skip to error
                if not responding_models:
                    print(f"[STREAM] All models failed, skipping stages 2-3", flush=True)
                    yield f"data: {json.dumps({'type': 'stage2_complete', 'data': [], 'metadata': {'label_to_model': {}, 'aggregate_rankings': {}}})}\n\n"
                    yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
                    stage3_result = {"model": "error", "response": "All models failed to respond. Please try again."}
                    yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result})}\n\n"
                    yield f"data: {json.dumps({'type': 'complete'})}\n\n"
                    return

                async for item in _with_heartbeat(
                    stage2_collect_rankings(
                        messages, stage1_results, responding_models,
                        temperature=request.temperature,
                        max_tokens=request.max_tokens,
                        conversation_id=conversation_id,
                        session_ids=session_ids
                    )
                ):
                    if isinstance(item, asyncio.Task):
                        stage2_results, label_to_model, ranking_session_ids = item.result()
                    else:
                        yield item
                aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
                print(f"[STREAM] Stage 2 complete", flush=True)
                yield f"data: {json.dumps({'type': 'stage2_complete', 'data': stage2_results, 'metadata': {'label_to_model': label_to_model, 'aggregate_rankings': aggregate_rankings}})}\n\n"

                # Stage 3
                print(f"[STREAM] Stage 3 starting", flush=True)
                yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
                # Merge session_ids for chairman
                all_session_ids = {**session_ids, **ranking_session_ids}
                async for item in _with_heartbeat(
                    stage3_synthesize_final(
                        messages, stage1_results, stage2_results, chairman_model,
                        temperature=request.temperature,
                        max_tokens=request.max_tokens,
                        conversation_id=conversation_id,
                        session_ids=all_session_ids
                    )
                ):
                    if isinstance(item, asyncio.Task):
                        stage3_result = item.result()
                    else:
                        yield item
                print(f"[STREAM] Stage 3 complete", flush=True)
                yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result})}\n\n"

            # Title
            if title_task:
                title = await title_task
                await storage.update_conversation_title_async(conversation_id, title)
                yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"

            # Save
            print(f"[STREAM] Saving to storage", flush=True)
            metadata = {}
            if label_to_model:
                metadata["label_to_model"] = label_to_model
            if aggregate_rankings:
                metadata["aggregate_rankings"] = aggregate_rankings
            await storage.add_assistant_message_async(
                conversation_id, stage1_results, stage2_results, stage3_result, metadata
            )
            print(f"[STREAM] Saved successfully", flush=True)

            yield f"data: {json.dumps({'type': 'complete'})}\n\n"

        except Exception as e:
            full_traceback = traceback.format_exc()
            print(f"[STREAM] ERROR: {e}\n{full_traceback}", flush=True)
            if stage1_results:
                try:
                    metadata = {}
                    if label_to_model:
                        metadata["label_to_model"] = label_to_model
                    if aggregate_rankings:
                        metadata["aggregate_rankings"] = aggregate_rankings
                    await storage.add_assistant_message_async(
                        conversation_id, stage1_results, stage2_results, stage3_result, metadata
                    )
                    print(f"[STREAM] Partial save succeeded", flush=True)
                except Exception as save_err:
                    print(f"[STREAM] Partial save failed: {save_err}", flush=True)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Tell any nginx-based proxy in the chain (including Synology's
            # reverse proxy, which is nginx under the hood) to forward bytes
            # immediately instead of buffering the whole response.
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
