"""Model reliability tracking for weighted council rankings."""
import json
import os
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from pathlib import Path
from collections import defaultdict

RELIABILITY_FILE = Path("data/model_reliability.json")
RELIABILITY_FILE.parent.mkdir(parents=True, exist_ok=True)

_reliability_lock = threading.Lock()

# Default reliability template
DEFAULT_RELIABILITY = {
    "agreement_rate": 0.5,        # how often model's ranking matches aggregate
    "factual_accuracy": 0.5,      # from claim corrections + expert eval
    "avg_confidence": 0.5,        # from Stage 1 structured output
    "reasoning_quality": 0.5,     # from metadata extraction
    "latency_p50_ms": 5000,
    "failure_rate": 0.1,
    "sample_count": 0,
    "last_updated": None,
}


def _load_reliability() -> Dict[str, Any]:
    """Load reliability data from file."""
    if not RELIABILITY_FILE.exists():
        return {}
    try:
        with open(RELIABILITY_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_reliability(data: Dict[str, Any]) -> None:
    """Save reliability data to file atomically."""
    tmp = RELIABILITY_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, RELIABILITY_FILE)


def get_reliability(model: str) -> Dict[str, Any]:
    """Get reliability metrics for a model (with defaults)."""
    with _reliability_lock:
        data = _load_reliability()
        if model not in data:
            data[model] = DEFAULT_RELIABILITY.copy()
            data[model]["last_updated"] = datetime.utcnow().isoformat() + "Z"
            _save_reliability(data)
        return data[model].copy()


def get_all_reliability() -> Dict[str, Any]:
    """Get reliability metrics for all models."""
    with _reliability_lock:
        data = _load_reliability()
        # Ensure all models have defaults
        for model in list(data.keys()):
            for key, val in DEFAULT_RELIABILITY.items():
                if key not in data[model]:
                    data[model][key] = val
        return {k: v.copy() for k, v in data.items()}


def compute_weight(model: str) -> float:
    """
    Compute reliability weight for a model.
    Weight = agreement_rate * 0.5 + factual_accuracy * 0.3 + reasoning_quality * 0.2
    Normalized so weights sum to 1 across all models in a council.
    """
    rel = get_reliability(model)
    weight = (
        rel["agreement_rate"] * 0.5 +
        rel["factual_accuracy"] * 0.3 +
        rel["reasoning_quality"] * 0.2
    )
    # Clamp to reasonable range
    return max(0.1, min(2.0, weight))


def normalize_weights(models: List[str]) -> Dict[str, float]:
    """Normalize weights so they sum to 1 across the given models."""
    weights = {model: compute_weight(model) for model in models}
    total = sum(weights.values())
    if total > 0:
        return {model: w / total for model, w in weights.items()}
    # Equal weights if all zero
    equal = 1.0 / len(models) if models else 0
    return {model: equal for model in models}


def update_from_stage1(
    model: str,
    confidence: Optional[float],
    has_reasoning_markers: bool,
    response_length: int,
    latency_ms: float,
    success: bool,
) -> None:
    """Update reliability from Stage 1 execution."""
    with _reliability_lock:
        data = _load_reliability()
        if model not in data:
            data[model] = DEFAULT_RELIABILITY.copy()
        
        rel = data[model]
        
        # Update avg_confidence (exponential moving average)
        if confidence is not None:
            alpha = 0.3
            rel["avg_confidence"] = (1 - alpha) * rel["avg_confidence"] + alpha * confidence
        
        # Update reasoning_quality based on markers and length
        if has_reasoning_markers:
            alpha = 0.2
            rel["reasoning_quality"] = (1 - alpha) * rel["reasoning_quality"] + alpha * 0.8
        elif response_length > 500:
            alpha = 0.1
            rel["reasoning_quality"] = (1 - alpha) * rel["reasoning_quality"] + alpha * 0.6
        
        # Update latency (EMA)
        alpha = 0.2
        rel["latency_p50_ms"] = (1 - alpha) * rel["latency_p50_ms"] + alpha * latency_ms
        
        # Update failure rate
        total = rel["sample_count"] + 1
        rel["failure_rate"] = ((total - 1) / total) * rel["failure_rate"] + (0 if success else 1) / total
        
        rel["sample_count"] = total
        rel["last_updated"] = datetime.utcnow().isoformat() + "Z"
        
        _save_reliability(data)


