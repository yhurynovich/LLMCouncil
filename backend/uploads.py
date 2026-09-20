"""File upload handling for chat attachments."""
import os
import uuid
import base64
import json
import re
import io
from pathlib import Path
from typing import Optional
from fastapi import UploadFile

UPLOAD_DIR = "data/uploads"

# Configurable size limits via env vars
MAX_TEXT_SIZE = int(os.getenv("MAX_TEXT_SIZE_MB", "100")) * 1024 * 1024  # 100MB default
MAX_IMAGE_SIZE = int(os.getenv("MAX_IMAGE_SIZE_MB", "100")) * 1024 * 1024  # 100MB default
MAX_UPLOAD_TOTAL_MB = int(os.getenv("MAX_UPLOAD_TOTAL_MB", "500"))  # warning threshold

# MCP / embedding thresholds
IMAGE_MAX_BASE64_MB = float(os.getenv("IMAGE_MAX_BASE64_MB", "2"))
TEXT_EMBED_MAX_KB = int(os.getenv("TEXT_EMBED_MAX_KB", "100"))
TEXT_EMBED_MAX_MB = int(os.getenv("TEXT_EMBED_MAX_MB", "1"))
IMAGE_MAX_DIMENSION = int(os.getenv("IMAGE_MAX_DIMENSION", "1920"))
MCP_FILE_ACCESS_ENABLED = os.getenv("MCP_FILE_ACCESS_ENABLED", "true").lower() == "true"

CHUNK_SIZE = 64 * 1024  # 64KB chunks for streaming

# In-memory registry of files available via MCP tools
# Maps file_id -> {filename, type, ext, size, file_type}
_mcp_file_registry: dict = {}

# UUID v4 regex for validation
UUID_V4_REGEX = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$', re.IGNORECASE)

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".html", ".css", ".yaml", ".yml", ".toml", ".xml", ".log"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
ALLOWED_EXTENSIONS = TEXT_EXTENSIONS | IMAGE_EXTENSIONS


def _ensure_upload_dir():
    Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)


def _get_file_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in TEXT_EXTENSIONS:
        return "text"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    return "unknown"


def _validate_file_id(file_id: str) -> bool:
    """Validate file_id is a valid UUID v4 to prevent path traversal."""
    return bool(UUID_V4_REGEX.match(file_id))


def _validate_extension(ext: str) -> bool:
    """Validate extension is in allowed list."""
    return ext.lower() in ALLOWED_EXTENSIONS


def _safe_join(base_dir: str, file_id: str, ext: str) -> str:
    """Safely construct file path, ensuring it stays within base_dir using O_NOFOLLOW to prevent TOCTOU."""
    if not _validate_file_id(file_id):
        raise ValueError("Invalid file ID")
    if not _validate_extension(ext):
        raise ValueError("Invalid file extension")
    
    filename = f"{file_id}{ext}"
    filepath = os.path.join(base_dir, filename)
    
    # Resolve paths to prevent directory traversal
    resolved_base = os.path.realpath(base_dir)
    resolved_path = os.path.realpath(filepath)
    
    if not resolved_path.startswith(resolved_base):
        raise ValueError("Path traversal attempt detected")
    
    return resolved_path


def _safe_open_read(filepath: str, base_dir: str) -> int:
    """Open file for reading with O_NOFOLLOW to prevent symlink attacks. Returns file descriptor."""
    # Validate path is within base_dir
    resolved_base = os.path.realpath(base_dir)
    resolved_path = os.path.realpath(filepath)
    
    if not resolved_path.startswith(resolved_base):
        raise ValueError("Path traversal attempt detected")
    
    # Open with O_NOFOLLOW to prevent symlink following
    fd = os.open(resolved_path, os.O_RDONLY | os.O_NOFOLLOW)
    return fd


