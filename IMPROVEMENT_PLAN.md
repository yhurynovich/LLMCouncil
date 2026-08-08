# Plan: Improve Final Response Accuracy & Correctness in LLM Council

## Executive Summary

The LLM Council's 3-stage deliberation (Stage 1: individual responses → Stage 2: peer ranking → Stage 3: chairman synthesis) has solid architecture but several weaknesses reduce final answer accuracy. This plan addresses the most impactful issues with **backend-only implementation** (frontend-agnostic, callable via API).

---

## Current Architecture Analysis

### Stage 1: Individual Responses
- **Strengths**: Parallel queries, diverse model perspectives, session tracking
- **Weaknesses**: 
  - No structured reasoning extraction (just raw content)
  - No confidence scoring per response
  - Failed models silently excluded without analysis

### Stage 2: Peer Ranking  
- **Strengths**: Anonymous evaluation prevents bias, multiple ranking parsers
- **Weaknesses**:
  - Ranking prompt lacks specific evaluation criteria (accuracy, completeness, clarity, reasoning)
  - Models rank anonymously but don't explain *why* one response is better
  - No weighting by model capability/reliability

### Stage 3: Chairman Synthesis
- **Strengths**: Full context (all responses + rankings)
- **Weaknesses**:
  - Single-pass synthesis without iterative refinement
  - Chairman prompt is generic - no guidance on conflict resolution
  - No citation/attribution to source responses
  - No verification step

---

## Phase 0: Foundation & Observability (P0 — Week 1)

*Prerequisites for validating all subsequent improvements. Backend-only, deployable immediately.*

### 0.1 Enhanced Ranking Prompt with Explicit Criteria
**File**: `backend/council.py` (lines 136-165)

Add structured evaluation dimensions to Stage 2 prompt:
- **Factual Accuracy** (0-10): Correctness of claims, no hallucinations
- **Completeness** (0-10): Addresses all aspects of the question
- **Reasoning Quality** (0-10): Logical flow, evidence use, step-by-step thinking
- **Clarity & Utility** (0-10): Actionable, well-structured, appropriate tone
- **Novelty** (0-10): Unique insights vs. generic boilerplate

Require models to score each dimension before ranking.

### 0.2 Improve Chairman Prompt with Conflict Resolution Rules
**File**: `backend/council.py` (lines 228-243)

Add explicit synthesis guidance:
```
CONFLICT RESOLUTION RULES:
1. When responses disagree on facts: favor the response with better reasoning/evidence
2. When responses complement: synthesize into comprehensive answer
3. When rankings are split: explain why you favor one side
4. Always cite which response(s) support each claim (use "Response R1", "Response R2")
5. Flag any unresolved disagreements explicitly
```

### 0.3 Add Basic Monitoring & Logging (NEW — P0)
**Files**: `backend/council.py`, `backend/main.py`, new `backend/metrics.py`

Instrument all three stages with structured logging:
```python
# Structured log entry per stage
{
  "stage": 1,
  "model": "openrouter/anthropic/claude-sonnet-4-5",
  "latency_ms": 2341,
  "success": true,
  "response_length": 1204,
  "has_reasoning_markers": true,
  "parse_success": true,
  "timestamp": "2026-08-07T..."
}
```

Metrics to track:
- Per-model success/failure rates
- Stage latency distributions (p50, p95, p99)
- Parse failure rates (ranking extraction, JSON parsing)
- Response quality signals (length, reasoning markers, structure)
- Inter-model agreement rates

Expose via `GET /api/metrics/summary` (backend endpoint, no frontend required).

### 0.4 Input Sanitization & Prompt Hardening (NEW — P0)
**Files**: `backend/council.py`, `backend/llm_client.py`

Defend against prompt injection in Stage 2/3:
- Sanitize Stage 1 responses before embedding in ranking prompt (escape delimiters, strip injection patterns)
- Use strict delimiters with unique tokens: `<<<BEGIN_RESPONSE_R1>>>...<<<END_RESPONSE_R1>>>`
- Harden system prompts with "ignore previous instructions" defenses
- Validate response format before parsing (reject malformed structures)

---

## Phase 1: Core Foundation (P1 — Week 2)

*Data-generating capabilities needed before weighted rankings. All backend endpoints.*

### 1.1 Structured Response Format for Stage 1
**Files**: `backend/council.py`, `backend/llm_client.py`