def update_from_stage2(
    model: str,
    parsed_ranking: List[str],
    aggregate_ranking: List[str],
    success: bool,
    latency_ms: float,
) -> None:
    """Update reliability from Stage 2 ranking (agreement with aggregate)."""
    with _reliability_lock:
        data = _load_reliability()
        if model not in data:
            data[model] = DEFAULT_RELIABILITY.copy()
        
        rel = data[model]
        
        if success and parsed_ranking and aggregate_ranking:
            # Compute agreement: how well model's ranking matches aggregate
            # Use Kendall tau or simple position correlation
            agreement = _compute_ranking_agreement(parsed_ranking, aggregate_ranking)
            
            # Update agreement_rate (EMA)
            alpha = 0.3
            rel["agreement_rate"] = (1 - alpha) * rel["agreement_rate"] + alpha * agreement
        
        rel["sample_count"] = rel.get("sample_count", 0) + 1
        rel["last_updated"] = datetime.utcnow().isoformat() + "Z"
        
        _save_reliability(data)


def _compute_ranking_agreement(model_ranking: List[str], aggregate_ranking: List[str]) -> float:
    """
    Compute agreement between model's ranking and aggregate ranking.
    Returns 0-1 score based on normalized Kendall tau distance.
    """
    if not model_ranking or not aggregate_ranking:
        return 0.5
    
    # Create position maps
    model_pos = {item: i for i, item in enumerate(model_ranking)}
    agg_pos = {item: i for i, item in enumerate(aggregate_ranking)}
    
    # Count concordant/discordant pairs
    items = set(model_ranking) & set(aggregate_ranking)
    if len(items) < 2:
        return 0.5
    
    concordant = 0
    discordant = 0
    items_list = list(items)
    for i in range(len(items_list)):
        for j in range(i + 1, len(items_list)):
            a, b = items_list[i], items_list[j]
            model_order = model_pos[a] - model_pos[b]
            agg_order = agg_pos[a] - agg_pos[b]
            if model_order * agg_order > 0:
                concordant += 1
            elif model_order * agg_order < 0:
                discordant += 1
    
    total_pairs = concordant + discordant
    if total_pairs == 0:
        return 0.5
    
    # Kendall tau: (concordant - discordant) / total_pairs = -1 to 1
    # Map to 0-1: (tau + 1) / 2
    tau = (concordant - discordant) / total_pairs
    return (tau + 1) / 2


def update_from_feedback(
    model: str,
    rating: str,
    claim_corrections: List[Dict[str, str]],
) -> None:
    """Update reliability from user feedback."""
    with _reliability_lock:
        data = _load_reliability()
        if model not in data:
            data[model] = DEFAULT_RELIABILITY.copy()
        
        rel = data[model]
        
        # Update factual_accuracy based on rating and corrections
        if rating == "up":
            alpha = 0.2
            rel["factual_accuracy"] = (1 - alpha) * rel["factual_accuracy"] + alpha * 0.9
        elif rating == "down":
            alpha = 0.3
            rel["factual_accuracy"] = (1 - alpha) * rel["factual_accuracy"] + alpha * 0.2
        
        # Corrections are strong negative signals
        if claim_corrections:
            alpha = 0.4
            penalty = min(1.0, len(claim_corrections) * 0.2)
            rel["factual_accuracy"] = (1 - alpha) * rel["factual_accuracy"] + alpha * (1.0 - penalty)
        
        rel["sample_count"] = rel.get("sample_count", 0) + 1
        rel["last_updated"] = datetime.utcnow().isoformat() + "Z"
        
        _save_reliability(data)


def update_from_synthesis(
    model: str,
    confidence_score: Optional[float],
    has_citations: bool,
    success: bool,
) -> None:
    """Update reliability from Stage 3 synthesis (for chairman models)."""
    with _reliability_lock:
        data = _load_reliability()
        if model not in data:
            data[model] = DEFAULT_RELIABILITY.copy()
        
        rel = data[model]
        
        if success:
            # High chairman confidence + citations = good reasoning
            if confidence_score is not None and confidence_score >= 7:
                alpha = 0.2
                rel["reasoning_quality"] = (1 - alpha) * rel["reasoning_quality"] + alpha * 0.9
            elif confidence_score is not None and confidence_score <= 4:
                alpha = 0.2
                rel["reasoning_quality"] = (1 - alpha) * rel["reasoning_quality"] + alpha * 0.3
            
            if has_citations:
                alpha = 0.15
                rel["reasoning_quality"] = (1 - alpha) * rel["reasoning_quality"] + alpha * 0.8
        
        rel["sample_count"] = rel.get("sample_count", 0) + 1
        rel["last_updated"] = datetime.utcnow().isoformat() + "Z"
        
        _save_reliability(data)


def reset_reliability(model: str) -> None:
    """Reset a model's reliability to defaults."""
    with _reliability_lock:
        data = _load_reliability()
        if model in data:
            data[model] = DEFAULT_RELIABILITY.copy()
            data[model]["last_updated"] = datetime.utcnow().isoformat() + "Z"
            _save_reliability(data)


# Backward compatibility
Optional = __import__("typing").Optional