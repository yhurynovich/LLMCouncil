"""User feedback collection and processing for model reliability."""
import json
import os
import threading
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path

FEEDBACK_FILE = Path("data/feedback.json")
FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)

_feedback_lock = threading.Lock()


def _load_feedback() -> List[Dict[str, Any]]:
    """Load feedback from file."""
    if not FEEDBACK_FILE.exists():
        return []
    try:
        with open(FEEDBACK_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_feedback(feedback_list: List[Dict[str, Any]]) -> None:
    """Save feedback to file atomically."""
    tmp = FEEDBACK_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(feedback_list, f, indent=2)
    os.replace(tmp, FEEDBACK_FILE)


def add_feedback(
    conversation_id: str,
    message_index: int,
    rating: str,
    claim_corrections: Optional[List[Dict[str, str]]] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Add a feedback entry."""
    if rating not in ("up", "down"):
        raise ValueError("Rating must be 'up' or 'down'")

    entry = {
        "conversation_id": conversation_id,
        "message_index": message_index,
        "rating": rating,
        "claim_corrections": claim_corrections or [],
        "user_id": user_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

    with _feedback_lock:
        feedback = _load_feedback()
        feedback.append(entry)
        _save_feedback(feedback)

    return entry


def get_feedback_for_conversation(conversation_id: str) -> List[Dict[str, Any]]:
    """Get all feedback for a conversation."""
    with _feedback_lock:
        feedback = _load_feedback()
        return [f for f in feedback if f["conversation_id"] == conversation_id]


def get_all_feedback() -> List[Dict[str, Any]]:
    """Get all feedback entries."""
    with _feedback_lock:
        return _load_feedback()


def compute_reliability_signals() -> Dict[str, Dict[str, float]]:
    """
    Compute reliability signals from feedback data.
    Returns per-model signals for weighting.
    """
    feedback = get_all_feedback()
    
    # Model -> list of (rating, claim_corrections)
    model_ratings: Dict[str, List[tuple]] = {}
    model_corrections: Dict[str, List[str]] = {}
    
    # Note: We need to map conversation/message_index to model
    # This requires cross-referencing with conversation data
    # For now, return empty - will be enhanced when integrated with council runs
    
    return {}