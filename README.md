# LLM Council

Forked from https://github.com/karpathy/llm-council.git

![llmcouncil](header.jpg)

A multi-model LLM council system where multiple AI models collaboratively answer your questions through 3-stage deliberation: individual responses, anonymized peer review, and chairman synthesis.

## What It Does

When you submit a query:

1. **Stage 1: First opinions** — Your query goes to all council models in parallel. Responses are shown in a tabbed view for inspection.
2. **Stage 2: Peer review** — Each model evaluates the others' responses. Identities are anonymized ("Response A, B, C...") to prevent bias. Models rank responses by accuracy and insight.
3. **Stage 3: Chairman synthesis** — A designated Chairman model compiles all responses and rankings into a single, comprehensive answer.

## New Features (vs. Original)

### Multi-Provider Support
- **Not just OpenRouter** — connect to any OpenAI-compatible API (local or remote)
- Built-in support for local model deployments (Qwen, DeepSeek, GLM/Kimi)
- Provider CRUD via API and UI

### Model Set Management
- **Configurable model sets** — group models into named sets (e.g., "Free Tier", "Smartest", "Internet Search")
- Create, edit, and delete custom model sets
- Switch active model set on the fly
- Built-in presets + unlimited custom sets

### OpenAI-Compatible API
- **`GET /v1/models`** — list available models in OpenAI format
- **`POST /v1/chat/completions`** — run the full council via OpenAI-compatible endpoint
- Works with Hermes Agent, OpenWebUI, and other OpenAI-compatible clients
- Supports both streaming (`stream=true`) and non-streaming responses

### Streaming Support
- **SSE streaming** in both the web UI and OpenAI-compatible endpoints
- Real-time stage progress updates (Stage 1 → 2 → 3)
- Stop button to interrupt responses

### File Attachments
- Upload and attach files (text, images) to chat messages
- Files are prepended to the query context for all models

### Enhanced Error Handling
- **Real error messages** — when a model fails, see the actual error (HTTP status, timeout, etc.) instead of generic messages
- Error details panel with expandable trace
- Graceful degradation — continue with successful responses if some models fail

### UI Improvements
- **Response time display** — see how fast each model responded
- **Quick mode** — skip Stage 2 & 3 for faster single-stage answers
- **Aggregate rankings** — average rank position across all peer evaluations
- **De-anonymized display** — model names shown alongside anonymous labels for readability
- Conversation management (create, delete, list)

### Infrastructure
- **Port configuration** — backend port configurable via `.env` (`VITE_BACKEND_PORT`)
- **SOCKS/HTTP proxy support** — works behind corporate proxies
- **OpenAPI documentation** — full API docs at `/docs`
- **JSON storage** — conversations persisted in `data/conversations/`

## Setup

### 1. Install Dependencies

**Backend:**
```bash
uv sync
```

**Frontend:**
```bash
cd frontend
npm install
```

### 2. Configure Environment

Create `.env` in the project root:

```bash
# Required — get your key at https://openrouter.ai
OPENROUTER_API_KEY=sk-or-v1-...

# Optional — backend port (default: 8001)
VITE_BACKEND_PORT=8001
```

### 3. Configure Providers (Optional)

For custom/local providers, edit `data/providers.json`:

```json
{
  "openrouter": {
    "base_url": "https://openrouter.ai/api/v1/chat/completions",
    "api_key_env": "OPENROUTER_API_KEY"
  },
  "my-local-model": {
    "base_url": "http://localhost:8080/v1/chat/completions",
    "api_key": "dummy-key",
    "model": "my-model-name"
  }
}
```

### 4. Configure Model Sets (Optional)

Model sets are managed via the UI or `data/model-sets.json`. Each set defines:
- `council` — list of model IDs for Stage 1 & 2
- `chairman` — model ID for Stage 3 synthesis

## Running the Application

**Option 1: Start script**
```bash
./start.sh
```

**Option 2: Manual**

Terminal 1 (Backend):
```bash
uv run python -m backend.main
```

Terminal 2 (Frontend):
```bash
cd frontend
npm run dev
```

Open http://localhost:5173 in your browser.

## API Endpoints

### Web API
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/model-sets` | List all model sets |
| POST | `/api/model-sets` | Create a model set |
| PUT | `/api/model-sets/{id}` | Update a model set |
| DELETE | `/api/model-sets/{id}` | Delete a model set |
| POST | `/api/model-sets/active` | Switch active model set |
| GET | `/api/providers` | List all providers |
| POST | `/api/providers` | Add a provider |
| GET | `/api/available-models` | List all available models |
| POST | `/api/upload` | Upload a file |
| GET | `/api/conversations` | List conversations |
| POST | `/api/conversations` | Create a conversation |
| POST | `/api/conversations/{id}/message/stream` | Send message (SSE) |

### OpenAI-Compatible API
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/models` | List models (OpenAI format) |
| POST | `/v1/chat/completions` | Chat completion (supports `stream=true`) |

## Tech Stack

- **Backend:** FastAPI (Python 3.10+), async httpx, multi-provider LLM routing
- **Frontend:** React + Vite, react-markdown
- **Storage:** JSON files in `data/conversations/`
- **APIs:** OpenRouter, OpenAI-compatible endpoints
- **Package Management:** uv (Python), npm (JavaScript)
