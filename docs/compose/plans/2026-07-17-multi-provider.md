# Multi-Provider Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable LLM Council to query models from multiple API providers (OpenRouter + custom local/remote endpoints) and form model sets mixing providers.

**Architecture:** Model IDs become `provider/model` prefixed strings. A `providers.json` config maps provider names to base URLs, API keys, and stream flags. The query layer routes each model to its correct provider endpoint. OpenRouter is just another provider named `openrouter`.

**Tech Stack:** Python 3.10+, FastAPI, httpx, React 19, Vite

## Global Constraints

- Backend runs on port 8001, frontend on port 5173
- All providers use OpenAI-compatible `/v1/chat/completions` API format
- Provider config stored in `data/providers.json` (persisted, like model sets)
- Model ID format: `{provider_name}/{model_id}` (e.g., `openrouter/openai/gpt-4o`)
- Built-in model sets auto-prefixed with `openrouter/` on first load
- OpenRouter provider retains web_search tool support
- Custom providers get plain chat completions (no tools)

---

## File Structure

| File | Action | Purpose |
|------|--------|---------|
| `backend/providers.py` | Create | Provider config loading, CRUD, lookup |
| `backend/llm_client.py` | Create | Generic provider-aware query routing (replaces openrouter.py) |
| `backend/openrouter.py` | Delete | Replaced by llm_client.py |
| `backend/config.py` | Modify | Add provider prefix migration for model sets |
| `backend/council.py` | Modify | Import from llm_client instead of openrouter |
| `backend/main.py` | Modify | Add provider CRUD endpoints, update imports |
| `frontend/src/api.js` | Modify | Add provider API methods |
| `frontend/src/components/ModelSetManager.jsx` | Modify | Show provider badges, provider filter |
| `frontend/src/components/ModelSetManager.css` | Modify | Styles for provider badges |
| `data/providers.json` | Create | Default provider config |

---

### Task 1: Provider Config System

**Covers:** Provider storage, CRUD, lookup

**Files:**
- Create: `backend/providers.py`
- Create: `data/providers.json`

**Interfaces:**
- Consumes: Nothing (standalone module)
- Produces: `get_provider(name) -> dict`, `list_providers() -> dict`, `save_providers(data)`, `PROVIDERS` dict

- [ ] **Step 1: Create default providers.json**

```json
{
  "openrouter": {
    "base_url": "https://openrouter.ai/api/v1/chat/completions",
    "api_key_env": "OPENROUTER_API_KEY",
    "stream": true,
    "description": "OpenRouter marketplace — hundreds of models"
  },
  "qwen-free": {
    "base_url": "http://192.168.31.66:8600/api/chat/completions",
    "api_key": "dummy-key",
    "stream": false,
    "description": "Qwen 3.7 Max — local deployment"
  },
  "deepseek-free": {
    "base_url": "http://192.168.31.66:8601/v1/chat/completions",
    "api_key": "dummy-key",
    "stream": false,
    "description": "DeepSeek Reasoner Search — local deployment"
  },
  "glmkimi-free": {
    "base_url": "http://192.168.31.66:3364/v1/chat/completions",
    "api_key": "dummy-key",
    "stream": false,
    "description": "GLM Kimi K2.5 — local deployment"
  }
}
```

- [ ] **Step 2: Create backend/providers.py**

```python
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
```

- [ ] **Step 3: Verify providers load**

Run: `cd /home/yury/Documents/LLMCouncil && .venv/bin/python -c "from backend.providers import PROVIDERS; print(list(PROVIDERS.keys()))"`
Expected: `['openrouter', 'qwen-free', 'deepseek-free', 'glmkimi-free']`

---

### Task 2: Generic LLM Client

**Covers:** Provider-aware query routing, replaces openrouter.py

**Files:**
- Create: `backend/llm_client.py`
- Delete: `backend/openrouter.py`

**Interfaces:**
- Consumes: `backend.providers.get_provider()`, `backend.providers.get_provider_api_key()`
- Produces: `query_model(model, messages, **kwargs) -> dict | None`, `query_models_parallel(models, messages, **kwargs) -> dict`

- [ ] **Step 1: Create backend/llm_client.py**

