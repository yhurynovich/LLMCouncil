# OpenAI-Compatible Endpoints for Hermes

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add OpenAI-compatible endpoints so Hermes can discover and use the LLM Council API.

**Architecture:** Add `/v1/models` and `/v1/chat/completions` endpoints that follow OpenAI API format, mapping to existing backend logic.

**Tech Stack:** Python, FastAPI, Pydantic

## Global Constraints

- Backend runs on port 8001
- Must be compatible with OpenAI API format
- Reuse existing council logic (stage1, stage2, stage3)

---

### Task 1: Add OpenAI-compatible Pydantic models

**Covers:** Request/response models for OpenAI endpoints

**Files:**
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: None
- Produces: Pydantic models for OpenAI API format

- [ ] **Step 1: Add OpenAI-compatible Pydantic models**

Add after line 98 in `backend/main.py`:

```python
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
```

- [ ] **Step 2: Verify the change**

Run: `cd /home/yury/Documents/LLMCouncil && .venv/bin/python -c "from backend.main import app; print('Import successful')"`

Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add backend/main.py
git commit -m "feat: add OpenAI-compatible Pydantic models"
```

---

### Task 2: Add GET /v1/models endpoint

**Covers:** OpenAI model listing endpoint

**Files:**
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: `prov.PROVIDERS`, `cfg.MODEL_SETS`
- Produces: `OpenAIModelList` response

- [ ] **Step 1: Add GET /v1/models endpoint**

Add after the `/api/available-models` endpoint (around line 334) in `backend/main.py`:

```python
# ── OpenAI-compatible endpoints ──────────────────────────────────────────────

