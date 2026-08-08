"""3-stage LLM Council orchestration."""

import re
import uuid
import time
import json
from collections import defaultdict
from typing import List, Dict, Any, Tuple, Optional, Union
from .llm_client import query_models_parallel, query_model
from .config import get_council_models, get_chairman_model, get_council_models_sync, get_chairman_model_sync
from .uploads import read_file_content, get_image_base64
from .storage import get_or_create_model_session_async
from .metrics import (
    log_stage_metric,
    log_ranking_metric,
    log_synthesis_metric,
)
from .reliability import (
    compute_weight,
    normalize_weights,
    update_from_stage1,
    update_from_stage2,
    update_from_synthesis,
)

FINAL_RANKING_TOKEN = "FINAL RANKING:"
MAX_RESPONSE_CHARS = 3000

# Unique delimiters for prompt injection protection
RESPONSE_START_DELIMITER = "<<<BEGIN_RESPONSE_"
RESPONSE_END_DELIMITER = ">>>"


def sanitize_for_prompt(text: str) -> str:
    """
    Sanitize model output before embedding in prompts to prevent injection.
    Escapes delimiter patterns and strips potential injection attempts.
    """
    if not text:
        return ""
    # Escape our unique delimiters
    text = text.replace("<<<BEGIN_RESPONSE_", "\\<<<BEGIN_RESPONSE_")
    text = text.replace(">>>", "\\>>>")
    # Strip common injection patterns
    injection_patterns = [
        r"ignore\s+(?:previous|above|all)\s+instructions?",
        r"disregard\s+(?:previous|above|all)\s+instructions?",
        r"forget\s+(?:previous|above|all)\s+instructions?",
        r"system\s*[:\-]\s*",
        r"assistant\s*[:\-]\s*",
        r"user\s*[:\-]\s*",
        r"<\|.*?\|>",  # Special tokens
    ]
    for pattern in injection_patterns:
        text = re.sub(pattern, "[INJECTION_BLOCKED]", text, flags=re.IGNORECASE)
    return text


def build_response_block(label: str, content: str) -> str:
    """Build a safely delimited response block for embedding in prompts."""
    sanitized = sanitize_for_prompt(content)
    return f"{RESPONSE_START_DELIMITER}{label}>>>\n{sanitized}\n{RESPONSE_START_DELIMITER}{label}END>>>"


def build_message_with_files(user_query: str, files: list) -> Union[str, List[Dict[str, Any]]]:
    """Prepends file content to user query. Returns multimodal content for vision-capable models."""
    if not files:
        return user_query

    content_parts = []

    # Add text files first
    for f in files:
        if f.type == "text":
            content = read_file_content(f.file_id, f.ext)
            content_parts.append({
                "type": "text",
                "text": f"File: {f.filename}\n```\n{content}\n```"
            })

    # Add the user query as text
    content_parts.append({
        "type": "text",
        "text": user_query
    })

    # Add images
    for f in files:
        if f.type == "image":
            b64 = get_image_base64(f.file_id, f.ext)
            if b64:
                mime = f"image/{f.ext.lstrip('.')}"
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"}
                })

    # If only text content, return as string for backwards compatibility
    if all(p["type"] == "text" for p in content_parts):
        return "\n\n".join(p["text"] for p in content_parts)

    return content_parts


