"""JSON-based storage for conversations."""

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path
from .config import DATA_DIR

logger = logging.getLogger(__name__)

MAX_CONVERSATIONS = 50
# UUID v4 regex for validation
UUID_V4_REGEX = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$', re.IGNORECASE)

# Per-conversation locks for thread-safe read-modify-write
_conversation_locks: Dict[str, asyncio.Lock] = {}
_locks_lock = asyncio.Lock()


def ensure_data_dir():
    """Ensure the data directory exists."""
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)


def _validate_conversation_id(conversation_id: str) -> bool:
    """Validate conversation_id is a valid UUID v4."""
    return bool(UUID_V4_REGEX.match(conversation_id))


def get_conversation_path(conversation_id: str) -> str:
    """Get the file path for a conversation. Validates ID format."""
    if not conversation_id or not _validate_conversation_id(conversation_id):
        raise ValueError(f"Invalid conversation ID: {conversation_id}")
    return os.path.join(DATA_DIR, f"{conversation_id}.json")


async def _get_conversation_lock(conversation_id: str) -> asyncio.Lock:
    """Get or create a lock for a specific conversation."""
    global _conversation_locks
    async with _locks_lock:
        if conversation_id not in _conversation_locks:
            _conversation_locks[conversation_id] = asyncio.Lock()
        return _conversation_locks[conversation_id]


async def enforce_retention_async():
    """Delete oldest conversations beyond MAX_CONVERSATIONS asynchronously."""
    global _conversation_locks
    async with _retention_lock:
        ensure_data_dir()
        files = []
        for f in os.listdir(DATA_DIR):
            if f.endswith(".json"):
                path = os.path.join(DATA_DIR, f)
                try:
                    import aiofiles
                    async with aiofiles.open(path, "r") as fh:
                        content = await fh.read()
                        data = json.loads(content)
                    files.append((data.get("created_at", ""), path))
                except (json.JSONDecodeError, OSError):
                    continue

        if len(files) <= MAX_CONVERSATIONS:
            return

        files.sort(key=lambda x: x[0])  # oldest first
        to_delete = files[: len(files) - MAX_CONVERSATIONS]
        
        # Remove deleted conversation locks
        async with _locks_lock:
            for _, path in to_delete:
                filename = os.path.basename(path)
                conversation_id = filename[:-5]  # remove .json
                if conversation_id in _conversation_locks:
                    del _conversation_locks[conversation_id]
        
        for _, path in to_delete:
            try:
                os.remove(path)
            except OSError:
                pass


def create_conversation(conversation_id: str) -> Dict[str, Any]:
    """
    Create a new conversation.

    Args:
        conversation_id: Unique identifier for the conversation

    Returns:
        New conversation dict
    """
    ensure_data_dir()

    conversation = {
        "id": conversation_id,
        "created_at": datetime.utcnow().isoformat(),
        "title": "New Conversation",
        "messages": []
    }

    # Save to file atomically
    path = get_conversation_path(conversation_id)
    tmp = path + ".tmp"
    with open(tmp, 'w') as f:
        json.dump(conversation, f, indent=2)
    os.replace(tmp, path)

    # Enforce retention limit
    enforce_retention()

    return conversation


async def create_conversation_async(conversation_id: str) -> Dict[str, Any]:
    """Create a new conversation asynchronously."""
    import aiofiles
    ensure_data_dir()

    conversation = {
        "id": conversation_id,
        "created_at": datetime.utcnow().isoformat(),
        "title": "New Conversation",
        "messages": []
    }

    # Save to file atomically
    path = get_conversation_path(conversation_id)
    tmp = path + ".tmp"
    async with aiofiles.open(tmp, 'w') as f:
        await f.write(json.dumps(conversation, indent=2))
    os.replace(tmp, path)

    # Enforce retention limit
    enforce_retention()

    return conversation


def get_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    """
    Load a conversation from storage.

    Args:
        conversation_id: Unique identifier for the conversation

    Returns:
        Conversation dict or None if not found
    """
    path = get_conversation_path(conversation_id)

    if not os.path.exists(path):
        return None

    with open(path, 'r') as f:
        return json.load(f)


async def get_conversation_async(conversation_id: str) -> Optional[Dict[str, Any]]:
    """Load a conversation from storage asynchronously."""
    import aiofiles
    path = get_conversation_path(conversation_id)

    if not os.path.exists(path):
        return None

    async with aiofiles.open(path, 'r') as f:
        content = await f.read()
        return json.loads(content)


def save_conversation(conversation: Dict[str, Any]):
    """
    Save a conversation to storage atomically.

    Args:
        conversation: Conversation dict to save
    """
    ensure_data_dir()

    path = get_conversation_path(conversation['id'])
    tmp = path + ".tmp"
    with open(tmp, 'w') as f:
        json.dump(conversation, f, indent=2)
    os.replace(tmp, path)


async def save_conversation_async(conversation: Dict[str, Any]):
    """Save a conversation to storage atomically asynchronously."""
    import aiofiles
    ensure_data_dir()

    path = get_conversation_path(conversation['id'])
    tmp = path + ".tmp"
    async with aiofiles.open(tmp, 'w') as f:
        await f.write(json.dumps(conversation, indent=2))
    os.replace(tmp, path)


