"""FastAPI backend for LLM Council."""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import uuid
import json
import asyncio
import traceback
import httpx

from . import storage
from . import config as cfg
from . import providers as prov
from .council import (
    run_full_council,
    generate_conversation_title,
    stage1_collect_responses,
    stage2_collect_rankings,
    stage3_synthesize_final,
    calculate_aggregate_rankings,
)

app = FastAPI(title="LLM Council API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Pydantic models ───────────────────────────────────────────────────────────

class CreateConversationRequest(BaseModel):
    pass

class SendMessageRequest(BaseModel):
    content: str
    model_set: Optional[str] = None  # if provided, overrides active set for this request
    quick: bool = False  # skip Stage 2 & 3, return Stage 1 only

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


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "ok", "service": "LLM Council API"}


# ── Model Sets ────────────────────────────────────────────────────────────────

@app.get("/api/model-sets")
async def list_model_sets():
    """Return all model sets and the currently active one."""
    sets = {}
    for key, val in cfg.MODEL_SETS.items():
        sets[key] = {
            "label": val["label"],
            "icon": val["icon"],
            "description": val["description"],
            "council": val["council"],
            "chairman": val["chairman"],
        }
    return {"sets": sets, "active": cfg.ACTIVE_MODEL_SET}


@app.post("/api/model-sets/active")
async def set_active_model_set(request: SetModelSetRequest):
    """Switch the active model set."""
    if request.set_id not in cfg.MODEL_SETS:
        raise HTTPException(status_code=400, detail=f"Unknown model set: {request.set_id}")
    cfg.ACTIVE_MODEL_SET = request.set_id
    cfg._save_active_model_set(request.set_id)
    active = cfg.MODEL_SETS[request.set_id]
    return {
        "active": request.set_id,
        "label": active["label"],
        "council": active["council"],
        "chairman": active["chairman"],
    }


@app.post("/api/model-sets")
async def create_model_set(request: CreateModelSetRequest):
    """Create a new model set."""
    set_id = request.set_id.strip().lower().replace(" ", "-")
    if not set_id:
        raise HTTPException(status_code=400, detail="set_id is required")
    if set_id in cfg.MODEL_SETS:
        raise HTTPException(status_code=409, detail=f"Model set '{set_id}' already exists")

    cfg.MODEL_SETS[set_id] = {
        "label": request.label,
        "icon": request.icon or request.label[:4].upper(),
        "description": request.description,
        "council": request.council,
        "chairman": request.chairman,
    }
    cfg._save_model_sets(cfg.MODEL_SETS)
    return {"ok": True, "set_id": set_id}


@app.put("/api/model-sets/{set_id}")
async def update_model_set(set_id: str, request: UpdateModelSetRequest):
    """Update an existing model set."""
    if set_id not in cfg.MODEL_SETS:
        raise HTTPException(status_code=404, detail=f"Model set '{set_id}' not found")

    ms = cfg.MODEL_SETS[set_id]
    if request.label is not None:
        ms["label"] = request.label
    if request.icon is not None:
        ms["icon"] = request.icon
    if request.description is not None:
        ms["description"] = request.description
    if request.council is not None:
        ms["council"] = request.council
    if request.chairman is not None:
        ms["chairman"] = request.chairman

    cfg._save_model_sets(cfg.MODEL_SETS)
    return {"ok": True, "set_id": set_id}


@app.delete("/api/model-sets/{set_id}")
async def delete_model_set(set_id: str):
    """Delete a model set. Built-in sets cannot be deleted."""
    if set_id not in cfg.MODEL_SETS:
        raise HTTPException(status_code=404, detail=f"Model set '{set_id}' not found")
    if set_id in cfg.BUILTIN_SET_IDS:
        raise HTTPException(status_code=400, detail=f"Cannot delete built-in set '{set_id}'")

    del cfg.MODEL_SETS[set_id]
    if cfg.ACTIVE_MODEL_SET == set_id:
        cfg.ACTIVE_MODEL_SET = "free"
    cfg._save_model_sets(cfg.MODEL_SETS)
    return {"ok": True}


# ── Providers ──────────────────────────────────────────────────────────────

@app.get("/api/providers")
async def list_providers():
    """Return all configured providers."""
    return {"providers": prov.list_providers()}


@app.post("/api/providers")
async def create_provider(request: CreateProviderRequest):
    """Add a new provider."""
    name = request.name.strip().lower().replace(" ", "-")
    if not name:
        raise HTTPException(status_code=400, detail="Provider name is required")
    if name in prov.PROVIDERS:
        raise HTTPException(status_code=409, detail=f"Provider '{name}' already exists")

    prov.PROVIDERS[name] = {
        "base_url": request.base_url,
        "api_key": request.api_key,
        "api_key_env": request.api_key_env,
        "stream": request.stream,
        "description": request.description,
    }
    prov._save_providers(prov.PROVIDERS)
    return {"ok": True, "name": name}


@app.put("/api/providers/{name}")
async def update_provider(name: str, request: UpdateProviderRequest):
    """Update an existing provider."""
    if name not in prov.PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")

    p = prov.PROVIDERS[name]
    if request.base_url is not None:
        p["base_url"] = request.base_url
    if request.api_key is not None:
        p["api_key"] = request.api_key
    if request.api_key_env is not None:
        p["api_key_env"] = request.api_key_env
    if request.stream is not None:
        p["stream"] = request.stream
    if request.description is not None:
        p["description"] = request.description

    prov._save_providers(prov.PROVIDERS)
    return {"ok": True, "name": name}


