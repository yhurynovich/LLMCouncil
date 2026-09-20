---
name: council-code-review
description: "Code review using LLM Council multi-model deliberation. Sends code to a panel of models that independently evaluate and rank each other's feedback, then a chairman synthesizes a final verdict. Use when user asks for 'council review', 'code review', 'multi-model review', 'review my code', 'council feedback', or wants code evaluated by multiple LLMs. Supports file attachments (any code file type) with hybrid embed/MCP large file handling for files up to 100MB. Supports session ID tracking for OpenRouter proxy observability."
---

# Council Code Review

Multi-model deliberation system for code review. Multiple LLMs independently evaluate code, anonymously rank each other's feedback, and a chairman synthesizes a final verdict.

## How Model Sets Work

The Council backend groups models into **model sets** (e.g., `search`, `code`, `think`). Each set has:
- `council`: list of models that respond independently
- `chairman`: single model that synthesizes the final verdict

Model sets are stored in `data/model_sets.json` on the backend. The active set is in `data/active_model_set.json`.

To list available model sets:
```bash
curl http://192.168.31.66:5174/api/model-sets
```

To create a new model set (e.g., `code`):
```bash
curl -X POST http://192.168.31.66:5174/api/model-sets \
  -H "Content-Type: application/json" \
  -d '{
    "set_id": "code",
    "label": "Code Review",
    "icon": "CODE",
    "description": "Models optimized for code review tasks.",
    "council": ["openrouter/openai/gpt-4o", "openrouter/anthropic/claude-sonnet-4-5"],
    "chairman": "openrouter/anthropic/claude-sonnet-4-5"
  }'
```

## Instructions

### Step 1: Gather input

Collect the code to review. The user may provide:
- Code pasted directly in the message
- File path(s) to attach
- Both (files + additional context)

### Step 2: Build the review payload

Use the bundled script to send code to the Council backend:

```bash
python3 SKILL_DIR/scripts/council_review.py \
  --url http://192.168.31.66:5174 \
  --model code \
  --code "PASTE_OR_VARIABLE" \
  --files /path/to/file1.py /path/to/file2.ts
```

- `--url`: Backend API URL (default: `http://192.168.31.66:5174`). Note: URL base, not `/v1` prefix.
- `--model`: Model set name (default: `code`). Accepts: `code`, `search`, `think`. Also accepts `set/code` format.
- `--code`: Code string to review (use when code is inline)
- `--files`: Space-separated file paths to attach. Files are **uploaded** to the backend's `/api/upload` endpoint, returning a `file_id`, then attached as `FileAttachment` objects. The backend decides whether to embed or use MCP tools based on file size.
- `--context`: Optional context/instructions for the review (e.g. "focus on security", "review for performance")
- `--session-id`: Optional session ID for conversation tracking (auto-generated UUID if not provided). Sent as `X-Session-ID` and `X-Conversation-ID` headers to the OpenRouter proxy for log correlation and observability.
- `--quick`: Skip Stage 2 & 3 (ranking and synthesis), return Stage 1 only (faster)
- `--format`: Output format for non-raw mode: `text` (default) or `json`
- `--stream`: Force streaming SSE endpoint even for inline code (gives full 3-stage output)

### Large File Handling via MCP

When files are attached, the backend uses a **hybrid embed/MCP** strategy:

- **Text files ≤ 100KB** — embedded directly in the prompt (fast, no extra round-trips)
- **Text files ≤ 1MB** — embedded with a truncation note (partial content in prompt)
- **Text files > 1MB** — made available via MCP file tools (`read_file`, `search_files`, `list_files`, `get_file_info`). The LLM calls these tools on-demand to access content, bypassing the 5MB OpenRouter proxy body limit.
- **Images ≤ 2MB** (after downscaling) — embedded as base64 directly in the prompt
- **Images > 2MB** — made available via MCP file tools

This allows reviewing files of **any size** (up to the `MAX_TEXT_SIZE_MB` / `MAX_IMAGE_SIZE_MB` env var limits, default 100MB each) without hitting payload size limits.

### Step 3: Present results

The script outputs all stages:
- `stage1`: Individual model responses (one per council model)
- `stage2`: Anonymous peer evaluations and rankings
- `stage3`: Chairman's final synthesized verdict
- `metadata`: Model mapping and aggregate rankings
- `title`: Auto-generated conversation title

Present results to the user in a readable format:
1. **Final Verdict** (Stage 3) — the synthesized recommendation
2. **Individual Reviews** (Stage 1) — expandable tabs for each model's take
3. **Peer Rankings** (Stage 2) — which models ranked which responses highest

### Step 4: Follow-up

The user may ask to:
- Re-run with different focus areas (security, performance, style)
- Drill into a specific model's feedback
- Compare reviews across different code snippets

## Session Tracking

The script supports session tracking via the `--session-id` argument. This is useful for:

- **Correlating requests** across multiple review runs for the same codebase
- **Log correlation** in the OpenRouter proxy server (logs include `X-Session-ID`)
- **Debugging** - include the session ID in error reports

If not provided, a random UUID is generated and printed to stderr.

## Examples

User says: "review this function for bugs" → Read code → Send to council → Present verdict.

User says: "attach utils.py and review it" → Read file → Send with `--files utils.py` → Present verdict.

User says: "review this 5000-line TypeScript file" → Upload file → Backend uses MCP tools for on-demand file reading → Present verdict from council.

User says: "council review my PR changes" → Upload all changed files → Backend embeds small ones, uses MCP for large ones → Present verdict with per-file breakdown.

## Important: Avoid Infinite Loops

When the user asks to "fix all critical bugs" or similar open-ended tasks, do NOT loop indefinitely (review → fix → review → fix ...). Instead:

1. **Set a hard iteration limit** — run the council review once, present findings, then stop. Let the user decide which fixes to apply.
2. **Single-pass only** — the skill's job is to *review*, not to auto-fix. One council deliberation per invocation.
3. **Explicit boundary** — if the user says "review and fix", do ONE review pass, apply the most critical fix if clearly unambiguous (e.g., `a - b` → `a + b`), then present what was changed. Do not re-run the council on the result.
4. **Recommend iteration to the user** — after presenting results, suggest: "Want me to re-run the council after you apply fixes?" rather than doing it automatically.

This prevents runaway loops where the agent endlessly reviews its own changes.

## Troubleshooting

- **Connection error**: Backend at `http://192.168.31.66:5174` may be down. Start with: `cd /path/to/LLMCouncil && python -m backend.main`
- **Timeout**: Council runs all models in parallel (Stage 1), then sequential peer review (Stage 2) and synthesis (Stage 3). Total time is typically 30-120s. Use `--quick` to skip Stage 2 & 3 for faster results.
- **File upload failed**: Check that `MAX_TEXT_SIZE_MB` and `MAX_IMAGE_SIZE_MB` env vars allow the file size (default 100MB each). Large files are handled via MCP tools — no need to split.
- **Model set not found**: Check available sets with `curl http://192.168.31.66:5174/api/model-sets`. Create new sets via `POST /api/model-sets`.