```python
"""Generic LLM client — routes queries to the correct provider."""
import asyncio
import json
import httpx
from typing import Any

from .providers import get_provider, get_provider_api_key
from .web_search import SEARCH_TOOL, handle_tool_call

STAGGER_DELAY = 0.5


def _parse_model_id(model: str) -> tuple[str, str]:
    """Split 'provider/model_name' into (provider, model_name).
    If no slash, defaults to 'openrouter' provider."""
    if "/" in model:
        parts = model.split("/", 1)
        return parts[0], parts[1]
    return "openrouter", model


async def query_model(
    model: str,
    messages: list,
    enable_search: bool = True,
    **kwargs,
) -> dict[str, Any] | None:
    provider_name, model_id = _parse_model_id(model)
    provider = get_provider(provider_name)
    if provider is None:
        print(f"Error: Unknown provider '{provider_name}' for model '{model}'")
        return None

    api_key = get_provider_api_key(provider)
    base_url = provider["base_url"]

    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # OpenRouter-specific headers
    if provider_name == "openrouter":
        headers["HTTP-Referer"] = "https://llm-council.local"
        headers["X-Title"] = "LLM Council"

    payload: dict[str, Any] = {
        "model": model_id,
        "messages": messages,
    }

    # Only add tools for OpenRouter (custom providers may not support them)
    if enable_search and provider_name == "openrouter":
        payload["tools"] = [SEARCH_TOOL]
        payload["tool_choice"] = "auto"

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            resp = await client.post(base_url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

            choice = data["choices"][0]
            msg = choice["message"]

            # Handle tool calls (OpenRouter only)
            if enable_search and provider_name == "openrouter" and msg.get("tool_calls"):
                tc = msg["tool_calls"][0]
                tool_name = tc["function"]["name"]
                tool_args = json.loads(tc["function"]["arguments"])

                print(f"[{model}] search_web({tool_args.get('query', '')})")
                search_result = handle_tool_call(tool_name, tool_args)

                second_messages = list(messages) + [
                    {"role": "assistant", "content": None, "tool_calls": [tc]},
                    {"role": "tool", "tool_call_id": tc["id"], "name": tool_name, "content": search_result},
                ]
                second_payload = {"model": model_id, "messages": second_messages}
                resp2 = await client.post(base_url, headers=headers, json=second_payload)
                resp2.raise_for_status()
                msg = resp2.json()["choices"][0]["message"]

            result: dict[str, Any] = {"content": msg.get("content", "")}
            if "reasoning_details" in msg:
                result["reasoning_details"] = msg["reasoning_details"]
            return result

        except Exception as e:
            print(f"Error querying {model}: {e}")
            return None


async def _staggered_query(model, messages, enable_search, delay):
    if delay > 0:
        await asyncio.sleep(delay)
    return await query_model(model, messages, enable_search=enable_search)


async def query_models_parallel(
    models: list[str],
    messages: list,
    enable_search: bool = True,
    **kwargs,
) -> dict[str, Any]:
    tasks = [
        _staggered_query(model, messages, enable_search, i * STAGGER_DELAY)
        for i, model in enumerate(models)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return {
        model: (None if isinstance(res, Exception) else res)
        for model, res in zip(models, results)
    }
```

- [ ] **Step 2: Delete openrouter.py**

Run: `rm /home/yury/Documents/LLMCouncil/backend/openrouter.py`

- [ ] **Step 3: Verify imports work**

Run: `cd /home/yury/Documents/LLMCouncil && .venv/bin/python -c "from backend.llm_client import query_model, query_models_parallel; print('OK')"`
Expected: `OK`

---

### Task 3: Update Config — Provider Prefix Migration

**Covers:** Auto-prefix existing model sets with `openrouter/`

**Files:**
- Modify: `backend/config.py`

**Interfaces:**
- Consumes: `backend.providers.PROVIDERS`
- Produces: Model sets with prefixed model IDs

- [ ] **Step 1: Update config.py**

Add at the top after imports:

```python
from .providers import PROVIDERS
```

Add at the bottom of the file (before `COUNCIL_MODELS` and `CHAIRMAN_MODEL`):

```python
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
```

Update the `MODEL_SETS` assignment:

```python
MODEL_SETS = _ensure_provider_prefix(_load_model_sets())
```

- [ ] **Step 2: Verify prefix migration**

Run: `cd /home/yury/Documents/LLMCouncil && .venv/bin/python -c "from backend.config import MODEL_SETS; print(MODEL_SETS['free']['council'][:2])"`
Expected: `['openrouter/openai/gpt-oss-120b:free', 'openrouter/meta-llama/llama-3.3-70b-instruct:free']`

---

### Task 4: Update Council — Use llm_client

**Covers:** Council uses generic query routing

**Files:**
- Modify: `backend/council.py`

