"""3-stage LLM Council orchestration."""

from typing import List, Dict, Any, Tuple, Optional
from .llm_client import query_models_parallel, query_model
from .config import COUNCIL_MODELS, CHAIRMAN_MODEL, get_chairman_model
from .uploads import read_file_content, get_image_base64


def build_message_with_files(user_query: str, files: list) -> tuple[str, list]:
    """Prepends file content to user query. Returns (text_message, image_urls)."""
    if not files:
        return user_query, []

    parts = []
    image_urls = []

    for f in files:
        if f.type == "text":
            content = read_file_content(f.file_id, f.ext)
            parts.append(f"File: {f.filename}\n```\n{content}\n```")
        elif f.type == "image":
            b64 = get_image_base64(f.file_id, f.ext)
            if b64:
                mime = f"image/{f.ext.lstrip('.')}"
                image_urls.append(f"data:{mime};base64,{b64}")

    if parts:
        return "\n\n".join(parts) + "\n\n" + user_query, image_urls
    return user_query, image_urls


async def stage1_collect_responses(
    user_query: str,
    council_models: Optional[List[str]] = None,
    files: Optional[list] = None,
) -> List[Dict[str, Any]]:
    text_message, image_urls = build_message_with_files(user_query, files or [])
    messages = [{"role": "user", "content": text_message}]
    models = council_models if council_models is not None else COUNCIL_MODELS
    responses = await query_models_parallel(models, messages)

    stage1_results = []
    for model, response in responses.items():
        if response is not None:
            stage1_results.append({
                "model": model,
                "response": response.get('content', ''),
                "response_time": response.get('response_time'),
            })
        else:
            stage1_results.append({
                "model": model,
                "response": None,
                "error": "Model failed to respond",
            })
    return stage1_results


async def stage2_collect_rankings(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    council_models: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    labels = [chr(65 + i) for i in range(len(stage1_results))]

    label_to_model = {
        f"Response {label}": result['model']
        for label, result in zip(labels, stage1_results)
    }

    responses_text = "\n\n".join([
        f"Response {label}:\n{result['response']}"
        for label, result in zip(labels, stage1_results)
    ])

    ranking_prompt = f"""You are evaluating different responses to the following question:

Question: {user_query}

Here are the responses from different models (anonymized):

{responses_text}

Your task:
1. First, evaluate each response individually. For each response, explain what it does well and what it does poorly.
2. Then, at the very end of your response, provide a final ranking.

IMPORTANT: Your final ranking MUST be formatted EXACTLY as follows:
- Start with the line "FINAL RANKING:" (all caps, with colon)
- Then list the responses from best to worst as a numbered list
- Each line should be: number, period, space, then ONLY the response label (e.g., "1. Response A")
- Do not add any other text or explanations in the ranking section

Example of the correct format for your ENTIRE response:

Response A provides good detail on X but misses Y...
Response B is accurate but lacks depth on Z...
Response C offers the most comprehensive answer...

FINAL RANKING:
1. Response C
2. Response A
3. Response B

Now provide your evaluation and ranking:"""

    messages = [{"role": "user", "content": ranking_prompt}]
    models = council_models if council_models is not None else COUNCIL_MODELS
    responses = await query_models_parallel(models, messages)

    stage2_results = []
    for model, response in responses.items():
        if response is not None:
            full_text = response.get('content', '')
            parsed = parse_ranking_from_text(full_text)
            stage2_results.append({
                "model": model,
                "ranking": full_text,
                "parsed_ranking": parsed
            })

    return stage2_results, label_to_model


async def stage3_synthesize_final(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]],
    chairman_model: Optional[str] = None,
) -> Dict[str, Any]:
    chair = chairman_model if chairman_model is not None else CHAIRMAN_MODEL

    stage1_text = "\n\n".join([
        f"Model: {result['model']}\nResponse: {result['response']}"
        for result in stage1_results
    ])
    stage2_text = "\n\n".join([
        f"Model: {result['model']}\nRanking: {result['ranking']}"
        for result in stage2_results
    ])

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

Provide a clear, well-reasoned final answer that represents the council's collective wisdom:"""

    messages = [{"role": "user", "content": chairman_prompt}]
    response = await query_model(chair, messages)

    if response is None:
        return {"model": chair, "response": "Error: Unable to generate final synthesis."}

    return {"model": chair, "response": response.get('content', '')}


def parse_ranking_from_text(ranking_text: str) -> List[str]:
    import re
    if "FINAL RANKING:" in ranking_text:
        parts = ranking_text.split("FINAL RANKING:")
        if len(parts) >= 2:
            ranking_section = parts[1]
            numbered_matches = re.findall(r'\d+\.\s*Response [A-Z]', ranking_section)
            if numbered_matches:
                return [re.search(r'Response [A-Z]', m).group() for m in numbered_matches]
            matches = re.findall(r'Response [A-Z]', ranking_section)
            return matches
    return re.findall(r'Response [A-Z]', ranking_text)


def calculate_aggregate_rankings(
    stage2_results: List[Dict[str, Any]],
    label_to_model: Dict[str, str]
) -> List[Dict[str, Any]]:
    from collections import defaultdict
    model_positions = defaultdict(list)

    for ranking in stage2_results:
        parsed_ranking = parse_ranking_from_text(ranking['ranking'])
        for position, label in enumerate(parsed_ranking, start=1):
            if label in label_to_model:
                model_positions[label_to_model[label]].append(position)

    aggregate = []
    for model, positions in model_positions.items():
        if positions:
            aggregate.append({
                "model": model,
                "average_rank": round(sum(positions) / len(positions), 2),
                "rankings_count": len(positions)
            })

    aggregate.sort(key=lambda x: x['average_rank'])
    return aggregate


async def generate_conversation_title(user_query: str) -> str:
    title_prompt = f"""Generate a very short title (3-5 words maximum) that summarizes the following question.
The title should be concise and descriptive. Do not use quotes or punctuation in the title.

Question: {user_query}

Title:"""

    messages = [{"role": "user", "content": title_prompt}]
    response = await query_model(get_chairman_model(), messages, timeout=30.0)

    if response is None:
        return "New Conversation"

    title = response.get('content', 'New Conversation').strip().strip('"\'')
    return title[:47] + "..." if len(title) > 50 else title


async def run_full_council(user_query: str) -> Tuple[List, List, Dict, Dict]:
    stage1_results = await stage1_collect_responses(user_query)

    if not stage1_results:
        return [], [], {
            "model": "error",
            "response": "All models failed to respond. Please try again."
        }, {}

    responding_models = [r["model"] for r in stage1_results]
    stage2_results, label_to_model = await stage2_collect_rankings(user_query, stage1_results, responding_models)
    aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
    stage3_result = await stage3_synthesize_final(user_query, stage1_results, stage2_results)

    return stage1_results, stage2_results, stage3_result, {
        "label_to_model": label_to_model,
        "aggregate_rankings": aggregate_rankings
    }