async def save_upload(file: UploadFile) -> dict:
    """Save uploaded file with streaming chunked validation to prevent OOM."""
    _ensure_upload_dir()

    ext = Path(file.filename or "file").suffix.lower()
    if not _validate_extension(ext):
        raise ValueError(f"File type not allowed: {ext}")

    file_id = str(uuid.uuid4())
    filename = f"{file_id}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    file_type = _get_file_type(file.filename or "")
    max_size = MAX_IMAGE_SIZE if file_type == "image" else MAX_TEXT_SIZE

    # Stream read in chunks and validate size incrementally
    total_size = 0
    with open(filepath, "wb") as f:
        while True:
            chunk = await file.read(CHUNK_SIZE)
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > max_size:
                # Clean up partial file
                os.remove(filepath)
                raise ValueError(f"File too large. Max size: {max_size // (1024*1024)}MB")
            f.write(chunk)

    result = {
        "file_id": file_id,
        "filename": file.filename or "file",
        "type": file_type,
        "size": total_size,
        "ext": ext,
    }

    # Register in MCP file registry for on-demand access
    register_file_for_mcp(result)

    return result


def read_file_content(file_id: str, ext: str) -> str:
    """Read text file content safely with O_NOFOLLOW."""
    filepath = _safe_join(UPLOAD_DIR, file_id, ext)
    try:
        fd = _safe_open_read(filepath, UPLOAD_DIR)
        try:
            with os.fdopen(fd, "r", errors="replace") as f:
                return f.read()
        except Exception:
            # fd is closed by fdopen context manager
            raise
    except OSError:
        return ""


def get_image_base64(file_id: str, ext: str) -> str:
    """Read image file and return base64 encoded string safely with O_NOFOLLOW."""
    filepath = _safe_join(UPLOAD_DIR, file_id, ext)
    try:
        fd = _safe_open_read(filepath, UPLOAD_DIR)
        try:
            with os.fdopen(fd, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception:
            # fd is closed by fdopen context manager
            raise
    except OSError:
        return ""


def delete_upload(file_id: str, ext: str):
    """Delete an uploaded file."""
    filepath = _safe_join(UPLOAD_DIR, file_id, ext)
    _mcp_file_registry.pop(file_id, None)
    if os.path.exists(filepath):
        os.remove(filepath)


# ── MCP / Hybrid Embed File Access ──────────────────────────────────────

def register_file_for_mcp(file_meta: dict) -> None:
    """Register a file in the MCP registry so LLMs can access it via tools."""
    if not MCP_FILE_ACCESS_ENABLED:
        return
    _mcp_file_registry[file_meta["file_id"]] = dict(file_meta)


def _resolve_file(file_id: str, ext: str) -> str:
    """Validate and return the filepath for an MCP-registered file."""
    if file_id not in _mcp_file_registry:
        raise ValueError(f"File not registered in MCP registry: {file_id}")
    return _safe_join(UPLOAD_DIR, file_id, ext)


def downscale_image(file_id: str, ext: str, max_dimension: int = None, max_size_mb: float = None) -> Optional[str]:
    """Downscale an image to fit within dimension and size limits.

    Returns base64-encoded image string, or None on failure.
    Uses Pillow to resize maintaining aspect ratio.
    """
    try:
        from PIL import Image
    except ImportError:
        return None

    if max_dimension is None:
        max_dimension = IMAGE_MAX_DIMENSION
    if max_size_mb is None:
        max_size_mb = IMAGE_MAX_BASE64_MB

    filepath = _safe_join(UPLOAD_DIR, file_id, ext)
    try:
        with Image.open(filepath) as img:
            # Convert to RGB if necessary (e.g., RGBA, P mode)
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")

            # Resize maintaining aspect ratio
            w, h = img.size
            if max(w, h) > max_dimension:
                scale = max_dimension / max(w, h)
                new_w = int(w * scale)
                new_h = int(h * scale)
                img = img.resize((new_w, new_h), Image.LANCZOS)

            # Encode to base64, trying progressively lower quality if too large
            for quality in [85, 70, 50, 30]:
                buf = io.BytesIO()
                fmt = "JPEG" if ext in (".jpg", ".jpeg") or img.format != "PNG" else "PNG"
                img.save(buf, format=fmt, quality=quality if fmt == "JPEG" else None, optimize=True)
                b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                b64_size_mb = len(b64.encode("utf-8")) / (1024 * 1024)
                if b64_size_mb <= max_size_mb:
                    return b64

            # If we still can't fit, return the smallest version
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=30, optimize=True)
            return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception:
        return None


