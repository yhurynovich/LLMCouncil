"""FastAPI backend for LLM Council."""

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response
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
from . import uploads
from .llm_client import _get_proxy_url
from .http_client import create_shared_client, close_shared_client
from .council import (
    run_full_council,
    generate_conversation_title,
    stage1_collect_responses,
    stage2_collect_rankings,
    stage3_synthesize_final,
    calculate_aggregate_rankings,
)


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

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Auth Middleware ────────────────────────────────────────────────────────────
import ipaddress

# Subnet(s) allowed to bypass login entirely (comma-separated CIDRs).
# This is intentionally separate from http_client's SSRF-protection allowlist,
# which serves an unrelated purpose (restricting outbound requests) and
# shouldn't be reused for an inbound auth decision.
AUTH_BYPASS_SUBNETS = [
    ipaddress.ip_network(s.strip())
    for s in os.getenv("AUTH_BYPASS_SUBNET", "192.168.31.0/24").split(",")
    if s.strip()
]

# Load auth credentials from environment
AUTH_USERNAME = os.getenv("AUTH_USERNAME", "admin")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "changeme")

# Login page HTML
LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>LLM Council - Authentication Required</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 400px; margin: 60px auto; padding: 20px; }
        .card { background: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); padding: 32px; }
        h1 { color: #333; margin-bottom: 8px; font-size: 24px; }
        .subtitle { color: #666; margin-bottom: 24px; font-size: 14px; }
        .form-group { margin-bottom: 16px; }
        label { display: block; margin-bottom: 6px; font-weight: 500; color: #333; }
        input { width: 100%; padding: 12px; border: 1px solid #ddd; border-radius: 6px; font-size: 16px; box-sizing: border-box; }
        input:focus { outline: none; border-color: #4a90e2; box-shadow: 0 0 0 3px rgba(74,144,226,0.1); }
        button { width: 100%; padding: 12px; background: #4a90e2; color: white; border: none; border-radius: 6px; font-size: 16px; font-weight: 600; cursor: pointer; }
        button:hover { background: #357abd; }
        .error { color: #dc2626; font-size: 14px; margin-top: 12px; text-align: center; }
        .info { background: #f0f7ff; border: 1px solid #d0e7ff; border-radius: 6px; padding: 12px; margin-bottom: 20px; font-size: 13px; color: #2a7ae2; }
    </style>
</head>
<body>
    <div class="card">
        <h1>🔐 Authentication Required</h1>
        <p class="subtitle">Please enter your credentials to access LLM Council</p>
        <div class="info">Your IP is not in the allowed list. Basic authentication is required.</div>
        <form method="post" action="/login">
            <div class="form-group">
                <label for="username">Username</label>
                <input type="text" id="username" name="username" required autocomplete="username">
            </div>
            <div class="form-group">
                <label for="password">Password</label>
                <input type="password" id="password" name="password" required autocomplete="current-password">
            </div>
            <button type="submit">Sign In</button>
        </form>
    </div>
</body>
</html>
"""

class AuthMiddleware:
    """Middleware to enforce authentication for non-allowed IPs."""
    
    def __init__(self, app):
        self.app = app
    
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        # Get client IP
        client_ip = self._get_client_ip(scope)
        
        # Skip auth for allowed IPs
        if self._is_allowed_ip(client_ip):
            await self.app(scope, receive, send)
            return
        
        # Check for login endpoint
        path = scope.get("path", "")
        if path == "/login":
            await self.app(scope, receive, send)
            return
        
        # Check for existing session cookie
        cookies = self._parse_cookies(scope.get("headers", []))
        if cookies.get("auth_token") == self._generate_token():
            await self.app(scope, receive, send)
            return
        
        # Check for Basic Auth header
        auth_header = self._get_auth_header(scope.get("headers", []))
        if auth_header and self._verify_basic_auth(auth_header):
            # Set session cookie and continue
            await self._send_with_cookie(scope, receive, send, True)
            return
        
        # Require authentication
        if path.startswith("/docs") or path.startswith("/openapi") or path.startswith("/redoc"):
            # Allow API docs without auth (optional)
            pass
        
        # Return login page
        await self._send_login_page(send)
    
    def _get_client_ip(self, scope) -> str:
        """Extract the client IP, trusting forwarded headers only when the
        immediate TCP peer is itself a private-network address (i.e. our own
        nginx container or Synology's reverse proxy relaying the request).
        A forwarded header is otherwise attacker-controlled, so a request
        arriving directly from a public IP is never allowed to claim a
        different (e.g. spoofed local) address via X-Forwarded-For."""
        client = scope.get("client")
        raw_peer = client[0] if client else None

        peer_is_private = False
        if raw_peer:
            try:
                peer_is_private = ipaddress.ip_address(raw_peer).is_private
            except ValueError:
                pass

        if peer_is_private:
            headers = dict(scope.get("headers", []))
            forwarded = headers.get(b"x-forwarded-for")
            if forwarded:
                return forwarded.decode().split(",")[0].strip()
            real_ip = headers.get(b"x-real-ip")
            if real_ip:
                return real_ip.decode()

        return raw_peer or "unknown"

    def _is_allowed_ip(self, ip: str) -> bool:
        """Check if the IP falls within AUTH_BYPASS_SUBNETS (default 192.168.31.0/24)."""
        if not ip or ip == "unknown":
            return False

        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False

        return any(addr in net for net in AUTH_BYPASS_SUBNETS)
    
    def _parse_cookies(self, headers) -> dict:
        """Parse Cookie header."""
        cookies = {}
        for name, value in headers:
            if name == b"cookie":
                cookie_str = value.decode()
                for part in cookie_str.split(";"):
                    if "=" in part:
                        k, v = part.strip().split("=", 1)
                        cookies[k] = v
        return cookies
    
    def _get_auth_header(self, headers) -> str | None:
        """Extract Authorization header."""
        for name, value in headers:
            if name == b"authorization":
                return value.decode()
        return None
    
    def _verify_basic_auth(self, auth_header: str) -> bool:
        """Verify Basic Auth credentials."""
        if not auth_header.startswith("Basic "):
            return False
        try:
            import base64
            credentials = base64.b64decode(auth_header[6:]).decode()
            username, password = credentials.split(":", 1)
            return username == AUTH_USERNAME and password == AUTH_PASSWORD
        except Exception:
            return False
    
    def _generate_token(self) -> str:
        """Generate a simple session token."""
        import hashlib
        import time
        return hashlib.sha256(f"{AUTH_USERNAME}:{AUTH_PASSWORD}:{int(time.time() // 3600)}".encode()).hexdigest()[:32]
    
    async def _send_with_cookie(self, scope, receive, send, set_cookie: bool):
        """Send response with auth cookie."""
        async def send_wrapper(message):
            if message["type"] == "http.response.start" and set_cookie:
                headers = list(message.get("headers", []))
                token = self._generate_token()
                headers.append((b"set-cookie", f"auth_token={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=86400".encode()))
                message["headers"] = headers
            await send(message)
        await self.app(scope, receive, send_wrapper)
    
    async def _send_login_page(self, send):
        """Send login page response."""
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"text/html; charset=utf-8"),
                (b"www-authenticate", b"Basic realm=\"LLM Council\""),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": LOGIN_HTML.encode(),
        })


app.add_middleware(AuthMiddleware)

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


@app.post("/login")
async def login(response: Response, username: str = Form(...), password: str = Form(...)):
    """Handle login form submission."""
    from .http_client import _is_private_ip
    import hashlib
    import time
    
    # Verify credentials
    if username == AUTH_USERNAME and password == AUTH_PASSWORD:
        token = hashlib.sha256(f"{AUTH_USERNAME}:{AUTH_PASSWORD}:{int(time.time() // 3600)}".encode()).hexdigest()[:32]
        response.set_cookie(
            key="auth_token",
            value=token,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=86400,  # 24 hours
        )
        return {"ok": True, "redirect": "/"}
    
    raise HTTPException(status_code=401, detail="Invalid credentials")


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
    model_sets = await cfg.get_model_sets()
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
        await cfg.set_active_model_set("free")
    await cfg.set_model_sets(model_sets)
    return {"ok": True}


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

            async with httpx.AsyncClient(timeout=30, proxy=_get_proxy_url(), trust_env=False) as client:
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

            async with httpx.AsyncClient(timeout=30, proxy=_get_proxy_url(), trust_env=False) as client:
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


@app.post("/v1/chat/completions", tags=["OpenAI Compatible"])
async def openai_chat_completions(request: OpenAIChatCompletionRequest):
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
        stage1_results = await stage1_collect_responses(
            msgs, council_models,
            temperature=request.temperature,
            max_tokens=request.max_tokens
        )

        if not stage1_results or all(r.get("response") is None for r in stage1_results):
            return "All models failed to respond. Please try again."

        responding_models = [r["model"] for r in stage1_results if r.get("response") is not None]
        stage2_results, label_to_model = await stage2_collect_rankings(
            msgs, stage1_results, responding_models,
            temperature=request.temperature,
            max_tokens=request.max_tokens
        )
        stage3_result = await stage3_synthesize_final(
            msgs, stage1_results, stage2_results, chairman_model,
            temperature=request.temperature,
            max_tokens=request.max_tokens
        )
        return stage3_result.get("response", "") if stage3_result else ""

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    if request.stream:
        async def event_stream():
            try:
                final_response = await run_council()
            except Exception as e:
                print(f"[OPENAI] Error: {e}\n{traceback.format_exc()}", flush=True)
                final_response = f"Error: council run failed ({e})"

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
        print(f"[OPENAI] Error: {e}\n{traceback.format_exc()}", flush=True)
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
            stage1_results = await stage1_collect_responses(messages, council_models, files=request.files)
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

                stage2_results, label_to_model = await stage2_collect_rankings(
                    messages, stage1_results, responding_models,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens
                )
                aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
                print(f"[STREAM] Stage 2 complete", flush=True)
                yield f"data: {json.dumps({'type': 'stage2_complete', 'data': stage2_results, 'metadata': {'label_to_model': label_to_model, 'aggregate_rankings': aggregate_rankings}})}\n\n"

                # Stage 3
                print(f"[STREAM] Stage 3 starting", flush=True)
                yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
                stage3_result = await stage3_synthesize_final(
                    messages, stage1_results, stage2_results, chairman_model,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens
                )
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
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