async def stage1_collect_responses(
    messages: List[Dict[str, Any]],
    council_models: Optional[List[str]] = None,
    files: Optional[list] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    conversation_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    # Handle files if present (multimodal content)
    if files:
        content = build_message_with_files(messages[-1].get("content", ""), files)
        new_messages = messages[:-1] + [{"role": "user", "content": content}]
    else:
        new_messages = messages
    
    models = council_models if council_models is not None else await get_council_models()
    
    # Get or create session IDs for each model
    session_ids = {}
    if conversation_id:
        for model in models:
            session_ids[model] = await get_or_create_model_session_async(conversation_id, model)
    
    responses = await query_models_parallel(models, new_messages, temperature=temperature, max_tokens=max_tokens, session_ids=session_ids)

    stage1_results = []
    for model, response in responses.items():
        start_time = time.perf_counter()
        if response is not None and "error" not in response:
            content = response.get('content', '')
            response_time = response.get('response_time') or (time.perf_counter() - start_time)
            # Check for reasoning markers
            reasoning_markers = any(marker in content.lower() for marker in [
                "therefore", "because", "evidence suggests", "step 1", "first,",
                "reasoning:", "analysis:", "conclusion:"
            ])
            # Try to extract structured JSON (confidence, key_claims, uncertainties)
            confidence = None
            key_claims = []
            uncertainties = []
            
            def try_parse_json(text: str) -> Optional[dict]:
                """Try to parse text as JSON, return dict if valid and has expected structure."""
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        return parsed
                except (json.JSONDecodeError, ValueError):
                    pass
                return None
            
            # Strategy 1: Try parsing entire response as JSON
            parsed = try_parse_json(content)
            if parsed:
                confidence = parsed.get('confidence')
                key_claims = parsed.get('key_claims', []) or []
                uncertainties = parsed.get('uncertainties', []) or []
            else:
                # Strategy 2: Look for JSON object with expected fields
                # Find all JSON-like objects in the response
                import re
                # Match JSON objects that might contain confidence/key_claims/uncertainties
                json_pattern = r'\{[^{}]*(?:"confidence"|"key_claims"|"uncertainties")[^{}]*\}'
                matches = re.findall(json_pattern, content)
                for match in matches:
                    parsed = try_parse_json(match)
                    if parsed and ('confidence' in parsed or 'key_claims' in parsed or 'uncertainties' in parsed):
                        confidence = parsed.get('confidence')
                        key_claims = parsed.get('key_claims', []) or []
                        uncertainties = parsed.get('uncertainties', []) or []
                        break
                
                # Strategy 3: Fallback to original method (first { to last })
                if confidence is None and not key_claims and not uncertainties:
                    json_start = content.find('{')
                    json_end = content.rfind('}') + 1
                    if json_start >= 0 and json_end > json_start:
                        potential_json = content[json_start:json_end]
                        parsed = try_parse_json(potential_json)
                        if parsed:
                            confidence = parsed.get('confidence')
                            key_claims = parsed.get('key_claims', []) or []
                            uncertainties = parsed.get('uncertainties', []) or []
            
            # Low confidence flag
            low_confidence = confidence is not None and confidence < 0.4
            
            log_stage_metric(
                stage=1,
                model=model,
                success=True,
                latency_ms=response_time * 1000 if isinstance(response_time, float) else 0,
                response_length=len(content),
                has_reasoning_markers=reasoning_markers,
                parse_success=True,
            )
            # Update reliability from Stage 1
            update_from_stage1(
                model=model,
                confidence=confidence,
                has_reasoning_markers=reasoning_markers,
                response_length=len(content),
                latency_ms=response_time * 1000 if isinstance(response_time, float) else 0,
                success=True,
            )
            stage1_results.append({
                "model": model,
                "response": content,
                "response_time": response_time,
                "confidence": confidence,
                "key_claims": key_claims,
                "uncertainties": uncertainties,
                "low_confidence": low_confidence,
            })
        else:
            error_msg = response.get("error", "Model failed to respond") if response else "Model failed to respond"
            log_stage_metric(
                stage=1,
                model=model,
                success=False,
                latency_ms=(time.perf_counter() - start_time) * 1000,
                error=error_msg,
            )
            # Update reliability for failed response
            update_from_stage1(
                model=model,
                confidence=None,
                has_reasoning_markers=False,
                response_length=0,
                latency_ms=(time.perf_counter() - start_time) * 1000,
                success=False,
            )
            stage1_results.append({
                "model": model,
                "response": None,
                "error": error_msg,
            })
    return stage1_results, session_ids


async def stage2_collect_rankings(
    messages: List[Dict[str, Any]],
    stage1_results: List[Dict[str, Any]],
    council_models: Optional[List[str]] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    conversation_id: Optional[str] = None,
    session_ids: Optional[Dict[str, str]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, str], Dict[str, str]]:
    # Filter out failed models (those with response=None)
    successful_results = [r for r in stage1_results if r.get('response') is not None]

    # Use R1, R2, ... labels (scales beyond 26 models)
    labels = [f"R{i + 1}" for i in range(len(successful_results))]

    label_to_model = {
        f"Response {label}": result['model']
        for label, result in zip(labels, successful_results)
    }

    # Truncate long responses to avoid context overflow
    def _truncate(text: str) -> str:
        return text[:MAX_RESPONSE_CHARS] + "\n[truncated]" if len(text) > MAX_RESPONSE_CHARS else text

    # Build sanitized, delimited response blocks for injection protection
    responses_text = "\n\n".join([
        build_response_block(label, _truncate(result['response']))
        for label, result in zip(labels, successful_results)
    ])

    # Extract the user query from the messages for the ranking prompt
    # Use the LAST user message (most recent) for context
    user_query = ""
    for msg in reversed(messages):
        if msg["role"] == "user":
            user_query = msg.get("content", "")
            break

    ranking_prompt = f"""You are evaluating different responses to the following question:

Question: {user_query}

Here are the responses from different models (anonymized):

{responses_text}

Your task:
1. First, evaluate each response individually across these FIVE dimensions (score each 0-10):
   - FACTUAL ACCURACY: Correctness of claims, no hallucinations, proper evidence
   - COMPLETENESS: Addresses all aspects of the question, no missing key points
   - REASONING QUALITY: Logical flow, step-by-step thinking, evidence use
   - CLARITY & UTILITY: Actionable, well-structured, appropriate tone, useful
   - NOVELTY: Unique insights vs. generic boilerplate, added value

   For each response, provide a brief assessment per dimension with scores.

2. Then, at the very end of your response, provide a final ranking.

IMPORTANT: Your final ranking MUST be formatted EXACTLY as follows:
- Start with the line "FINAL RANKING:" (all caps, with colon)
- Then list the responses from best to worst as a numbered list
- Each line should be: number, period, space, then ONLY the response label (e.g., "1. Response R1")
- Do not add any other text or explanations in the ranking section

Example of the correct format for your ENTIRE response:

Response R1:
  Factual Accuracy: 8/10 - Correct claims, minor omission
  Completeness: 7/10 - Misses edge case
  Reasoning Quality: 8/10 - Clear logic
  Clarity & Utility: 9/10 - Well structured
  Novelty: 6/10 - Standard approach

Response R2:
  Factual Accuracy: 9/10 - Well evidenced
  Completeness: 9/10 - Comprehensive
  Reasoning Quality: 9/10 - Excellent step-by-step
  Clarity & Utility: 8/10 - Good structure
  Novelty: 7/10 - Some unique insights

FINAL RANKING:
1. Response R2
2. Response R1

Now provide your evaluation and ranking:"""

    ranking_messages = [{"role": "user", "content": ranking_prompt}]
    models = council_models if council_models is not None else await get_council_models()
    
    # Pass session_ids for ranking models
    ranking_session_ids = {}
    if conversation_id and session_ids:
        for model in models:
            if model in session_ids:
                ranking_session_ids[model] = session_ids[model]
            else:
                # Create new session for ranking if needed
                ranking_session_ids[model] = await get_or_create_model_session_async(conversation_id, model)
    
    responses = await query_models_parallel(models, ranking_messages, temperature=temperature, max_tokens=max_tokens, session_ids=ranking_session_ids)

    stage2_results = []
    parsed_rankings = {}
    for model, response in responses.items():
        if response is not None:
            full_text = response.get('content', '')
            parsed = parse_ranking_from_text(full_text)
            log_ranking_metric(
                model=model,
                success=True,
                latency_ms=response.get('response_time', 0) * 1000 if response.get('response_time') else 0,
                parsed_ranking_count=len(parsed),
                parse_success=len(parsed) > 0,
            )
            stage2_results.append({
                "model": model,
                "ranking": full_text,
                "parsed_ranking": parsed
            })
            if parsed:
                parsed_rankings[model] = parsed
        else:
            log_ranking_metric(
                model=model,
                success=False,
                latency_ms=0,
                error=response.get('error', 'Unknown error') if response else 'No response',
            )

    # Compute aggregate ranking for reliability update
    aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
    aggregate_order = [item['model'] for item in aggregate_rankings if item['rankings_count'] > 0]
    
    # Update reliability from Stage 2 (agreement with aggregate)
    for model, parsed in parsed_rankings.items():
        update_from_stage2(
            model=model,
            parsed_ranking=parsed,
            aggregate_ranking=aggregate_order,
            success=True,
            latency_ms=0,
        )

    return stage2_results, label_to_model, ranking_session_ids


async def stage3_synthesize_final(
    messages: List[Dict[str, Any]],
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]],
    chairman_model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    conversation_id: Optional[str] = None,
    session_ids: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    chair = chairman_model if chairman_model is not None else await get_chairman_model()

    # Filter out failed models (those with response=None)
    successful_results = [r for r in stage1_results if r.get('response') is not None]

    # Build sanitized Stage 1 responses for chairman (with unique labels for citation)
    stage1_labels = [f"R{i + 1}" for i in range(len(successful_results))]
    stage1_text = "\n\n".join([
        build_response_block(label, result['response'])
        for label, result in zip(stage1_labels, successful_results)
    ])
    stage2_text = "\n\n".join([
        f"Model: {result['model']}\nRanking: {result['ranking']}"
        for result in stage2_results
    ])

    # Extract the user query from the messages
    # Use the LAST user message (most recent) for context
    user_query = ""
    for msg in reversed(messages):
        if msg["role"] == "user":
            user_query = msg.get("content", "")
            break

    # Compute aggregate confidence from Stage 1
    confidences = [r.get('confidence') for r in successful_results if r.get('confidence') is not None]
    aggregate_confidence = sum(confidences) / len(confidences) if confidences else None
    all_low_confidence = all(r.get('low_confidence', False) for r in successful_results)

    # If all models are low confidence or aggregate is very low, return early with uncertainty notice
    if all_low_confidence or (aggregate_confidence is not None and aggregate_confidence < 0.3):
        low_conf_msg = (
            "Insufficient certainty to provide a reliable answer. "
            "All responding models indicated low confidence in their responses. "
            "Please consider rephrasing the question or providing more context."
        )
        return {
            "model": chair if chair else "chairman",
            "response": low_conf_msg,
            "low_confidence": True,
            "aggregate_confidence": aggregate_confidence,
        }

    chairman_prompt = f"""You are the Chairman of an LLM Council. Multiple AI models have provided responses to a user's question, and then ranked each other's responses.

Original Question: {user_query}

STAGE 1 - Individual Responses:
{stage1_text}

STAGE 2 - Peer Rankings:
{stage2_text}

Your task as Chairman is to synthesize all of this information into a single, comprehensive, accurate answer to the user's original question. Consider:
- The individual responses and their insights
- The peer rankings and what they reveal about response quality
- Any patterns of agreement or disagreement

CONFLICT RESOLUTION RULES:
1. When responses disagree on facts: favor the response with better reasoning/evidence
2. When responses complement: synthesize into comprehensive answer
3. When rankings are split: explain why you favor one side
4. Always cite which response(s) support each claim (use "Response R1", "Response R2")
5. Flag any unresolved disagreements explicitly

CITATION FORMAT (mandatory):
- Every factual claim must cite source: [Response R1], [Response R3]
- Disagreements: "Response R1 claims X, but Response R3 argues Y. I favor R3 because..."
- End with: "Confidence: X/10" (X reflects synthesis certainty, not model confidence)

Provide a clear, well-reasoned final answer that represents the council's collective wisdom:"""

    chairman_messages = [{"role": "user", "content": chairman_prompt}]
    
    # Get session_id for chairman
    chairman_session_id = None
    if conversation_id and session_ids and chair in session_ids:
        chairman_session_id = session_ids[chair]
    elif conversation_id:
        chairman_session_id = await get_or_create_model_session_async(conversation_id, chair)
    
    start_time = time.perf_counter()
    response = await query_model(chair, chairman_messages, temperature=temperature, max_tokens=max_tokens, session_id=chairman_session_id)
    latency_ms = (time.perf_counter() - start_time) * 1000

    if response is None:
        log_synthesis_metric(
            model=chair,
            success=False,
            latency_ms=latency_ms,
            error="No response from chairman model",
        )
        return {"model": chair, "response": "Error: Unable to generate final synthesis."}

    content = response.get('content', '')
    # Check for citations
    has_citations = bool(re.search(r'\[Response R\d+\]', content))
    # Extract confidence score if present
    confidence_score = None
    conf_match = re.search(r'Confidence:\s*(\d+(?:\.\d+)?)', content)
    if conf_match:
        try:
            confidence_score = float(conf_match.group(1))
        except ValueError:
            pass

    log_synthesis_metric(
        model=chair,
        success=True,
        latency_ms=latency_ms,
        response_length=len(content),
        has_citations=has_citations,
        confidence_score=confidence_score,
    )

    return {"model": chair, "response": content}