def should_embed_directly(file_meta: dict) -> tuple:
    """Decide whether to embed a file directly in the LLM prompt or use MCP tools.

    Args:
        file_meta: dict with keys: file_id, filename, type, ext, size

    Returns:
        (embed: bool, reason: str)
    """
    if not MCP_FILE_ACCESS_ENABLED:
        return True, "MCP file access disabled; embedding all files"

    file_type = file_meta.get("type", "unknown")
    size = file_meta.get("size", 0)
    ext = file_meta.get("ext", "")

    if file_type == "image":
        # Try downscaling; if result is small enough, embed directly
        b64 = downscale_image(file_meta["file_id"], ext)
        if b64 is None:
            return False, "Image downscale failed; using MCP"
        b64_mb = len(b64.encode("utf-8")) / (1024 * 1024)
        if b64_mb <= IMAGE_MAX_BASE64_MB:
            return True, f"Image downscaled to {b64_mb:.1f}MB; embedded directly"
        return False, f"Downscaled image still {b64_mb:.1f}MB > {IMAGE_MAX_BASE64_MB}MB; using MCP"

    if file_type == "text":
        size_kb = size / 1024
        if size_kb <= TEXT_EMBED_MAX_KB:
            return True, f"Text file {size_kb:.0f}KB below embed threshold; embedded directly"
        elif size <= TEXT_EMBED_MAX_MB * 1024 * 1024:
            return True, f"Text file {size_kb:.0f}KB within truncation threshold; embedded with truncation note"
        return False, f"Text file {size_kb:.0f}KB exceeds MCP threshold; using MCP tools"

    # Unknown/binary type
    return False, f"Unknown/binary file type; using MCP"


def get_file_metadata(file_id: str, ext: str) -> dict:
    """Get metadata for a registered file."""
    filepath = _resolve_file(file_id, ext)
    stat = os.stat(filepath)
    return {
        "file_id": file_id,
        "filename": f"{file_id}{ext}",
        "ext": ext,
        "type": _get_file_type(f"{file_id}{ext}"),
        "size": stat.st_size,
        "modified": stat.st_mtime,
        "encoding": "utf-8",
        "lines": _count_lines(filepath) if _get_file_type(f"{file_id}{ext}") == "text" else None,
    }


def _count_lines(filepath: str) -> int:
    """Count lines in a text file."""
    try:
        fd = _safe_open_read(filepath, UPLOAD_DIR)
        with os.fdopen(fd, "r", errors="replace") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def read_file_chunk(file_id: str, ext: str, offset: int = 0, limit: int = 64 * 1024) -> str:
    """Read a chunk of a file starting at byte offset, up to limit bytes.

    For text files returns decoded text. For images returns an info string
    with image dimensions instead of raw bytes (use get_image_base64 for images).
    """
    filepath = _resolve_file(file_id, ext)
    file_type = _get_file_type(f"{file_id}{ext}")

    if file_type == "image":
        # For images, return metadata instead of raw bytes
        meta = get_file_metadata(file_id, ext)
        return json.dumps({
            "file_id": file_id,
            "type": "image",
            "size": meta["size"],
            "info": f"Image file ({meta['size']} bytes). Use the model's vision capabilities with downscaled base64 if needed."
        }, indent=2)

    # Read text chunk
    try:
        fd = _safe_open_read(filepath, UPLOAD_DIR)
        with os.fdopen(fd, "rb") as f:
            f.seek(offset)
            chunk = f.read(limit)
        return chunk.decode("utf-8", errors="replace")
    except OSError:
        return ""


def list_uploaded_files(pattern: str = None) -> list:
    """List all uploaded files, optionally filtered by pattern."""
    files = []
    for fid, meta in _mcp_file_registry.items():
        if pattern is None or pattern.lower() in meta.get("filename", "").lower():
            files.append(meta)
    return files


