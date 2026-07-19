"""File upload handling for chat attachments."""
import os
import uuid
import base64
from pathlib import Path
from fastapi import UploadFile

UPLOAD_DIR = "data/uploads"
MAX_TEXT_SIZE = 1 * 1024 * 1024  # 1MB
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".html", ".css", ".yaml", ".yml", ".toml", ".xml", ".log"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _ensure_upload_dir():
    Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)


def _get_file_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in TEXT_EXTENSIONS:
        return "text"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    return "unknown"


async def save_upload(file: UploadFile) -> dict:
    """Save uploaded file and return metadata."""
    _ensure_upload_dir()

    ext = Path(file.filename or "file").suffix.lower()
    file_id = str(uuid.uuid4())
    filename = f"{file_id}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    content = await file.read()
    file_type = _get_file_type(file.filename or "")

    max_size = MAX_IMAGE_SIZE if file_type == "image" else MAX_TEXT_SIZE
    if len(content) > max_size:
        raise ValueError(f"File too large. Max size: {max_size // (1024*1024)}MB")

    with open(filepath, "wb") as f:
        f.write(content)

    return {
        "file_id": file_id,
        "filename": file.filename or "file",
        "type": file_type,
        "size": len(content),
        "ext": ext,
    }


def read_file_content(file_id: str, ext: str) -> str:
    """Read text file content."""
    filepath = os.path.join(UPLOAD_DIR, f"{file_id}{ext}")
    if not os.path.exists(filepath):
        return ""
    with open(filepath, "r", errors="replace") as f:
        return f.read()


def get_image_base64(file_id: str, ext: str) -> str:
    """Read image file and return base64 encoded string."""
    filepath = os.path.join(UPLOAD_DIR, f"{file_id}{ext}")
    if not os.path.exists(filepath):
        return ""
    with open(filepath, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def delete_upload(file_id: str, ext: str):
    """Delete an uploaded file."""
    filepath = os.path.join(UPLOAD_DIR, f"{file_id}{ext}")
    if os.path.exists(filepath):
        os.remove(filepath)