def list_conversations() -> List[Dict[str, Any]]:
    """
    List all conversations (metadata only).

    Returns:
        List of conversation metadata dicts
    """
    ensure_data_dir()

    conversations = []
    for filename in os.listdir(DATA_DIR):
        if filename.endswith('.json'):
            path = os.path.join(DATA_DIR, filename)
            try:
                with open(path, 'r') as f:
                    data = json.load(f)
                conversations.append({
                    "id": data["id"],
                    "created_at": data["created_at"],
                    "title": data.get("title", "New Conversation"),
                    "message_count": len(data["messages"])
                })
            except (json.JSONDecodeError, KeyError, OSError) as e:
                logger.warning("Skipping corrupt conversation file %s: %s", filename, e)
                continue

    # Sort by creation time, newest first
    conversations.sort(key=lambda x: x["created_at"], reverse=True)

    return conversations


async def list_conversations_async() -> List[Dict[str, Any]]:
    """List all conversations asynchronously."""
    import aiofiles
    ensure_data_dir()

    conversations = []
    for filename in os.listdir(DATA_DIR):
        if filename.endswith('.json'):
            path = os.path.join(DATA_DIR, filename)
            try:
                async with aiofiles.open(path, 'r') as f:
                    content = await f.read()
                    data = json.loads(content)
                conversations.append({
                    "id": data["id"],
                    "created_at": data["created_at"],
                    "title": data.get("title", "New Conversation"),
                    "message_count": len(data["messages"])
                })
            except (json.JSONDecodeError, KeyError, OSError) as e:
                logger.warning("Skipping corrupt conversation file %s: %s", filename, e)
                continue

    # Sort by creation time, newest first
    conversations.sort(key=lambda x: x["created_at"], reverse=True)

    return conversations


def add_user_message(conversation_id: str, content: str):
    """
    Add a user message to a conversation.

    Args:
        conversation_id: Conversation identifier
        content: User message content
    """
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")

    conversation["messages"].append({
        "role": "user",
        "content": content
    })

    save_conversation(conversation)
    enforce_retention()


async def add_user_message_async(conversation_id: str, content: str):
    """Add a user message to a conversation asynchronously with lock."""
    lock = await _get_conversation_lock(conversation_id)
    async with lock:
        conversation = await get_conversation_async(conversation_id)
        if conversation is None:
            raise ValueError(f"Conversation {conversation_id} not found")

        conversation["messages"].append({
            "role": "user",
            "content": content
        })

        await save_conversation_async(conversation)


def add_assistant_message(
    conversation_id: str,
    stage1: List[Dict[str, Any]],
    stage2: List[Dict[str, Any]],
    stage3: Dict[str, Any]
):
    """
    Add an assistant message with all 3 stages to a conversation.

    Args:
        conversation_id: Conversation identifier
        stage1: List of individual model responses
        stage2: List of model rankings
        stage3: Final synthesized response
    """
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")

    conversation["messages"].append({
        "role": "assistant",
        "stage1": stage1,
        "stage2": stage2,
        "stage3": stage3
    })

    save_conversation(conversation)
    enforce_retention()


async def add_assistant_message_async(
    conversation_id: str,
    stage1: List[Dict[str, Any]],
    stage2: List[Dict[str, Any]],
    stage3: Dict[str, Any]
):
    """Add an assistant message with all 3 stages asynchronously with lock."""
    lock = await _get_conversation_lock(conversation_id)
    async with lock:
        conversation = await get_conversation_async(conversation_id)
        if conversation is None:
            raise ValueError(f"Conversation {conversation_id} not found")

        conversation["messages"].append({
            "role": "assistant",
            "stage1": stage1,
            "stage2": stage2,
            "stage3": stage3
        })

        await save_conversation_async(conversation)


def update_conversation_title(conversation_id: str, title: str):
    """
    Update the title of a conversation.

    Args:
        conversation_id: Conversation identifier
        title: New title for the conversation
    """
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")

    conversation["title"] = title
    save_conversation(conversation)


async def update_conversation_title_async(conversation_id: str, title: str):
    """Update the title of a conversation asynchronously with lock."""
    lock = await _get_conversation_lock(conversation_id)
    async with lock:
        conversation = await get_conversation_async(conversation_id)
        if conversation is None:
            raise ValueError(f"Conversation {conversation_id} not found")

        conversation["title"] = title
        await save_conversation_async(conversation)


def delete_conversation(conversation_id: str) -> bool:
    """Delete a conversation file. Returns True if deleted."""
    path = get_conversation_path(conversation_id)
    if not os.path.exists(path):
        return False
    os.remove(path)
    return True


async def delete_conversation_async(conversation_id: str) -> bool:
    """Delete a conversation file asynchronously."""
    path = get_conversation_path(conversation_id)
    if not os.path.exists(path):
        return False
    os.remove(path)
    
    # Clean up the conversation lock to prevent memory leak
    global _conversation_locks
    async with _locks_lock:
        if conversation_id in _conversation_locks:
            del _conversation_locks[conversation_id]
    return True