@app.delete("/api/providers/{name}")
async def delete_provider(name: str):
    """Delete a provider. Cannot delete 'openrouter'."""
    if name not in prov.PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    if name == "openrouter":
        raise HTTPException(status_code=400, detail="Cannot delete built-in 'openrouter' provider")

    del prov.PROVIDERS[name]
    prov._save_providers(prov.PROVIDERS)
    return {"ok": True}


@app.get("/api/available-models")
async def list_available_models():
    """Fetch available models from all providers."""
    all_models = []

    for provider_name, provider in prov.PROVIDERS.items():
        api_key = prov.get_provider_api_key(provider)
        if not api_key and provider_name != "openrouter":
            continue

        try:
            # Derive models endpoint from base_url
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

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(models_url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    # OpenAI-compatible format: { "data": [{ "id": "...", "name": "..." }] }
                    for m in data.get("data", []):
                        model_id = m.get("id", "")
                        if model_id:
                            all_models.append({
                                "id": f"{provider_name}/{model_id}",
                                "name": m.get("name", model_id),
                                "provider": provider_name,
                                "pricing": m.get("pricing", {}),
                                "context_length": m.get("context_length"),
                            })
                else:
                    # Fallback: use configured model if models endpoint fails
                    model_id = provider.get("model", "")
                    if model_id:
                        all_models.append({
                            "id": f"{provider_name}/{model_id}",
                            "name": model_id,
                            "provider": provider_name,
                            "pricing": {},
                            "context_length": None,
                        })
        except Exception as e:
            print(f"Error fetching models from {provider_name}: {e}")
            # Fallback: use configured model
            model_id = provider.get("model", "")
            if model_id:
                all_models.append({
                    "id": f"{provider_name}/{model_id}",
                    "name": model_id,
                    "provider": provider_name,
                    "pricing": {},
                    "context_length": None,
                })

    return {"models": all_models}


# ── Conversations ─────────────────────────────────────────────────────────────

@app.get("/api/conversations", response_model=List[ConversationMetadata])
async def list_conversations():
    return storage.list_conversations()


@app.post("/api/conversations", response_model=Conversation)
async def create_conversation(request: CreateConversationRequest):
    conversation_id = str(uuid.uuid4())
    conversation = storage.create_conversation(conversation_id)
    return conversation


@app.get("/api/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(conversation_id: str):
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    if not storage.delete_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


@app.post("/api/conversations/{conversation_id}/message/stream")
async def send_message_stream(conversation_id: str, request: SendMessageRequest):
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    is_first_message = len(conversation["messages"]) == 0

    # Resolve which model set to use for this request
    set_id = request.model_set if request.model_set else cfg.ACTIVE_MODEL_SET
    if set_id not in cfg.MODEL_SETS:
        set_id = cfg.ACTIVE_MODEL_SET
    model_set = cfg.MODEL_SETS[set_id]
    council_models = model_set["council"]
    chairman_model = model_set["chairman"]

    async def event_generator():
        stage1_results = []
        stage2_results = []
        stage3_result = {}
        try:
            storage.add_user_message(conversation_id, request.content)

            # Emit which model set is being used
            yield f"data: {json.dumps({'type': 'model_set', 'data': {'set_id': set_id, 'label': model_set['label'], 'council': council_models, 'chairman': chairman_model}})}\n\n"

            title_task = None
            if is_first_message:
                title_task = asyncio.create_task(
                    generate_conversation_title(request.content)
                )

            # Stage 1
            print(f"[STREAM] Stage 1 starting — set={set_id}, models={council_models}, quick={request.quick}", flush=True)
            yield f"data: {json.dumps({'type': 'stage1_start'})}\n\n"
            stage1_results = await stage1_collect_responses(request.content, council_models)
            print(f"[STREAM] Stage 1 complete: {len(stage1_results)} responses", flush=True)
            yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results})}\n\n"

            if not request.quick:
                # Stage 2
                print(f"[STREAM] Stage 2 starting", flush=True)
                yield f"data: {json.dumps({'type': 'stage2_start'})}\n\n"
                responding_models = [r["model"] for r in stage1_results]
                stage2_results, label_to_model = await stage2_collect_rankings(
                    request.content, stage1_results, responding_models
                )
                aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
                print(f"[STREAM] Stage 2 complete", flush=True)
                yield f"data: {json.dumps({'type': 'stage2_complete', 'data': stage2_results, 'metadata': {'label_to_model': label_to_model, 'aggregate_rankings': aggregate_rankings}})}\n\n"

                # Stage 3
                print(f"[STREAM] Stage 3 starting", flush=True)
                yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
                stage3_result = await stage3_synthesize_final(
                    request.content, stage1_results, stage2_results, chairman_model
                )
                print(f"[STREAM] Stage 3 complete", flush=True)
                yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result})}\n\n"

            # Title
            if title_task:
                title = await title_task
                storage.update_conversation_title(conversation_id, title)
                yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"

            # Save
            print(f"[STREAM] Saving to storage", flush=True)
            storage.add_assistant_message(
                conversation_id, stage1_results, stage2_results, stage3_result
            )
            print(f"[STREAM] Saved successfully", flush=True)

            yield f"data: {json.dumps({'type': 'complete'})}\n\n"

        except Exception as e:
            full_traceback = traceback.format_exc()
            print(f"[STREAM] ERROR: {e}\n{full_traceback}", flush=True)
            if stage1_results:
                try:
                    storage.add_assistant_message(
                        conversation_id, stage1_results, stage2_results, stage3_result
                    )
                    print(f"[STREAM] Partial save succeeded", flush=True)
                except Exception as save_err:
                    print(f"[STREAM] Partial save failed: {save_err}", flush=True)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