Require models to output structured JSON with graceful fallback:
```json
{
  "answer": "...",
  "reasoning": "...",
  "confidence": 0.85,
  "key_claims": ["claim1", "claim2"],
  "uncertainties": ["area1", "area2"]
}
```

**Implementation details:**
- Add `response_format: { "type": "json_object" }` to OpenAI-compatible requests where supported
- Parse JSON in `stage1_collect_responses`; on failure, fall back to raw text extraction
- Store both parsed and raw in stage1_results for debugging
- Confidence definition in prompt: *"Use 0.0-1.0. Base on token probability or self-consistency. 0.9+ = high certainty, <0.5 = speculative."*

### 1.2 Response Quality Metadata Extraction
**File**: `backend/council.py` (stage1_collect_responses)

Extract and store (for both structured and raw responses):
- Response length (tokens, approximate)
- Presence of reasoning markers ("therefore", "because", "evidence suggests", "step 1", "first")
- Confidence hedging density ("likely", "possibly", "I think", "maybe" vs. definitive claims)
- Structure quality (has headers, lists, code blocks)
- Self-consistency markers ("as mentioned", "to summarize", "in conclusion")

### 1.3 Minimal User Feedback Endpoint (NEW — P1)
**Files**: `backend/main.py`, new `backend/feedback.py`, `data/feedback.json`

Add backend-only API (callable without frontend):
```
POST /api/conversations/{conversation_id}/messages/{message_index}/feedback
{
  "rating": "up" | "down",           // thumbs up/down
  "claim_corrections": [             // optional, claim-level
    {"claim": "X is true", "correction": "X is false because..."}
  ],
  "user_id": "optional_identifier"
}
```

Storage: `data/feedback.json` with conversation_id, message_index, timestamp, rating, corrections.

### 1.4 Low-Confidence / Refusal Handling (NEW — P1)
**Files**: `backend/council.py` (stage1, stage3)

Add threshold-based uncertainty handling:
- Stage 1: If model's structured confidence < 0.4, mark response as `low_confidence: true`
- Stage 3: If aggregate confidence (weighted average of stage1 confidences) < 0.5, chairman must:
  - Explicitly state uncertainty in final answer
  - Not hallucinate definitive answers
  - End with "Confidence: X/10" where X reflects true uncertainty
- Fallback: If ALL models low_confidence, return "Insufficient certainty to provide reliable answer" instead of synthesis

---

## Phase 2: Data Infrastructure (P2 — Week 3)

*Reliability tracking MUST precede weighted rankings (data before consumption).*

### 2.1 Model Reliability Tracking (MOVED FROM P3 → P2)
**Files**: New `backend/reliability.py`, `data/model_reliability.json`, `backend/storage.py` (extend)

Track per-model metrics updated from feedback + automated signals:
```json
{
  "openrouter/anthropic/claude-sonnet-4-5": {
    "agreement_rate": 0.78,           // how often model's ranking matches aggregate
    "factual_accuracy": 0.85,         // from claim corrections + expert eval
    "avg_confidence": 0.82,           // from Stage 1 structured output
    "reasoning_quality": 0.80,        // from metadata extraction
    "latency_p50_ms": 3200,
    "failure_rate": 0.02,
    "sample_count": 147,              // number of evaluations
    "last_updated": "2026-08-07T..."
  }
}
```

**Signal sources (in priority order):**
1. **Explicit user corrections** (claim_corrections from feedback endpoint) — highest weight
2. **Automated fact-check** (RAG verification for high-stakes claims) — medium weight
3. **Expert evaluation** (periodic manual review) — highest weight, low frequency
4. **Inter-model agreement** (ranking correlation) — baseline signal

Update reliability after each council run asynchronously.

### 2.2 Chairman Synthesis with Response-ID Citations
**File**: `backend/council.py` (stage3_synthesize_final)

**Simplified citation approach (per council recommendation):**
- Require response-ID attribution only: `[Response R1]`, `[Response R3]`
- **Do NOT** post-process semantic verification (too error-prone)
- Flag unverifiable claims for human review via metadata
- Prompt addition:
```
CITATION FORMAT (mandatory):
- Every factual claim must cite source: [Response R1], [Response R3]
- Disagreements: "Response R1 claims X, but Response R3 argues Y. I favor R3 because..."
- End with: "Confidence: X/10" (X reflects synthesis certainty, not model confidence)
```

### 2.3 Timeout Budgets & Fallback Logic
**Files**: `backend/council.py`, `backend/main.py`, `backend/llm_client.py`