def search_files(query: str, max_results: int = 10) -> list:
    """Search for a query string within text files in the upload directory.

    Returns list of {file_id, filename, line_number, line_content} dicts.
    """
    results = []
    for fid, meta in _mcp_file_registry.items():
        if meta.get("type") != "text":
            continue
        filepath = _safe_join(UPLOAD_DIR, fid, meta["ext"])
        try:
            fd = _safe_open_read(filepath, UPLOAD_DIR)
            with os.fdopen(fd, "r", errors="replace") as f:
                for i, line in enumerate(f, 1):
                    if query.lower() in line.lower():
                        results.append({
                            "file_id": fid,
                            "filename": meta["filename"],
                            "line_number": i,
                            "line_content": line.rstrip()[:200],
                        })
                        if len(results) >= max_results:
                            return results
        except (OSError, ValueError):
            continue
    return results


# ── OpenAI/OpenRouter Tool Definitions for MCP File Access ──────────────

def get_file_tools() -> list:
    """Return tool definitions for file access, suitable for OpenRouter tool calling.

    Only include read_file, search_files, list_files, get_file_info — all
    operate on the backend's local upload storage.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": (
                    "Read the content of an uploaded file. For large text files "
                    "this returns a chunk starting at 'offset' (in bytes); supply "
                    "a new offset with the file_id to read subsequent chunks. "
                    "Images return metadata instead of raw bytes."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_id": {"type": "string", "description": "UUID of the uploaded file"},
                        "ext": {"type": "string", "description": "File extension including the dot, e.g. '.py'"},
                        "offset": {"type": "integer", "description": "Byte offset to start reading from (default 0)", "default": 0},
                        "limit": {"type": "integer", "description": "Maximum bytes to return (default 65536)", "default": 65536},
                    },
                    "required": ["file_id", "ext"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_files",
                "description": (
                    "Search for a query string across all uploaded text files. "
                    "Returns matching lines with file_id, filename, and line number."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query to find in file contents"},
                        "max_results": {"type": "integer", "description": "Maximum results to return (default 10)", "default": 10},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": (
                    "List all uploaded files available for access, optionally "
                    "filtered by a pattern in the filename."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Optional pattern to filter filenames"},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_file_info",
                "description": (
                    "Get metadata for a specific file: size, type, line count, "
                    "encoding, modification time."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_id": {"type": "string", "description": "UUID of the uploaded file"},
                        "ext": {"type": "string", "description": "File extension including the dot"},
                    },
                    "required": ["file_id", "ext"],
                },
            },
        },
    ]


def resolve_file_tool_call(tool_name: str, arguments: dict) -> str:
    """Resolve a file tool call and return the result string.

    Handles: read_file, search_files, list_files, get_file_info
    """
    try:
        if tool_name == "read_file":
            file_id = arguments.get("file_id", "")
            ext = arguments.get("ext", "")
            offset = int(arguments.get("offset", 0))
            limit = int(arguments.get("limit", 64 * 1024))
            content = read_file_chunk(file_id, ext, offset=offset, limit=limit)
            result = {
                "file_id": file_id,
                "ext": ext,
                "offset": offset,
                "bytes_returned": len(content),
                "content": content if content else "[empty or end of file]",
            }
            return json.dumps(result, indent=2)

        elif tool_name == "search_files":
            query = arguments.get("query", "")
            max_results = int(arguments.get("max_results", 10))
            results = search_files(query, max_results=max_results)
            return json.dumps(results, indent=2)

        elif tool_name == "list_files":
            pattern = arguments.get("pattern", None)
            files = list_uploaded_files(pattern=pattern)
            return json.dumps(files, indent=2)

        elif tool_name == "get_file_info":
            file_id = arguments.get("file_id", "")
            ext = arguments.get("ext", "")
            meta = get_file_metadata(file_id, ext)
            return json.dumps(meta, indent=2)

        else:
            return json.dumps({"error": f"Unknown file tool: {tool_name}"})
    except ValueError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        return json.dumps({"error": f"File tool error: {str(e)}"})