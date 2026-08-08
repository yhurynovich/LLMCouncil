"""Metrics collection and logging for LLM Council."""
import json
import time
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Metrics storage directory
METRICS_DIR = Path("data/metrics")
METRICS_DIR.mkdir(parents=True, exist_ok=True)

# In-memory metrics for summary endpoint
_stage_metrics = {
    "stage1": {"total": 0, "success": 0, "failed": 0, "latencies": []},
    "stage2": {"total": 0, "success": 0, "failed": 0, "latencies": []},
    "stage3": {"total": 0, "success": 0, "failed": 0, "latencies": []},
}
_model_metrics: Dict[str, Dict[str, Any]] = {}
_metrics_lock = __import__("threading").Lock()


def _get_metrics_file() -> Path:
    """Get today's metrics log file."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    return METRICS_DIR / f"metrics_{today}.jsonl"


def log_stage_metric(
    stage: int,
    model: str,
    success: bool,
    latency_ms: float,
    response_length: int = 0,
    has_reasoning_markers: bool = False,
    parse_success: bool = True,
    error: Optional[str] = None,
) -> None:
    """Log a structured metric entry for a stage execution."""
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "stage": stage,
        "model": model,
        "success": success,
        "latency_ms": latency_ms,
        "response_length": response_length,
        "has_reasoning_markers": has_reasoning_markers,
        "parse_success": parse_success,
        "error": error,
    }

    # Write to JSONL file
    try:
        with open(_get_metrics_file(), "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("Failed to write metrics: %s", e)

    # Update in-memory aggregates
    with _metrics_lock:
        stage_key = f"stage{stage}"
        if stage_key not in _stage_metrics:
            _stage_metrics[stage_key] = {"total": 0, "success": 0, "failed": 0, "latencies": []}
        _stage_metrics[stage_key]["total"] += 1
        if success:
            _stage_metrics[stage_key]["success"] += 1
        else:
            _stage_metrics[stage_key]["failed"] += 1
        _stage_metrics[stage_key]["latencies"].append(latency_ms)
        # Keep only last 1000 latencies
        if len(_stage_metrics[stage_key]["latencies"]) > 1000:
            _stage_metrics[stage_key]["latencies"] = _stage_metrics[stage_key]["latencies"][-1000:]

        # Per-model metrics
        if model not in _model_metrics:
            _model_metrics[model] = {
                "total": 0, "success": 0, "failed": 0, "latencies": [],
                "total_response_length": 0
            }
        m = _model_metrics[model]
        m["total"] += 1
        if success:
            m["success"] += 1
        else:
            m["failed"] += 1
        m["latencies"].append(latency_ms)
        m["total_response_length"] += response_length
        if len(m["latencies"]) > 1000:
            m["latencies"] = m["latencies"][-1000:]


def log_ranking_metric(
    model: str,
    success: bool,
    latency_ms: float,
    parsed_ranking_count: int = 0,
    parse_success: bool = True,
    error: Optional[str] = None,
) -> None:
    """Log a structured metric entry for Stage 2 ranking."""
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "stage": 2,
        "model": model,
        "success": success,
        "latency_ms": latency_ms,
        "parsed_ranking_count": parsed_ranking_count,
        "parse_success": parse_success,
        "error": error,
    }

    try:
        with open(_get_metrics_file(), "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("Failed to write ranking metrics: %s", e)


def log_synthesis_metric(
    model: str,
    success: bool,
    latency_ms: float,
    response_length: int = 0,
    has_citations: bool = False,
    confidence_score: Optional[float] = None,
    error: Optional[str] = None,
) -> None:
    """Log a structured metric entry for Stage 3 synthesis."""
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "stage": 3,
        "model": model,
        "success": success,
        "latency_ms": latency_ms,
        "response_length": response_length,
        "has_citations": has_citations,
        "confidence_score": confidence_score,
        "error": error,
    }

    try:
        with open(_get_metrics_file(), "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("Failed to write synthesis metrics: %s", e)


def get_metrics_summary() -> Dict[str, Any]:
    """Get aggregated metrics summary for the /api/metrics/summary endpoint."""
    with _metrics_lock:
        summary = {}
        for stage_key, data in _stage_metrics.items():
            latencies = data["latencies"]
            if latencies:
                sorted_lat = sorted(latencies)
                summary[stage_key] = {
                    "total_requests": data["total"],
                    "success_rate": data["success"] / data["total"] if data["total"] > 0 else 0,
                    "failure_rate": data["failed"] / data["total"] if data["total"] > 0 else 0,
                    "latency_p50_ms": sorted_lat[len(sorted_lat) // 2],
                    "latency_p95_ms": sorted_lat[int(len(sorted_lat) * 0.95)],
                    "latency_p99_ms": sorted_lat[int(len(sorted_lat) * 0.99)],
                }
            else:
                summary[stage_key] = {
                    "total_requests": 0,
                    "success_rate": 0,
                    "failure_rate": 0,
                    "latency_p50_ms": 0,
                    "latency_p95_ms": 0,
                    "latency_p99_ms": 0,
                }

        # Per-model summary
        model_summary = {}
        for model, data in _model_metrics.items():
            latencies = data["latencies"]
            if latencies:
                sorted_lat = sorted(latencies)
                model_summary[model] = {
                    "total_requests": data["total"],
                    "success_rate": data["success"] / data["total"] if data["total"] > 0 else 0,
                    "avg_latency_ms": sum(latencies) / len(latencies),
                    "latency_p50_ms": sorted_lat[len(sorted_lat) // 2],
                    "latency_p95_ms": sorted_lat[int(len(sorted_lat) * 0.95)],
                    "avg_response_length": data["total_response_length"] / data["total"] if data["total"] > 0 else 0,
                }
            else:
                model_summary[model] = {"total_requests": 0}

        return {
            "stages": summary,
            "models": model_summary,
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }


@contextmanager
def measure_latency(model: str, stage: int):
    """Context manager to measure and log latency for a model call."""
    start = time.perf_counter()
    success = False
    error = None
    try:
        yield lambda s, **kwargs: log_stage_metric(stage, model, s, (time.perf_counter() - start) * 1000, **kwargs)
        success = True
    except Exception as e:
        error = str(e)
        raise
    finally:
        if not success:
            log_stage_metric(stage, model, False, (time.perf_counter() - start) * 1000, error=error)