**Interfaces:**
- Consumes: `backend.llm_client.query_model`, `backend.llm_client.query_models_parallel`
- Produces: Same interface as before

- [ ] **Step 1: Update import in council.py**

Change line 4:
```python
from .llm_client import query_models_parallel, query_model
```

Remove the old import:
```python
# DELETE: from .openrouter import query_models_parallel, query_model
```

- [ ] **Step 2: Verify council imports**

Run: `cd /home/yury/Documents/LLMCouncil && .venv/bin/python -c "from backend.council import stage1_collect_responses; print('OK')"`
Expected: `OK`

---

### Task 5: Backend Provider CRUD API

**Covers:** Provider management endpoints

**Files:**
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: `backend.providers.list_providers`, `backend.providers.get_provider`, `backend.providers._save_providers`, `backend.providers.PROVIDERS`
- Produces: REST endpoints for provider CRUD

- [ ] **Step 1: Add provider import to main.py**

Add after existing imports:
```python
from . import providers as prov
```

- [ ] **Step 2: Add Pydantic models**

Add after existing Pydantic models:
```python
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
```

- [ ] **Step 3: Add provider endpoints**

Add after the model set endpoints:

```python
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
```

- [ ] **Step 4: Update available-models to query all providers**

Replace the existing `list_available_models` endpoint:

```python
@app.get("/api/available-models")
async def list_available_models():
    """Fetch available models from all providers."""
    all_models = []

    for provider_name, provider in prov.PROVIDERS.items():
        api_key = prov.get_provider_api_key(provider)
        if not api_key and provider_name != "openrouter":
            continue

        try:
            # Use the models endpoint for OpenRouter, skip for custom providers
            if provider_name == "openrouter":
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(
                        "https://openrouter.ai/api/v1/models",
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                    if resp.status_code == 200:
                        for m in resp.json().get("data", []):
                            all_models.append({
                                "id": f"{provider_name}/{m['id']}",
                                "name": m.get("name", m["id"]),
                                "provider": provider_name,
                                "pricing": m.get("pricing", {}),
                                "context_length": m.get("context_length"),
                            })
            else:
                # For custom providers, add the configured model
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

    return {"models": all_models}
```

- [ ] **Step 5: Restart backend and test**

Run: `cd /home/yury/Documents/LLMCouncil && kill $(lsof -t -i:8001) 2>/dev/null; sleep 1; nohup .venv/bin/python -m backend.main > /tmp/backend.log 2>&1 & sleep 2; curl -s http://localhost:8001/api/providers | python3 -m json.tool | head -20`
Expected: JSON with all 4 providers listed

---

### Task 6: Frontend — Provider API Methods

**Covers:** Frontend API client for providers

**Files:**
- Modify: `frontend/src/api.js`

**Interfaces:**
- Consumes: Backend provider CRUD endpoints
- Produces: `api.listProviders()`, `api.createProvider()`, `api.updateProvider()`, `api.deleteProvider()`

- [ ] **Step 1: Add provider methods to api.js**

Add after the `listAvailableModels` method:

```javascript
//  Providers

async listProviders() {
    const response = await fetch(`${API_BASE}/api/providers`);
    if (!response.ok) throw new Error('Failed to list providers');
    return response.json();
},

async createProvider(data) {
    const response = await fetch(`${API_BASE}/api/providers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to create provider');
    }
    return response.json();
},

