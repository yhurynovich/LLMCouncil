# File Attachments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add ability to attach text files and images to chat messages, with content prepended to the user's question.

**Architecture:** Frontend file picker uploads to backend `/api/upload`, returns file IDs. Message request includes file IDs. Backend reads file content and prepends to user message before sending to LLM council.

**Tech Stack:** Python 3.10+, FastAPI, httpx, React 19, Vite

## Global Constraints

- Backend runs on port 8001, frontend on port 5173
- Uploaded files stored in `data/uploads/` (gitignored)
- Max file size: 1MB for text, 10MB for images
- Supported text: .txt, .md, .csv, .json, .py, .js, .ts, .html, .css, .yaml, .yml, .toml, .xml, .log
- Supported images: .png, .jpg, .jpeg, .gif, .webp

---

## File Structure

| File | Action | Purpose |
|------|--------|---------|
| `backend/uploads.py` | Create | File upload handling, storage, content extraction |
| `backend/main.py` | Modify | Add upload endpoint, update message flow |
| `backend/council.py` | Modify | Accept file context in messages |
| `frontend/src/api.js` | Modify | Add uploadFile method |
| `frontend/src/components/ChatInterface.jsx` | Modify | File picker UI, drag & drop |
| `frontend/src/components/ChatInterface.css` | Modify | File attachment styles |

---

### Task 1: Backend Upload Handler

**Files:**
- Create: `backend/uploads.py`

**Interfaces:**
- Produces: `save_upload(file) -> dict`, `read_file_content(file_id) -> str`, `get_image_base64(file_id) -> str`

- [ ] **Step 1: Create backend/uploads.py**

```python
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
```

- [ ] **Step 2: Verify module loads**

Run: `cd /home/yury/Documents/LLMCouncil && .venv/bin/python -c "from backend.uploads import save_upload, read_file_content; print('OK')"`
Expected: `OK`

---

### Task 2: Backend Upload Endpoint

**Files:**
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: `backend.uploads.save_upload`
- Produces: `POST /api/upload` endpoint

- [ ] **Step 1: Add import to main.py**

Add after existing imports:
```python
from . import uploads
from fastapi import UploadFile, File
```

- [ ] **Step 2: Add upload endpoint**

Add after provider endpoints, before conversations:

```python
# ── File Uploads ────────────────────────────────────────────────────────────

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload a file for chat attachment."""
    try:
        result = await uploads.save_upload(file)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

- [ ] **Step 3: Restart backend and test**

Run: `cd /home/yury/Documents/LLMCouncil && kill $(lsof -t -i:8001) 2>/dev/null; sleep 1; setsid .venv/bin/python -m backend.main > /tmp/backend.log 2>&1 & sleep 2; echo "test" | curl -s -X POST http://localhost:8001/api/upload -F "file=@-;filename=test.txt" | python3 -m json.tool`
Expected: JSON with file_id, filename, type, size, ext

---

### Task 3: Backend — Prepend File Content to Messages

**Files:**
- Modify: `backend/main.py` (SendMessageRequest)
- Modify: `backend/council.py` (stage1_collect_responses)

**Interfaces:**
- Consumes: `uploads.read_file_content`, `uploads.get_image_base64`
- Produces: Updated message content with file context

- [ ] **Step 1: Update SendMessageRequest in main.py**

```python
class FileAttachment(BaseModel):
    file_id: str
    filename: str
    type: str  # "text" or "image"
    ext: str

class SendMessageRequest(BaseModel):
    content: str
    model_set: Optional[str] = None
    quick: bool = False
    files: List[FileAttachment] = []
```

- [ ] **Step 2: Add file context builder in council.py**

Add at the top of council.py after imports:

```python
from .uploads import read_file_content, get_image_base64


def build_message_with_files(user_query: str, files: list) -> tuple[str, list]:
    """Prepends file content to user query. Returns (text_message, image_urls)."""
    if not files:
        return user_query, []

    parts = []
    image_urls = []

    for f in files:
        if f.type == "text":
            content = read_file_content(f.file_id, f.ext)
            parts.append(f"File: {f.filename}\n```\n{content}\n```")
        elif f.type == "image":
            b64 = get_image_base64(f.file_id, f.ext)
            if b64:
                mime = f"image/{f.ext.lstrip('.')}"
                image_urls.append(f"data:{mime};base64,{b64}")

    if parts:
        return "\n\n".join(parts) + "\n\n" + user_query, image_urls
    return user_query, image_urls
```

- [ ] **Step 3: Update stage1_collect_responses to accept files**

