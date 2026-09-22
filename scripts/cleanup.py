#!/usr/bin/env python3
"""
Daily cleanup for LLM Council data directories.

Removes stale upload files, old conversations, backup files, temp artifacts,
and orphaned bytecode caches. Configured via environment variables (loaded
from .env) with sensible defaults.

Usage:
    .venv/bin/python scripts/cleanup.py              # Run cleanup
    .venv/bin/python scripts/cleanup.py --dry-run    # Preview without deleting
    .venv/bin/python scripts/cleanup.py --quiet      # Suppress stdout (cron-friendly)
"""

import argparse
import logging
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv()

PROJECT_ROOT = Path(__file__).parent.parent.resolve()

UPLOAD_DAYS = int(os.getenv("CLEANUP_UPLOAD_DAYS", "7"))
CONVERSATION_DAYS = int(os.getenv("CLEANUP_CONVERSATION_DAYS", "7"))
METRICS_DAYS = int(os.getenv("CLEANUP_METRICS_DAYS", "30"))
LOG_PATTERNS = os.getenv("CLEANUP_LOGS", "backend.log,frontend.log").split(",")
TEMP_DIRS = os.getenv("CLEANUP_TEMP_DIRS", "/tmp/council_review").split(",")
LOG_FILE = Path(os.getenv("CLEANUP_LOG_FILE", str(PROJECT_ROOT / "data" / "logs" / "cleanup.log")))

logger = logging.getLogger("cleanup")


def setup_logging(quiet: bool):
    """Configure logging to both file and stdout."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    handlers = [logging.FileHandler(LOG_FILE)]
    if not quiet:
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )


def delete_old_files(directory: Path, pattern: str, max_age_days: int, dry_run: bool) -> int:
    """Delete files matching pattern older than max_age_days. Returns count deleted."""
    if not directory.exists():
        return 0

    cutoff = datetime.now() - timedelta(days=max_age_days)
    count = 0
    for filepath in directory.glob(pattern):
        try:
            mtime = datetime.fromtimestamp(filepath.stat().st_mtime)
            if mtime < cutoff:
                if not dry_run:
                    filepath.unlink()
                logger.info(f"Deleted: {filepath.relative_to(PROJECT_ROOT)} (age: {(datetime.now() - mtime).days}d)")
                count += 1
        except OSError as e:
            logger.warning(f"Failed to delete {filepath}: {e}")
    return count


def delete_orphaned_tmps(directory: Path, dry_run: bool) -> int:
    """Delete orphaned .tmp files in a directory."""
    if not directory.exists():
        return 0

    count = 0
    for filepath in directory.glob("*.tmp"):
        if not dry_run:
            filepath.unlink()
        logger.info(f"Deleted orphaned: {filepath.relative_to(PROJECT_ROOT)}")
        count += 1
    return count


def remove_temp_dirs(temp_dirs: list, dry_run: bool) -> int:
    """Remove temporary directories listed in config."""
    count = 0
    for d in temp_dirs:
        path = Path(d.strip())
        if path.exists():
            if not dry_run:
                shutil.rmtree(path)
            logger.info(f"Removed temp dir: {path}")
            count += 1
    return count


def remove_log_files(log_patterns: list, dry_run: bool) -> int:
    """Remove log files matching the configured patterns from project root."""
    count = 0
    for pattern in log_patterns:
        pattern = pattern.strip()
        for filepath in PROJECT_ROOT.glob(pattern):
            if not dry_run:
                filepath.unlink()
            logger.info(f"Deleted log: {filepath.relative_to(PROJECT_ROOT)}")
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description="Daily cleanup for LLM Council")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions without deleting")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress stdout output")
    args = parser.parse_args()

    setup_logging(args.quiet)

    if args.dry_run:
        logger.info("=== DRY RUN — no files will be deleted ===")
    else:
        logger.info("=== Starting cleanup ===")

    total = 0

    logger.info(f"Config: upload_days={UPLOAD_DAYS}, conv_days={CONVERSATION_DAYS}, metrics_days={METRICS_DAYS}")

    uploads_dir = PROJECT_ROOT / "data" / "uploads"
    total += delete_old_files(uploads_dir, "*", UPLOAD_DAYS, args.dry_run)

    conv_dir = PROJECT_ROOT / "data" / "conversations"
    total += delete_old_files(conv_dir, "*.json", CONVERSATION_DAYS, args.dry_run)

    data_dir = PROJECT_ROOT / "data"
    total += delete_old_files(data_dir, "*.json.bak", 0, args.dry_run)

    metrics_dir = PROJECT_ROOT / "data" / "metrics"
    total += delete_old_files(metrics_dir, "*.jsonl", METRICS_DAYS, args.dry_run)

    total += delete_orphaned_tmps(uploads_dir, args.dry_run)
    total += delete_orphaned_tmps(conv_dir, args.dry_run)

    total += remove_temp_dirs(TEMP_DIRS, args.dry_run)

    total += remove_log_files(LOG_PATTERNS, args.dry_run)

    if args.dry_run:
        logger.info(f"=== DRY RUN complete — {total} files would be removed ===")
    else:
        logger.info(f"=== Cleanup complete — {total} items removed ===")


if __name__ == "__main__":
    main()