async updateProvider(name, data) {
    const response = await fetch(`${API_BASE}/api/providers/${encodeURIComponent(name)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to update provider');
    }
    return response.json();
},

async deleteProvider(name) {
    const response = await fetch(`${API_BASE}/api/providers/${encodeURIComponent(name)}`, {
        method: 'DELETE',
    });
    if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to delete provider');
    }
    return response.json();
},
```

---

### Task 7: Frontend — Model Set Manager with Provider Badges

**Covers:** Show provider info in model set editor

**Files:**
- Modify: `frontend/src/components/ModelSetManager.jsx`
- Modify: `frontend/src/components/ModelSetManager.css`

**Interfaces:**
- Consumes: `api.listProviders()` data
- Produces: Provider badges in model list, provider filter

- [ ] **Step 1: Add providers state and load**

In `ModelSetManager.jsx`, add state:
```javascript
const [providers, setProviders] = useState({});
```

Update `loadData` to also fetch providers:
```javascript
const loadData = async () => {
    setLoading(true);
    try {
        const [setsData, modelsData, providersData] = await Promise.all([
            api.listModelSets(),
            api.listAvailableModels(),
            api.listProviders(),
        ]);
        setSets(setsData.sets);
        setActive(setsData.active);
        setAvailableModels(modelsData.models);
        setProviders(providersData.providers);
    } catch (e) {
        setError(e.message);
    } finally {
        setLoading(false);
    }
};
```

- [ ] **Step 2: Add provider filter dropdown**

Add after the search input in the model picker:
```jsx
<select
    value={providerFilter}
    onChange={(e) => setProviderFilter(e.target.value)}
    className="msm-provider-filter"
>
    <option value="">All Providers</option>
    {Object.keys(providers).map((p) => (
        <option key={p} value={p}>{p}</option>
    ))}
</select>
```

Add state:
```javascript
const [providerFilter, setProviderFilter] = useState('');
```

Update `filteredModels`:
```javascript
const filteredModels = useMemo(() => {
    let models = availableModels;
    if (providerFilter) {
        models = models.filter((m) => m.provider === providerFilter);
    }
    if (searchQuery) {
        const q = searchQuery.toLowerCase();
        models = models.filter(
            (m) => m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q)
        );
    }
    return models;
}, [availableModels, searchQuery, providerFilter]);
```

- [ ] **Step 3: Add provider badge to model items**

Update the model item render to show provider:
```jsx
<div className="msm-model-info">
    <div className="msm-model-name">{m.name}</div>
    <div className="msm-model-id">{m.id}</div>
</div>
<div className="msm-model-meta">
    <span className={`msm-provider-badge provider-${m.provider}`}>
        {m.provider}
    </span>
    {formatPrice(m.pricing) && (
        <span className="msm-model-price">{formatPrice(m.pricing)}</span>
    )}
    {m.context_length && (
        <span className="msm-model-ctx">
            {(m.context_length / 1000).toFixed(0)}K ctx
        </span>
    )}
</div>
```

- [ ] **Step 4: Add provider badge styles**

Add to `ModelSetManager.css`:
```css
.msm-provider-filter {
    padding: 6px 12px;
    border: 1px solid #ddd;
    border-radius: 6px;
    font-size: 13px;
    background: white;
}

.msm-provider-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
}

.provider-openrouter { background: #e3f2fd; color: #1565c0; }
.provider-qwen-free { background: #fce4ec; color: #c62828; }
.provider-deepseek-free { background: #e8f5e9; color: #2e7d32; }
.provider-glmkimi-free { background: #fff3e0; color: #e65100; }
```

- [ ] **Step 5: Restart frontend and verify**

Run: `curl -s http://localhost:5173/src/components/ModelSetManager.jsx | grep -c "provider-badge"`
Expected: `1` or more

---

### Task 8: Integration Test

**Covers:** End-to-end verification

**Files:** None (verification only)

- [ ] **Step 1: Test backend endpoints**

```bash
# Test providers endpoint
curl -s http://localhost:8001/api/providers | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Providers: {len(d[\"providers\"])}')"

# Test model sets with prefixed IDs
curl -s http://localhost:8001/api/model-sets | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Free set models: {d[\"sets\"][\"free\"][\"council\"][:1]}')"

# Test available models from custom providers
curl -s http://localhost:8001/api/available-models | python3 -c "import sys,json; d=json.load(sys.stdin); providers=set(m['provider'] for m in d['models']); print(f'Model providers: {providers}')"
```

Expected:
- Providers: 4
- Free set models: starts with `openrouter/`
- Model providers includes `qwen-free`, `deepseek-free`, `glmkimi-free`

- [ ] **Step 2: Test a query to custom provider**

```bash
# Create conversation and send message using custom model set
curl -s -X POST http://localhost:8001/api/conversations -H "Content-Type: application/json" -d '{}' | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])"
# Use the conversation ID from above
curl -s -X POST http://localhost:8001/api/conversations/<ID>/message/stream \
  -H "Content-Type: application/json" \
  -d '{"content": "Hello", "model_set": "free"}' \
  --max-time 30 | head -20
```

Expected: SSE events with stage1_complete containing response from custom provider

---

## Self-Review

1. **Spec coverage:** Task 1 covers provider config, Task 2 covers routing, Task 3 covers migration, Task 4 covers council update, Task 5 covers API, Tasks 6-7 cover frontend, Task 8 covers integration.

2. **Placeholder scan:** All code blocks are complete. No TBD/TODO markers.

3. **Type consistency:** `query_model()` signature consistent between llm_client.py and council.py. Provider dict structure consistent across providers.py, main.py, and frontend.