Define per-stage SLA:
| Stage | Timeout | Fallback |
|-------|---------|----------|
| Stage 1 | 5s/model (configurable) | Exclude timed-out models, continue with responders |
| Stage 2 | 10s | Skip ranking, proceed to Stage 3 with equal weights |
| Stage 3 | 15s | Return best Stage 1 response (highest confidence) |

Configure via `backend/config.py` timeout settings.

### 2.4 Weighted Aggregate Rankings (NOW ENABLED BY P2 RELIABILITY)
**File**: `backend/council.py` (calculate_aggregate_rankings)

Replace simple average with weighted Borda count:
```python
# Weight = reliability.agreement_rate * 0.5 + reliability.factual_accuracy * 0.3 + reliability.reasoning_quality * 0.2
weight = model_reliability[model]["agreement_rate"] * 0.5 + \
         model_reliability[model]["factual_accuracy"] * 0.3 + \
         model_reliability[model]["reasoning_quality"] * 0.2

# Borda count: each ranker gives (N - position) points
# Final score = sum(weight * borda_points) across all rankers
```

Use Borda count (simpler than Schulze, adequate for 5-7 models). Normalize weights to sum=1.

---

## Phase 3: Advanced Orchestration (P3 — Week 4)

### 3.1 Multiple Chairmen with Consensus Mechanism
**Files**: `backend/config.py`, `backend/council.py` (stage3)

Run Stage 3 with 2-3 different chairman models:
- Compare outputs for semantic consistency (not exact match)
- Consensus rule: if ≥2/3 agree on key claims → use majority synthesis
- If split → flag discrepancies, return all versions with "Chairman Disagreement" notice
- Cost/latency: linear increase, bounded by Stage 3 timeout

### 3.2 Diversity Audit in Ranking Prompt
**File**: `backend/council.py` (stage2 prompt)

Add evaluation dimension: "Independence" — does this response offer unique perspective vs. others? Penalize convergent boilerplate.

### 3.3 Cost & Latency Budget Enforcement
**Files**: `backend/main.py`, `backend/council.py`

Track cumulative cost/latency per request:
- Per-model API cost estimates (from provider pricing)
- Cumulative latency check before each stage
- If budget exceeded → activate fallback chain

---

## Phase 4: Enhancement (P4 — Ongoing)

### 4.1 RAG Verification (Optional, Timeout-Limited)
**Files**: `backend/council.py`, `backend/web_search.py`

For high-stakes factual claims in chairman output:
- Trigger `search_web` tool for top 3 claims (configurable)
- Timeout: 3s per search, 10s total
- Attach verification status: `verified`, `contradicted`, `no_evidence`
- Reduce confidence for unverified claims

### 4.2 Advanced Feedback (Claim-Level Corrections)
**Files**: `backend/feedback.py`, `backend/main.py`

Extend feedback endpoint with structured claim corrections that feed directly into reliability signals.

### 4.3 A/B Testing Framework
**Files**: `backend/main.py`, new `backend/experiments.py`

Backend-only experiment assignment:
- Random assignment per conversation (stored in session)
- Track metrics per variant
- Expose via `GET /api/experiments/status`

---

## Implementation Priority Matrix (Corrected)

| Improvement | Effort | Impact | Dependencies | Priority |
|-------------|--------|--------|--------------|----------|
| Enhanced ranking criteria | Low | High | None | **P0** |
| Chairman conflict resolution | Low | High | None | **P0** |
| Basic monitoring/logging | Low | High | None | **P0** (NEW) |
| Prompt injection hardening | Low | Critical | None | **P0** (NEW) |
| Structured Stage 1 format | Medium | High | llm_client.py | **P1** |
| Response quality metadata | Medium | Medium | None | **P1** |
| Minimal feedback endpoint | Low | High | Backend only | **P1** (NEW) |
| Low-confidence/refusal handling | Medium | High | Structured Stage 1 | **P1** (NEW) |
| **Model reliability tracking** | **Medium** | **High** | **Feedback endpoint** | **P2** (MOVED from P3) |
| Chairman citations | Medium | High | Structured Stage 1 | **P2** |
| Timeout budgets & fallbacks | Low | High | None | **P2** |
| Weighted aggregate rankings | Medium | High | Reliability data | **P3** (MOVED from P2) |
| Multiple chairmen | Medium | Medium | Config changes | **P3** |
| Diversity audit | Low | Medium | Ranking prompt | **P3** |
| RAG verification | High | High | Search tool | **P4** |
| Advanced feedback | Medium | Medium | Basic feedback | **P4** |
| A/B testing | Medium | Medium | Backend only | **P4** |