@app.get("/v1/models", response_model=OpenAIModelList)
async def openai_list_models():
    """List available models in OpenAI-compatible format."""
    import time
    
    models = []
    current_time = int(time.time())
    
    # Add model sets as models
    for set_id, ms in cfg.MODEL_SETS.items():
        models.append(OpenAIModel(
            id=f"set/{set_id}",
            object="model",
            created=current_time,
            owned_by="llm-council"
        ))
    
    # Add individual models from providers
    for provider_name, provider in prov.PROVIDERS.items():
        api_key = prov.get_provider_api_key(provider)
        if not api_key and provider_name != "openrouter":
            continue
        
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

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(models_url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    for m in data.get("data", []):
                        model_id = m.get("id", "")
                        if model_id:
                            models.append(OpenAIModel(
                                id=f"{provider_name}/{model_id}",
                                object="model",
                                created=current_time,
                                owned_by=provider_name
                            ))
        except Exception as e:
            print(f"Error fetching models from {provider_name}: {e}")
    
    return OpenAIModelList(object="list", data=models)
```

- [ ] **Step 2: Verify the endpoint works**

Run: `cd /home/yury/Documents/LLMCouncil && .venv/bin/python -c "from backend.main import app; print('Endpoint added')"`

Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add backend/main.py
git commit -m "feat: add GET /v1/models endpoint"
```

---

### Task 3: Add POST /v1/chat/completions endpoint

**Covers:** OpenAI chat completion endpoint

**Files:**
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: `run_full_council` from `backend.council`
- Produces: `OpenAIChatCompletionResponse` response

- [ ] **Step 1: Add POST /v1/chat/completions endpoint**

Add after the `/v1/models` endpoint in `backend/main.py`:

```python
@app.post("/v1/chat/completions", response_model=OpenAIChatCompletionResponse)
async def openai_chat_completions(request: OpenAIChatCompletionRequest):
    """Create a chat completion using the LLM Council in OpenAI-compatible format."""
    import time
    import uuid
    
    # Extract user message from messages list
    user_message = ""
    for msg in request.messages:
        if msg.role == "user":
            user_message = msg.content
            break
    
    if not user_message:
        raise HTTPException(status_code=400, detail="No user message found")
    
    # Resolve model set
    set_id = request.model
    if set_id.startswith("set/"):
        set_id = set_id[4:]  # Remove "set/" prefix
    
    if set_id not in cfg.MODEL_SETS:
        set_id = cfg.ACTIVE_MODEL_SET
    
    # Run the council
    try:
        stage1_results, stage2_results, stage3_result, metadata = await run_full_council(
            user_message, council_models=cfg.MODEL_SETS[set_id]["council"]
        )
        
        # Extract the final response
        final_response = stage3_result.get("response", "") if stage3_result else ""
        
        return OpenAIChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            object="chat.completion",
            created=int(time.time()),
            model=request.model,
            choices=[
                OpenAIChoice(
                    index=0,
                    message={"role": "assistant", "content": final_response},
                    finish_reason="stop"
                )
            ],
            usage=OpenAIUsage(
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0
            )
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 2: Verify the endpoint works**

Run: `cd /home/yury/Documents/LLMCouncil && .venv/bin/python -c "from backend.main import app; print('Endpoint added')"`

Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add backend/main.py
git commit -m "feat: add POST /v1/chat/completions endpoint"
```

---

### Task 4: Add OpenAPI metadata and documentation

**Covers:** OpenAPI documentation improvements

**Files:**
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: None
- Produces: Improved OpenAPI documentation

- [ ] **Step 1: Update FastAPI app with metadata**

Replace line 27 in `backend/main.py`:

```python
app = FastAPI(
    title="LLM Council API",
    description="A multi-model LLM council system with OpenAI-compatible endpoints for Hermes integration.",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)
```

- [ ] **Step 2: Add tags to endpoints**

Add tags to endpoint decorators. For example:

```python
@app.get("/api/conversations", response_model=List[ConversationMetadata], tags=["Conversations"])
@app.post("/api/conversations", response_model=Conversation, tags=["Conversations"])
@app.get("/api/conversations/{conversation_id}", response_model=Conversation, tags=["Conversations"])
@app.delete("/api/conversations/{conversation_id}", tags=["Conversations"])
@app.post("/api/conversations/{conversation_id}/message/stream", tags=["Conversations"])

@app.get("/api/model-sets", tags=["Model Sets"])
@app.post("/api/model-sets/active", tags=["Model Sets"])
@app.post("/api/model-sets", tags=["Model Sets"])
@app.put("/api/model-sets/{set_id}", tags=["Model Sets"])
@app.delete("/api/model-sets/{set_id}", tags=["Model Sets"])

@app.get("/api/providers", tags=["Providers"])
@app.post("/api/providers", tags=["Providers"])
@app.put("/api/providers/{name}", tags=["Providers"])
@app.delete("/api/providers/{name}", tags=["Providers"])

@app.get("/api/available-models", tags=["Models"])
@app.post("/api/upload", tags=["Files"])

@app.get("/v1/models", tags=["OpenAI Compatible"])
@app.post("/v1/chat/completions", tags=["OpenAI Compatible"])
```

- [ ] **Step 3: Verify OpenAPI docs work**

Run: `cd /home/yury/Documents/LLMCouncil && .venv/bin/python -c "from backend.main import app; print(f'OpenAPI URL: {app.openapi_url}')"`

Expected: Shows OpenAPI URL

- [ ] **Step 4: Commit**

```bash
git add backend/main.py
git commit -m "docs: improve OpenAPI metadata and add tags"
```

---

### Task 5: Test endpoints with curl

**Covers:** Endpoint testing

**Files:**
- None (testing only)

**Interfaces:**
- Consumes: All previous tasks
- Produces: Verified working endpoints

- [ ] **Step 1: Test GET /v1/models**

Run: `curl -s http://localhost:8001/v1/models | head -50`

Expected: JSON response with models list

- [ ] **Step 2: Test POST /v1/chat/completions**

Run: `curl -s -X POST http://localhost:8001/v1/chat/completions -H "Content-Type: application/json" -d '{"model": "set/free", "messages": [{"role": "user", "content": "Hello"}]}' | head -50`

Expected: JSON response with chat completion

- [ ] **Step 3: Test OpenAPI docs**

Run: `curl -s http://localhost:8001/openapi.json | head -50`

Expected: OpenAPI JSON spec

- [ ] **Step 4: Final commit if any fixes needed**

```bash
git add -A
git commit -m "chore: verify OpenAI-compatible endpoints"
```
