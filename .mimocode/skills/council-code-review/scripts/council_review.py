#!/usr/bin/env python3
"""
Council Code Review - sends code to LLM Council backend for multi-model deliberation.

Backend: http://192.168.31.66:5174
Model set: code (server-side group of models)

When files are provided, they are uploaded to the backend's /api/upload endpoint
and sent as FileAttachment objects via the SSE streaming endpoint. The backend
uses a hybrid embed/MCP architecture: small files are embedded directly in the
prompt, while large files (text >1MB, images >2MB) are made available via MCP
file tools (read_file, search_files, list_files, get_file_info) that the LLM
can call on-demand — bypassing the 5MB body limit of the OpenRouter proxy.

When only inline code is provided (no files), the simpler OpenAI-compatible
endpoint is used.

Usage:
    python council_review.py --code "def foo(): pass"
    python council_review.py --files main.py utils.py
    python council_review.py --files src/app.py --context "focus on security"
    python council_review.py --files src/app.py --output-format json
"""

import argparse
import json
import sys
import os
import uuid
import mimetypes
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


DEFAULT_URL = "http://192.168.31.66:5174"
DEFAULT_MODEL = "code"
TIMEOUT = 600
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".html", ".css", ".yaml", ".yml", ".toml", ".xml", ".log"}


def read_inline_files(file_paths):
    """Read file contents for inline embedding (legacy mode, no upload)."""
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


def upload_file(base_url, file_path):
    """Upload a single file to the backend's /api/upload endpoint.

    Returns the FileAttachment dict (file_id, filename, type, ext, size)
    or None on failure.
    """
    path = Path(file_path).resolve()
    if not path.exists() or not path.is_file():
        print(f"Warning: file not found: {file_path}", file=sys.stderr)
        return None

    ext = path.suffix.lower()
    filename = path.name
    file_size = path.stat().st_size

    # Build multipart/form-data
    boundary = f"----CouncilReview-{uuid.uuid4().hex}"
    with open(path, "rb") as f:
        file_data = f.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n"
        f"\r\n"
    ).encode("utf-8") + file_data + f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = Request(
        f"{base_url}/api/upload",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
        method="POST",
    )

    try:
        with urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result
    except HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        print(f"Error uploading {filename}: HTTP {e.code} - {error_body}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Error uploading {filename}: {e}", file=sys.stderr)
        return None


def upload_files(base_url, file_paths):
    """Upload all files and return list of FileAttachment dicts."""
    attachments = []
    for fp in file_paths:
        result = upload_file(base_url, fp)
        if result:
            attachments.append(result)
            print(f"  Uploaded: {result['filename']} ({result['type']}, {result['size']} bytes)", file=sys.stderr)
        else:
            # Upload failed — fall back to inline embedding for small text files
            path = Path(fp).resolve()
            ext = path.suffix.lower()
            if ext in TEXT_EXTENSIONS and path.stat().st_size < 1024 * 1024:
                content = path.read_text(encoding="utf-8", errors="replace")
                attachments.append({
                    "file_id": None,
                    "filename": path.name,
                    "type": "text",
                    "ext": ext,
                    "size": len(content),
                    "_inline_content": content,
                })
    return attachments


def build_inline_prompt(code, files, context):
    """Build review prompt with inline file contents (legacy mode)."""
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