```python
async def stage1_collect_responses(
    user_query: str,
    council_models: Optional[List[str]] = None,
    files: Optional[list] = None,
) -> List[Dict[str, Any]]:
    text_message, image_urls = build_message_with_files(user_query, files or [])

    messages = [{"role": "user", "content": text_message}]
    # TODO: Add image support when providers support vision
    # if image_urls:
    #     messages[0]["content"] = [
    #         {"type": "text", "text": text_message},
    #         {"type": "image_url", "image_url": {"url": image_urls[0]}}
    #     ]

    models = council_models if council_models is not None else COUNCIL_MODELS
    responses = await query_models_parallel(models, messages)

    stage1_results = []
    for model, response in responses.items():
        if response is not None:
            stage1_results.append({
                "model": model,
                "response": response.get('content', ''),
                "response_time": response.get('response_time'),
            })
    return stage1_results
```

- [ ] **Step 4: Update main.py stream endpoint to pass files**

In `send_message_stream`, update the stage1 call:

```python
stage1_results = await stage1_collect_responses(
    request.content, council_models, files=request.files
)
```

- [ ] **Step 5: Restart backend and test**

Run: `cd /home/yury/Documents/LLMCouncil && kill $(lsof -t -i:8001) 2>/dev/null; sleep 1; setsid .venv/bin/python -m backend.main > /tmp/backend.log 2>&1 & sleep 2; curl -s http://localhost:8001/ && echo " OK"`

---

### Task 4: Frontend — Upload API Method

**Files:**
- Modify: `frontend/src/api.js`

**Interfaces:**
- Produces: `api.uploadFile(file) -> Promise<{file_id, filename, type, size, ext}>`

- [ ] **Step 1: Add uploadFile method to api.js**

Add after `deleteProvider`:

```javascript
async uploadFile(file) {
    const formData = new FormData();
    formData.append('file', file);
    const response = await fetch(`${API_BASE}/api/upload`, {
        method: 'POST',
        body: formData,
    });
    if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to upload file');
    }
    return response.json();
},
```

---

### Task 5: Frontend — File Picker UI

**Files:**
- Modify: `frontend/src/components/ChatInterface.jsx`
- Modify: `frontend/src/components/ChatInterface.css`

**Interfaces:**
- Consumes: `api.uploadFile`
- Produces: File picker button, drag & drop, file preview chips

- [ ] **Step 1: Add file state and handlers to ChatInterface.jsx**

Add after `const [input, setInput] = useState('');`:

```javascript
const [attachedFiles, setAttachedFiles] = useState([]);
const [isDragging, setIsDragging] = useState(false);
const fileInputRef = useRef(null);
```

Add handlers after `handleKeyDown`:

```javascript
const handleFileSelect = async (e) => {
    const files = Array.from(e.target.files || []);
    for (const file of files) {
        try {
            const result = await api.uploadFile(file);
            setAttachedFiles(prev => [...prev, result]);
        } catch (err) {
            console.error('Failed to upload:', err);
        }
    }
    if (fileInputRef.current) fileInputRef.current.value = '';
};

const handleDragOver = (e) => {
    e.preventDefault();
    setIsDragging(true);
};

const handleDragLeave = () => setIsDragging(false);

const handleDrop = async (e) => {
    e.preventDefault();
    setIsDragging(false);
    const files = Array.from(e.dataTransfer.files);
    for (const file of files) {
        try {
            const result = await api.uploadFile(file);
            setAttachedFiles(prev => [...prev, result]);
        } catch (err) {
            console.error('Failed to upload:', err);
        }
    }
};

const removeFile = (fileId) => {
    setAttachedFiles(prev => prev.filter(f => f.file_id !== fileId));
};
```

Update `handleSubmit` to include files:

```javascript
const handleSubmit = (e) => {
    e.preventDefault();
    if ((input.trim() || attachedFiles.length > 0) && !isLoading) {
        onSendMessage(input, attachedFiles);
        setInput('');
        setAttachedFiles([]);
    }
};
```

- [ ] **Step 2: Update form JSX in ChatInterface.jsx**

Replace the form section:

```jsx
<form
    className={`input-form ${isDragging ? 'drag-over' : ''}`}
    onSubmit={handleSubmit}
    onDragOver={handleDragOver}
    onDragLeave={handleDragLeave}
    onDrop={handleDrop}
>
    {attachedFiles.length > 0 && (
        <div className="attached-files">
            {attachedFiles.map(f => (
                <div key={f.file_id} className="file-chip">
                    {f.type === 'image' ? '🖼' : '📄'}
                    <span className="file-chip-name">{f.filename}</span>
                    <button
                        type="button"
                        className="file-chip-remove"
                        onClick={() => removeFile(f.file_id)}
                    >
                        ×
                    </button>
                </div>
            ))}
        </div>
    )}
    <div className="input-row">
        <input
            type="file"
            ref={fileInputRef}
            onChange={handleFileSelect}
            multiple
            accept=".txt,.md,.csv,.json,.py,.js,.ts,.html,.css,.yaml,.yml,.toml,.xml,.log,.png,.jpg,.jpeg,.gif,.webp"
            style={{ display: 'none' }}
        />
        <button
            type="button"
            className="attach-button"
            onClick={() => fileInputRef.current?.click()}
            disabled={isLoading}
            title="Attach file"
        >
            📎
        </button>
        <textarea
            className="message-input"
            placeholder={isDragging ? "Drop files here..." : "Ask a follow-up... (Shift+Enter for new line, Enter to send)"}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isLoading}
            rows={3}
        />
        <div className="button-column">
            <button
                type="button"
                className={`quick-button ${quickMode ? 'active' : ''}`}
                onClick={() => onQuickModeChange(!quickMode)}
                disabled={isLoading}
                title={quickMode ? 'Quick mode ON: Skip peer ranking & synthesis' : 'Click to enable quick mode'}
            >
                ⚡
            </button>
            {isLoading ? (
                <button type="button" className="stop-button" onClick={onStop}>
                    Stop
                </button>
            ) : (
                <button
                    type="submit"
                    className="send-button"
                    disabled={!input.trim() && attachedFiles.length === 0}
                >
                    Send
                </button>
            )}
        </div>
    </div>
</form>
```

- [ ] **Step 3: Update App.jsx handleSendMessage to accept files**

```javascript
const handleSendMessage = async (content, files = []) => {
```

Pass files to API:

```javascript
await api.sendMessageStream(
    currentConversationId,
    content,
    (eventType, event) => { ... },
    activeModelSet,
    quickMode,
    controller.signal,
    files
);
```

- [ ] **Step 4: Update api.js sendMessageStream to accept files**

```javascript
async sendMessageStream(conversationId, content, onEvent, modelSet = null, quick = false, signal = null, files = []) {
    const body = { content, quick, files };
    if (modelSet) body.model_set = modelSet;
```

- [ ] **Step 5: Add CSS for file attachments**

Add to ChatInterface.css:

```css
.input-form.drag-over {
    border-color: #4a90e2;
    background: #eff6ff;
}

.input-row {
    display: flex;
    align-items: flex-end;
    gap: 12px;
    width: 100%;
}

.attach-button {
    width: 44px;
    height: 44px;
    border: 2px solid #d0d0d0;
    border-radius: 8px;
    background: #fff;
    font-size: 20px;
    cursor: pointer;
    transition: all 0.2s;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
}

.attach-button:hover {
    border-color: #4a90e2;
    background: #eff6ff;
}

.attached-files {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 8px;
}

.file-chip {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    background: #f3f4f6;
    border: 1px solid #e5e7eb;
    border-radius: 16px;
    font-size: 13px;
}

.file-chip-name {
    max-width: 150px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.file-chip-remove {
    background: none;
    border: none;
    color: #9ca3af;
    cursor: pointer;
    font-size: 16px;
    padding: 0 2px;
    line-height: 1;
}

.file-chip-remove:hover {
    color: #dc2626;
}
```

- [ ] **Step 6: Restart frontend and verify**

Run: `curl -s http://localhost:5173/src/components/ChatInterface.jsx | grep -c "attach-button"`
Expected: 1

---

### Task 6: Integration Test

**Files:** None (verification only)

- [ ] **Step 1: Test upload endpoint**

```bash
echo "Hello world" > /tmp/test.txt
curl -s -X POST http://localhost:8001/api/upload -F "file=@/tmp/test.txt" | python3 -m json.tool
```

Expected: JSON with file_id, filename, type="text", size, ext=".txt"

- [ ] **Step 2: Test message with file**

```bash
# Upload file first
FILE_RESULT=$(curl -s -X POST http://localhost:8001/api/upload -F "file=@/tmp/test.txt")
FILE_ID=$(echo $FILE_RESULT | python3 -c "import sys,json; print(json.load(sys.stdin)['file_id'])")
FILE_EXT=$(echo $FILE_RESULT | python3 -c "import sys,json; print(json.load(sys.stdin)['ext'])")

# Create conversation and send message with file
CONV_ID=$(curl -s -X POST http://localhost:8001/api/conversations -H "Content-Type: application/json" -d '{}' | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

curl -s -X POST "http://localhost:8001/api/conversations/$CONV_ID/message/stream" \
  -H "Content-Type: application/json" \
  -d "{\"content\":\"Summarize this file\",\"model_set\":\"free\",\"quick\":true,\"files\":[{\"file_id\":\"$FILE_ID\",\"filename\":\"test.txt\",\"type\":\"text\",\"ext\":\"$FILE_EXT\"}]}" \
  --max-time 60 | grep "stage1_complete" | head -1
```

Expected: stage1_complete event with response containing file content summary

- [ ] **Step 3: Verify file is prepended to message**

Check backend logs for the message content:
```bash
cat /tmp/backend.log | grep "File: test.txt" | head -1
```

Expected: Log showing file content was prepended
