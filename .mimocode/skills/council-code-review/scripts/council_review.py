#!/usr/bin/env python3
"""
Council Code Review - sends code to LLM Council backend for multi-model deliberation.

Backend: http://localhost:8001/v1
Model set: code (server-side group of models)

Usage:
    python council_review.py --code "def foo(): pass" --files main.py utils.py
    python council_review.py --files src/app.py --context "focus on security"
"""

import argparse
import json
import sys
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


DEFAULT_URL = "http://localhost:8001/v1"
DEFAULT_MODEL = "code"
TIMEOUT = 300


def read_files(file_paths: list[str]) -> dict[str, str]:
    """Read file contents and return {filename: content} dict."""
    contents = {}
    for fp in file_paths:
        path = Path(fp).resolve()
        if not path.exists():
            print(f"Warning: file not found: {fp}", file=sys.stderr)
            continue
        if not path.is_file():
            print(f"Warning: not a file: {fp}", file=sys.stderr)
            continue
        try:
            contents[str(path)] = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            print(f"Warning: could not read {fp}: {e}", file=sys.stderr)
    return contents


def build_review_prompt(code: str, files: dict[str, str], context: str | None) -> str:
    """Build the full review prompt with code and file contents."""
    parts = []

    if context:
        parts.append(f"Review context/instructions: {context}\n")

    if files:
        parts.append("=== Attached Files ===\n")
        for filename, content in files.items():
            parts.append(f"--- {filename} ---\n{content}\n")

    if code:
        parts.append("=== Code to Review ===\n")
        parts.append(code)

    if not parts:
        parts.append("No code provided. Please specify code or files to review.")

    prompt = (
        "You are participating in a multi-model code review council.\n\n"
        "Review the following code thoroughly. Consider:\n"
        "- Correctness and bugs\n"
        "- Security vulnerabilities\n"
        "- Performance issues\n"
        "- Code style and maintainability\n"
        "- Edge cases and error handling\n\n"
        "Provide your evaluation with specific, actionable feedback.\n\n"
        + "\n".join(parts)
    )
    return prompt


def query_council(url: str, model: str, prompt: str) -> dict:
    """Send review request to the LLM Council backend."""
    endpoint = f"{url}/chat/completions"

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an expert code reviewer. Evaluate code thoroughly for bugs, "
                    "security issues, performance problems, and style. Be specific and "
                    "actionable in your feedback."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 4096,
    }

    data = json.dumps(payload).encode("utf-8")
    req = Request(
        endpoint,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body
    except HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP Error {e.code}: {error_body}", file=sys.stderr)
        sys.exit(1)
    except URLError as e:
        print(f"Connection error: {e.reason}", file=sys.stderr)
        print(
            f"Make sure the backend is running at {url}", file=sys.stderr
        )
        sys.exit(1)
    except Exception as e:
        print(f"Request failed: {e}", file=sys.stderr)
        sys.exit(1)


def extract_content(response: dict) -> str | None:
    """Extract text content from chat completion response."""
    try:
        choices = response.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content")
    except (KeyError, IndexError):
        pass
    return None


def main():
    parser = argparse.ArgumentParser(description="LLM Council Code Review")
    parser.add_argument("--url", default=DEFAULT_URL, help="Backend API URL")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model set name")
    parser.add_argument("--code", default="", help="Inline code to review")
    parser.add_argument("--files", nargs="*", default=[], help="File paths to attach")
    parser.add_argument("--context", default=None, help="Additional review context")
    parser.add_argument(
        "--raw", action="store_true", help="Output raw API response JSON"
    )
    args = parser.parse_args()

    # Read attached files
    files = read_files(args.files) if args.files else {}

    # Build prompt
    prompt = build_review_prompt(args.code, files, args.context)

    # Query the council
    response = query_council(args.url, args.model, prompt)

    if args.raw:
        print(json.dumps(response, indent=2))
    else:
        content = extract_content(response)
        if content:
            print(content)
        else:
            print("No response content received.", file=sys.stderr)
            print(json.dumps(response, indent=2))
            sys.exit(1)


if __name__ == "__main__":
    main()