def parse_ranking_from_text(ranking_text: str) -> List[str]:
    """Parse ranking from text. Supports multiple formats for resilience."""
    if FINAL_RANKING_TOKEN not in ranking_text:
        return []

    parts = ranking_text.split(FINAL_RANKING_TOKEN, 1)
    if len(parts) < 2:
        return []

    ranking_section = parts[1]
    
    # Try multiple parsing patterns in order of preference
    patterns = [
        # Standard: "1. Response R1" or "1. Response R10"
        r'^\d+\.\s*(Response R\d+)',
        # With parentheses: "1) Response R1"
        r'^\d+\)\s*(Response R\d+)',
        # Dash format: "- Response R1"
        r'^[-*]\s*(Response R\d+)',
        # Plain: "Response R1" on separate lines
        r'^(Response R\d+)',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, ranking_section, re.MULTILINE)
        if matches:
            return matches
    
    return []


def calculate_aggregate_rankings(
    stage2_results: List[Dict[str, Any]],
    label_to_model: Dict[str, str],
    council_models: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Calculate weighted aggregate rankings using Borda count with reliability weights."""
    model_positions: dict[str, list[int]] = defaultdict(list)
    ranker_models = set()

    for ranking in stage2_results:
        # Reuse stored parsed_ranking if available, else parse
        parsed = ranking.get('parsed_ranking') or parse_ranking_from_text(ranking['ranking'])
        if not parsed:
            continue
        ranker_model = ranking['model']
        ranker_models.add(ranker_model)
        for position, label in enumerate(parsed, start=1):
            if label in label_to_model:
                model_positions[label_to_model[label]].append(position)

    # Compute reliability weights for ranker models
    if council_models:
        # Normalize weights only for models that actually ranked
        ranker_weights = normalize_weights(list(ranker_models))
    else:
        ranker_weights = {m: 1.0 for m in ranker_models}

    aggregate = []
    for model, positions in model_positions.items():
        if not positions:
            continue
        # Weighted Borda count: each ranker gives (N - position) points weighted by reliability
        n_models = len(label_to_model)
        weighted_score = 0.0
        total_weight = 0.0
        
        # We need to know which ranker gave which position
        # Re-iterate to compute weighted Borda
        for ranking in stage2_results:
            ranker = ranking['model']
            weight = ranker_weights.get(ranker, 1.0)
            parsed = ranking.get('parsed_ranking') or parse_ranking_from_text(ranking['ranking'])
            for position, label in enumerate(parsed, start=1):
                if label in label_to_model and label_to_model[label] == model:
                    # Borda score: (n_models - position) points
                    borda_points = n_models - position
                    weighted_score += weight * borda_points
                    total_weight += weight
        
        avg_borda = weighted_score / total_weight if total_weight > 0 else 0
        # Convert to average rank for compatibility (lower = better)
        # Approximate: average_rank ≈ n_models + 1 - avg_borda
        avg_rank = round(n_models + 1 - avg_borda, 2)
        
        aggregate.append({
            "model": model,
            "average_rank": avg_rank,
            "rankings_count": len(positions),
            "weighted_score": round(avg_borda, 2),
        })

    # Include models with zero votes at the bottom
    voted_models = {item['model'] for item in aggregate}
    for model in label_to_model.values():
        if model not in voted_models:
            aggregate.append({
                "model": model,
                "average_rank": float('inf'),
                "rankings_count": 0,
                "weighted_score": 0.0,
            })

    aggregate.sort(key=lambda x: x['average_rank'])
    return aggregate


async def generate_conversation_title(user_query: str) -> str:
    title_prompt = f"""Generate a very short title (3-5 words maximum) that summarizes the following question.
The title should be concise and descriptive. Do not use quotes or punctuation in the title.

Question: {user_query}

Title:"""

    messages = [{"role": "user", "content": title_prompt}]
    response = await query_model(await get_chairman_model(), messages, timeout=30.0)

    if response is None:
        return "New Conversation"

    title = response.get('content', 'New Conversation').strip().strip('"\'')
    max_len = 50
    return title[:max_len - 3] + "..." if len(title) > max_len else title


async def run_full_council(messages: List[Dict[str, Any]], council_models: Optional[List[str]] = None, temperature: Optional[float] = None, max_tokens: Optional[int] = None, conversation_id: Optional[str] = None) -> Tuple[List, List, Dict, Dict]:
    stage1_results, session_ids = await stage1_collect_responses(messages, council_models=council_models, temperature=temperature, max_tokens=max_tokens, conversation_id=conversation_id)

    if not stage1_results:
        return [], [], {
            "model": "error",
            "response": "All models failed to respond. Please try again."
        }, {}

    responding_models = [r["model"] for r in stage1_results if r.get("response") is not None]
    stage2_results, label_to_model, ranking_session_ids = await stage2_collect_rankings(messages, stage1_results, responding_models, temperature=temperature, max_tokens=max_tokens, conversation_id=conversation_id, session_ids=session_ids)
    
    # Merge session_ids (include ranking sessions)
    all_session_ids = {**session_ids, **ranking_session_ids}
    
    aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
    stage3_result = await stage3_synthesize_final(messages, stage1_results, stage2_results, temperature=temperature, max_tokens=max_tokens, conversation_id=conversation_id, session_ids=all_session_ids)

    return stage1_results, stage2_results, stage3_result, {
        "label_to_model": label_to_model,
        "aggregate_rankings": aggregate_rankings
    }