def create_conversation(base_url):
    """Create a new conversation and return its ID."""
    req = Request(
        f"{base_url}/api/conversations",
        data=json.dumps({}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as resp:
            conv = json.loads(resp.read().decode("utf-8"))
            return conv["id"]
    except Exception as e:
        print(f"Error creating conversation: {e}", file=sys.stderr)
        sys.exit(1)


def send_review_sse(base_url, conversation_id, code, file_attachments, context,
                    model_set, session_id, temperature, max_tokens, quick):
    """Send review request via SSE streaming endpoint with file attachments.

    Returns a dict with stage1, stage2, stage3, metadata, and title.
    """
    messages = [{"role": "user", "content": code}] if code else []

    payload = {
        "content": context or "",
        "model_set": model_set,
        "quick": quick,
        "files": [
            {k: v for k, v in fa.items() if not k.startswith("_")}
            for fa in file_attachments
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    # If inline content exists in any file attachment, include it
    inline_contents = []
    for fa in file_attachments:
        if fa.get("_inline_content"):
            inline_contents.append(f"--- {fa['filename']} ---\n{fa['_inline_content']}\n")
    if inline_contents:
        inline_text = "\n".join(inline_contents)
        if not payload["content"]:
            payload["content"] = inline_text
        else:
            payload["content"] = inline_text + "\n\n" + payload["content"]

    # Build the system + user messages context for the council
    if payload["content"]:
        prompt_parts = []
        if context:
            prompt_parts.append(f"Review context/instructions: {context}\n")
        prompt_parts.append("=== Code to Review ===\n")
        prompt_parts.append(payload["content"])
        full_prompt = (
            "You are participating in a multi-model code review council.\n\n"
            "Review the following code thoroughly. Consider:\n"
            "- Correctness and bugs\n"
            "- Security vulnerabilities\n"
            "- Performance issues\n"
            "- Code style and maintainability\n"
            "- Edge cases and error handling\n\n"
            "Provide your evaluation with specific, actionable feedback.\n\n"
            + "\n".join(prompt_parts)
        )
        payload["content"] = full_prompt

    headers = {
        "Content-Type": "application/json",
    }
    if session_id:
        headers["X-Session-ID"] = session_id
        headers["X-Conversation-ID"] = session_id
    headers["X-Request-ID"] = str(uuid.uuid4())

    endpoint = f"{base_url}/api/conversations/{conversation_id}/message/stream"
    req = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            results = {
                "stage1": [],
                "stage2": [],
                "stage3": {},
                "metadata": {},
                "title": None,
            }
            for line in resp:
                line = line.decode("utf-8").strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    etype = event.get("type")
                    if etype == "model_set":
                        results["model_set"] = event.get("data", {})
                    elif etype == "stage1_complete":
                        results["stage1"] = event.get("data", [])
                    elif etype == "stage2_complete":
                        results["stage2"] = event.get("data", [])
                        meta = event.get("metadata", {})
                        results["metadata"]["label_to_model"] = meta.get("label_to_model", {})
                        results["metadata"]["aggregate_rankings"] = meta.get("aggregate_rankings", [])
                    elif etype == "stage3_complete":
                        results["stage3"] = event.get("data", {})
                    elif etype == "title_complete":
                        results["title"] = event.get("data", {}).get("title")
                    elif etype == "error":
                        results["error"] = event.get("message", "Unknown error")
                    elif etype == "complete":
                        break

            return results
    except HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP Error {e.code}: {error_body}", file=sys.stderr)
        sys.exit(1)
    except URLError as e:
        print(f"Connection error: {e.reason}", file=sys.stderr)
        print(f"Make sure the backend is running at {base_url}", file=sys.stderr)
        sys.exit(1)


def query_council(url, model, prompt, session_id=None):
    """Send review request to the LLM Council backend via OpenAI-compatible endpoint.

    Used for inline-only code reviews (no file attachments).
    """
    endpoint = f"{url}/v1/chat/completions"

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
        "stream": False,
    }

    data = json.dumps(payload).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
    }
    if session_id:
        headers["X-Session-ID"] = session_id
        headers["X-Conversation-ID"] = session_id
    headers["X-Request-ID"] = str(uuid.uuid4())

    req = Request(
        endpoint,
        data=data,
        headers=headers,
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
        print(f"Make sure the backend is running at {url}", file=sys.stderr)
        sys.exit(1)


def extract_content(response):
    """Extract text content from OpenAI-compatible chat completion response."""
    try:
        choices = response.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content")
    except (KeyError, IndexError):
        pass
    return None


def format_sse_output(results):
    """Format SSE streaming results for human-readable output."""
    lines = []

    if results.get("title"):
        lines.append(f"# Code Review: {results['title']}")
        lines.append("")

    # Stage 3 — Final Verdict
    stage3 = results.get("stage3", {})
    if stage3:
        lines.append("## Final Verdict (Stage 3)")
        lines.append("")
        lines.append(stage3.get("response") or "No verdict from chairman.")
        lines.append("")

    # Stage 1 — Individual Reviews
    stage1 = results.get("stage1", [])
    if stage1:
        lines.append("## Individual Reviews (Stage 1)")
        lines.append("")
        for r in stage1:
            model_name = r.get("model", "unknown")
            response_text = r.get("response") or ""
            lines.append(f"### {model_name}")
            lines.append("")
            lines.append(response_text)
            lines.append("")

    # Stage 2 — Peer Rankings
    stage2 = results.get("stage2", [])
    if stage2:
        lines.append("## Peer Rankings (Stage 2)")
        lines.append("")
        for ranking in results.get("metadata", {}).get("aggregate_rankings", []):
            model_name = ranking.get("model", "unknown")
            avg_pos = ranking.get("avg_position", "?")
            votes = ranking.get("votes", 0)
            lines.append(f"- **{model_name}**: avg rank {avg_pos} ({votes} votes)")
        lines.append("")

        for r in stage2:
            model = results.get("metadata", {}).get("label_to_model", {}).get(
                r.get("evaluator_label", ""), r.get("model", "unknown")
            )
            lines.append(f"### {model} evaluated anonymously:")
            lines.append("")
            lines.append(r.get("evaluation") or "")
            lines.append("")

    return "\n".join(lines)


def format_json_output(results):
    """Format results as structured JSON."""
    return json.dumps(results, indent=2)


def main():
    parser = argparse.ArgumentParser(description="LLM Council Code Review")
    parser.add_argument("--url", default=DEFAULT_URL, help="Backend API URL (default: %(default)s)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model set name")
    parser.add_argument("--code", default="", help="Inline code to review")
    parser.add_argument("--files", nargs="*", default=[], help="File paths to attach (uploaded via /api/upload)")
    parser.add_argument("--context", default=None, help="Additional review context/instructions")
    parser.add_argument("--session-id", default=None, help="Session ID for conversation tracking")
    parser.add_argument("--stream", action="store_true", help="Force streaming SSE endpoint even for inline code")
    parser.add_argument("--raw", action="store_true", help="Output raw API response JSON")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format for non-raw mode")
    parser.add_argument("--temperature", type=float, default=0.3, help="Sampling temperature")
    parser.add_argument("--max-tokens", type=int, default=4096, help="Maximum tokens per model response")
    parser.add_argument("--quick", action="store_true", help="Skip Stage 2 & 3 (ranking and synthesis), return Stage 1 only")
    args = parser.parse_args()

    # Generate session ID if not provided
    session_id = args.session_id or str(uuid.uuid4())
    print(f"Using session ID: {session_id}", file=sys.stderr)

    base_url = args.url.rstrip("/")

    # Determine whether to use file upload + SSE flow
    use_files = len(args.files) > 0
    use_sse = use_files or args.stream

    if use_sse:
        # Upload files if provided
        file_attachments = []
        if args.files:
            print(f"Uploading {len(args.files)} file(s)...", file=sys.stderr)
            file_attachments = upload_files(base_url, args.files)
            if not file_attachments:
                print("Error: all file uploads failed", file=sys.stderr)
                sys.exit(1)

        # Create a conversation for the SSE streaming endpoint
        conversation_id = create_conversation(base_url)
        print(f"Conversation: {conversation_id}", file=sys.stderr)

        # Send via SSE streaming endpoint
        print("Reviewing with LLM Council (SSE)..." if not args.quick else "Quick review (Stage 1 only)...", file=sys.stderr)
        results = send_review_sse(
            base_url, conversation_id, args.code, file_attachments, args.context,
            args.model, session_id, args.temperature, args.max_tokens, args.quick
        )

        if args.raw or args.format == "json":
            print(format_json_output(results))
        else:
            print(format_sse_output(results))
    else:
        # Inline-only: use OpenAI-compatible endpoint
        files = read_inline_files(args.files) if args.files else {}

        parts = []
        if args.context:
            parts.append(f"Review context/instructions: {args.context}\n")
        if files:
            parts.append("=== Attached Files ===\n")
            for filename, content in files.items():
                parts.append(f"--- {filename} ---\n{content}\n")
        if args.code:
            parts.append("=== Code to Review ===\n")
            parts.append(args.code)
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

        print("Reviewing with LLM Council...", file=sys.stderr)
        response = query_council(base_url, args.model, prompt, session_id=session_id)

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
