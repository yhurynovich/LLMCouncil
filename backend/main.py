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

from . import storage
from . import config as cfg
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

class SetModelSetRequest(BaseModel):
    set_id: str

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
    active = cfg.MODEL_SETS[request.set_id]
    return {
        "active": request.set_id,
        "label": active["label"],
        "council": active["council"],
        "chairman": active["chairman"],
    }


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
            print(f"[STREAM] Stage 1 starting — set={set_id}, models={council_models}", flush=True)
            yield f"data: {json.dumps({'type': 'stage1_start'})}\n\n"
            stage1_results = await stage1_collect_responses(request.content, council_models)
            print(f"[STREAM] Stage 1 complete: {len(stage1_results)} responses", flush=True)
            yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results})}\n\n"

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
