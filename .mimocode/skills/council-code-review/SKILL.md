---
name: council-code-review
description: "Code review using LLM Council multi-model deliberation. Sends code to a panel of models that independently evaluate and rank each other's feedback, then a chairman synthesizes a final verdict. Use when user asks for 'council review', 'code review', 'multi-model review', 'review my code', 'council feedback', or wants code evaluated by multiple LLMs. Supports file attachments (any code file type)."
---

# Council Code Review

Multi-model deliberation system for code review. Multiple LLMs independently evaluate code, anonymously rank each other's feedback, and a chairman synthesizes a final verdict.

## How Model Sets Work

The Council backend groups models into **model sets** (e.g., `search`, `free`, `smart`). Each set has:
- `council`: list of models that respond independently
- `chairman`: single model that synthesizes the final verdict

Model sets are stored in `data/model_sets.json` on the backend. The active set is in `data/active_model_set.json`.

To list available model sets:
```bash
curl http://localhost:8001/api/model-sets
```

To create a new model set (e.g., `code`):
```bash
curl -X POST http://localhost:8001/api/model-sets \
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

Read all attached files fully before sending. Include full file contents in the review request.

### Step 2: Build the review payload

Use the bundled script to send code to the Council backend:

```bash
python3 SKILL_DIR/scripts/council_review.py \
  --url http://localhost:8001/v1 \
  --model code \
  --code "PASTE_OR_VARIABLE" \
  --files /path/to/file1.py /path/to/file2.ts
```

- `--url`: Backend API endpoint (default: `http://localhost:8001/v1`)
- `--model`: Model set name (default: `code`). Accepts: `code`, `search`, `free`, `smart`, `reasonable`, `privacy`, or any custom set_id. Also accepts `set/code` format.
- `--code`: Code string to review (use when code is inline)
- `--files`: Space-separated file paths to attach (reads content and includes in payload)
- `--context`: Optional context/instructions for the review (e.g. "focus on security", "review for performance")

### Step 3: Present results

The script outputs JSON with:
- `stage1`: Individual model responses (one per council model)
- `stage2`: Anonymous peer evaluations and rankings
- `stage3`: Chairman's final synthesized verdict
- `metadata`: Model mapping and aggregate rankings

Present results to the user in a readable format:
1. **Final Verdict** (Stage 3) — the synthesized recommendation
2. **Individual Reviews** (Stage 1) — expandable tabs for each model's take
3. **Peer Rankings** (Stage 2) — which models ranked which responses highest

### Step 4: Follow-up

The user may ask to:
- Re-run with different focus areas (security, performance, style)
- Drill into a specific model's feedback
- Compare reviews across different code snippets

## Examples

User says: "review this function for bugs" → Read code → Send to council → Present verdict.

User says: "attach utils.py and review it" → Read file → Send with `--files utils.py` → Present verdict.

User says: "council review my PR changes" → Read changed files → Send all → Present verdict with per-file breakdown.

## Important: Avoid Infinite Loops

When the user asks to "fix all critical bugs" or similar open-ended tasks, do NOT loop indefinitely (review → fix → review → fix ...). Instead:

1. **Set a hard iteration limit** — run the council review once, present findings, then stop. Let the user decide which fixes to apply.
2. **Single-pass only** — the skill's job is to *review*, not to auto-fix. One council deliberation per invocation.
3. **Explicit boundary** — if the user says "review and fix", do ONE review pass, apply the most critical fix if clearly unambiguous (e.g., `a - b` → `a + b`), then present what was changed. Do not re-run the council on the result.
4. **Recommend iteration to the user** — after presenting results, suggest: "Want me to re-run the council after you apply fixes?" rather than doing it automatically.

This prevents runaway loops where the agent endlessly reviews its own changes.

## Troubleshooting

- **Connection error**: Backend at `http://localhost:8001/v1` may be down. Start with: `cd /path/to/LLMCouncil && python -m backend.main`
- **Timeout**: Large codebases may take longer. The script has a 600s timeout; for very large payloads consider splitting.
- **Model set not found**: Check available sets with `curl http://localhost:8001/api/model-sets`. Create new sets via `POST /api/model-sets`.