**Key Principle**: Data-generating capabilities (feedback, reliability tracking) MUST precede data-consuming capabilities (weighted rankings).

---

## Testing Strategy

### Unit Tests (backend/)
- `parse_ranking_from_text`: All format variations
- `calculate_aggregate_rankings`: Weighted Borda vs unweighted
- Citation extraction from chairman output
- Confidence threshold logic
- Feedback endpoint validation

### Integration Tests
- Full 3-stage run with known question/answer pairs
- Compare council output vs single best model
- Measure accuracy improvement
- Feedback → reliability update → weighted ranking flow

### Backend-Only A/B Testing
- Deploy improved version alongside current (feature flag)
- Random assignment per conversation (stored in session)
- Track: accuracy (expert eval), user satisfaction (feedback), hallucination rate
- All measurable without frontend changes

---

## Rollout Plan (Corrected)

1. **Week 1 (P0)**: Ranking criteria, Chairman rules, **monitoring**, **prompt hardening**
2. **Week 2 (P1)**: Structured Stage 1, **feedback endpoint**, **refusal handling**, metadata
3. **Week 3 (P2)**: **Reliability tracking**, Chairman citations, timeout budgets
4. **Week 4 (P3)**: Weighted rankings, Multi-chairman, Diversity audit
5. **Ongoing (P4)**: RAG verification, Advanced feedback, A/B testing

---

## Success Metrics

| Metric | Current | Target | Measurement |
|--------|---------|--------|-------------|
| Factual accuracy (expert eval) | ~70% | >90% | Quarterly expert review |
| User satisfaction (thumbs up) | Unknown | >80% | Feedback endpoint analytics |
| Hallucination rate | Unknown | <5% | RAG verification + expert spot-check |
| Inter-model agreement | Unknown | >75% | Ranking correlation analysis |
| Stage parse success rate | Unknown | >99% | Monitoring dashboard |
| Low-confidence detection | N/A | >90% recall | Manual validation sample |

---

## Risk Mitigation (Expanded)

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Prompt changes break parsing | Medium | Medium | Comprehensive regex test suite + CI |
| Structured output fails | High | Low | Graceful fallback to raw text (tested) |
| Increased latency | Medium | Medium | Parallel execution, per-stage timeouts, caching |
| Over-fitting to benchmarks | Low | High | Diverse test questions, human eval, A/B |
| **Chairman hallucination** | **Medium** | **High** | Restrict to synthesis-only; RAG fact-check for high-stakes |
| **Prompt injection (S2/S3)** | **Medium** | **Critical** | Sanitize outputs; strict delimiters; hardened prompts |
| **Model collusion/bias** | **High** | **Medium** | Diversity dimension; periodic similarity audit |
| **Latency budget exceeded** | **High** | **High** | Per-stage timeouts; fallback chain; async where possible |
| **Privacy/proprietary data** | Low | High | Anonymize reliability data; no raw response storage in metrics |
| **Circular reliability** | Medium | High | Ground truth sources: user corrections > automated > expert |

---

## Files to Modify / Create (Backend Only)

### Primary (Core Logic)
- `backend/council.py` — Core orchestration, prompts, parsing
- `backend/llm_client.py` — Structured output requests, response parsing
- `backend/main.py` — API endpoints (feedback, metrics, health)

### Secondary (Config & Storage)
- `backend/config.py` — Timeout budgets, model set configuration
- `backend/storage.py` — Reliability data, feedback persistence

### New Modules
- `backend/metrics.py` — Structured logging, metrics collection
- `backend/reliability.py` — Model reliability tracking, weight computation
- `backend/feedback.py` — Feedback endpoint, storage, signal processing
- `backend/metrics.py` — `/api/metrics/summary` endpoint

### Data Files
- `data/model_reliability.json` — Persistent per-model metrics
- `data/feedback.json` — User feedback history
- `data/metrics/` — Rotating log files (JSONL)

---

## Backend-Only Design Principle

All improvements implemented as **backend API endpoints and internal logic**. No frontend changes required. The council can be invoked via:
- `POST /v1/chat/completions` (OpenAI-compatible)
- `POST /api/conversations/{id}/message/stream` (SSE)
- `POST /api/conversations/{id}/messages/{idx}/feedback` (feedback)
- `GET /api/metrics/summary` (observability)

This ensures the improvements work regardless of client (web UI, CLI, Hermes, custom integrations).