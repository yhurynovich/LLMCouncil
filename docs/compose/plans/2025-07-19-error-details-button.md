# Error Details Button for Failed Models

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a button to see error details when a model fails to reply or errors out, displayed to the right of stage 1 tabs.

**Architecture:** Modify backend to include failed models with error information in stage1_results. Modify frontend Stage1 component to display error tabs with expandable error details.

**Tech Stack:** Python (FastAPI), React, CSS

## Global Constraints

- Backend runs on port 8001
- Frontend runs on port 5173 (Vite default)
- Environment variable prefix for Vite: `VITE_`

---

### Task 1: Modify backend to include failed models in stage1_results

**Covers:** Backend error reporting

**Files:**
- Modify: `backend/council.py:42-50`

**Interfaces:**
- Consumes: `responses` dict from `query_models_parallel`
- Produces: `stage1_results` list with both successful and failed models

- [ ] **Step 1: Update stage1_collect_responses to include failed models**

Replace lines 42-50 in `backend/council.py`:

```python
    stage1_results = []
    for model, response in responses.items():
        if response is not None:
            stage1_results.append({
                "model": model,
                "response": response.get('content', ''),
                "response_time": response.get('response_time'),
            })
        else:
            stage1_results.append({
                "model": model,
                "response": None,
                "error": "Model failed to respond",
            })
    return stage1_results
```

- [ ] **Step 2: Verify the change**

Run: `cd /home/yury/Documents/LLMCouncil && python -c "from backend.council import stage1_collect_responses; print('Import successful')"`

Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add backend/council.py
git commit -m "feat: include failed models with error info in stage1_results"
```

---

### Task 2: Update Stage1 component to handle error states

**Covers:** Frontend error display

**Files:**
- Modify: `frontend/src/components/Stage1.jsx`
- Modify: `frontend/src/components/Stage1.css`

**Interfaces:**
- Consumes: `responses` array with `error` field for failed models
- Produces: UI with error tabs and expandable error details

- [ ] **Step 1: Update Stage1.jsx to handle error states**

Replace the entire content of `frontend/src/components/Stage1.jsx`:

```jsx
import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import './Stage1.css';

export default function Stage1({ responses }) {
  const [activeTab, setActiveTab] = useState(0);
  const [expandedError, setExpandedError] = useState(null);

  if (!responses || !Array.isArray(responses) || responses.length === 0) {
    return null;
  }

  const formatTime = (seconds) => {
    if (!seconds && seconds !== 0) return '';
    return seconds < 1 ? `${Math.round(seconds * 1000)}ms` : `${seconds}s`;
  };

  const handleErrorClick = (index) => {
    setExpandedError(expandedError === index ? null : index);
  };

  return (
    <div className="stage stage1">
      <h3 className="stage-title">Stage 1: Individual Responses</h3>

      <div className="tabs">
        {responses.map((resp, index) => (
          <button
            key={index}
            className={`tab ${activeTab === index ? 'active' : ''} ${resp.error ? 'error' : ''}`}
            onClick={() => {
              setActiveTab(index);
              if (resp.error) {
                handleErrorClick(index);
              }
            }}
          >
            {resp.model ? (resp.model.split('/')[1] || resp.model) : 'Unknown'}
            {resp.error && <span className="error-indicator">!</span>}
            {!resp.error && resp.response_time != null && (
              <span className="tab-time">{formatTime(resp.response_time)}</span>
            )}
          </button>
        ))}
      </div>

      <div className="tab-content">
        {responses[activeTab].error ? (
          <div className="error-content">
            <div className="model-name error">
              {responses[activeTab].model || 'Unknown'}
              <span className="error-badge">Failed</span>
            </div>
            <div className="error-details">
              <div className="error-message">
                {responses[activeTab].error}
              </div>
              {expandedError === activeTab && (
                <div className="error-expandable">
                  <p>This model did not return a response. Possible reasons:</p>
                  <ul>
                    <li>API key invalid or missing</li>
                    <li>Model unavailable or rate limited</li>
                    <li>Network timeout</li>
                    <li>Invalid request format</li>
                  </ul>
                </div>
              )}
              <button
                className="error-toggle"
                onClick={() => handleErrorClick(activeTab)}
              >
                {expandedError === activeTab ? 'Hide Details' : 'Show Details'}
              </button>
            </div>
          </div>
        ) : (
          <>
            <div className="model-name">
              {responses[activeTab].model || 'Unknown'}
              {responses[activeTab].response_time != null && (
                <span className="response-time-badge">
                  {formatTime(responses[activeTab].response_time)}
                </span>
              )}
            </div>
            <div className="response-text markdown-content">
              <ReactMarkdown>{responses[activeTab].response || ''}</ReactMarkdown>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Add CSS styles for error states**

Append to `frontend/src/components/Stage1.css`:

```css
/* Error state styles */
.tab.error {
  border-bottom: 2px solid #e74c3c;
  color: #e74c3c;
}

.tab.error.active {
  background-color: #fdf2f2;
  border-bottom-color: #e74c3c;
}

.error-indicator {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  background-color: #e74c3c;
  color: white;
  border-radius: 50%;
  font-size: 10px;
  font-weight: bold;
  margin-left: 6px;
}

.error-content {
  padding: 16px;
  background-color: #fdf2f2;
  border-radius: 8px;
  border: 1px solid #fecaca;
}

.model-name.error {
  color: #e74c3c;
  display: flex;
  align-items: center;
  gap: 8px;
}

.error-badge {
  display: inline-block;
  padding: 2px 8px;
  background-color: #e74c3c;
  color: white;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
}

.error-details {
  margin-top: 12px;
}

.error-message {
  color: #991b1b;
  font-weight: 500;
}

.error-expandable {
  margin-top: 12px;
  padding: 12px;
  background-color: white;
  border-radius: 6px;
  border: 1px solid #fecaca;
}

.error-expandable p {
  margin: 0 0 8px 0;
  color: #666;
  font-size: 14px;
}

.error-expandable ul {
  margin: 0;
  padding-left: 20px;
  color: #666;
  font-size: 14px;
}

.error-expandable li {
  margin-bottom: 4px;
}

.error-toggle {
  margin-top: 12px;
  padding: 6px 12px;
  background-color: white;
  border: 1px solid #e74c3c;
  color: #e74c3c;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
  transition: all 0.2s;
}

.error-toggle:hover {
  background-color: #e74c3c;
  color: white;
}
```

- [ ] **Step 3: Verify the frontend builds**

Run: `cd /home/yury/Documents/LLMCouncil/frontend && npm run build`

Expected: Build succeeds with no errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Stage1.jsx frontend/src/components/Stage1.css
git commit -m "feat: add error details button for failed models in Stage1"
```

---

### Task 3: Verify complete setup

**Covers:** Integration testing

**Files:**
- None (verification only)

**Interfaces:**
- Consumes: All previous tasks
- Produces: Verified working configuration

- [ ] **Step 1: Verify backend includes failed models**

Run: `cd /home/yury/Documents/LLMCouncil && python -c "from backend.council import stage1_collect_responses; import asyncio; print(asyncio.run(stage1_collect_responses('test', ['invalid_model'])))"`

Expected: Output shows model with error field

- [ ] **Step 2: Verify frontend builds**

Run: `cd /home/yury/Documents/LLMCouncil/frontend && npm run build`

Expected: Build succeeds

- [ ] **Step 3: Final commit if any fixes needed**

```bash
git add -A
git commit -m "chore: verify error details button setup"
```
