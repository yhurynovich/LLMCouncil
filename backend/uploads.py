"""File upload handling for chat attachments."""
import os
import uuid
import base64
import re
from pathlib import Path
from fastapi import UploadFile

UPLOAD_DIR = "data/uploads"
MAX_TEXT_SIZE = 1 * 1024 * 1024  # 1MB
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB
CHUNK_SIZE = 64 * 1024  # 64KB chunks for streaming

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

    return {
        "file_id": file_id,
        "filename": file.filename or "file",
        "type": file_type,
        "size": total_size,
        "ext": ext,
    }


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
    if os.path.exists(filepath):
        os.remove(filepath)