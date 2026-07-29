/**
 * API client for the LLM Council backend.
 */
const port = import.meta.env.VITE_BACKEND_PORT || '8001';
const API_BASE = `${window.location.protocol}//${window.location.hostname}:${port}`;

// Auth state
let authCredentials = null;

// Load credentials from sessionStorage on startup
try {
  const stored = sessionStorage.getItem('llm_council_auth');
  if (stored) {
    authCredentials = JSON.parse(stored);
  }
} catch (e) {
  // Ignore parse errors
}

export function setAuthCredentials(username, password) {
  authCredentials = { username, password };
  sessionStorage.setItem('llm_council_auth', JSON.stringify({ username, password }));
}

export function clearAuthCredentials() {
  authCredentials = null;
  sessionStorage.removeItem('llm_council_auth');
}

export function getAuthCredentials() {
  return authCredentials;
}

function getAuthHeader() {
  if (authCredentials) {
    const encoded = btoa(`${authCredentials.username}:${authCredentials.password}`);
    return `Basic ${encoded}`;
  }
  return null;
}

async function fetchWithAuth(url, options = {}) {
  const headers = {
    ...options.headers,
  };
  
  const authHeader = getAuthHeader();
  if (authHeader) {
    headers.Authorization = authHeader;
  }
  
  const response = await fetch(url, {
    ...options,
    headers,
  });
  
  // Handle 401 - clear credentials and notify
  if (response.status === 401) {
    clearAuthCredentials();
    throw new Error('UNAUTHORIZED');
  }
  
  return response;
}

export const api = {

  //  Conversations 

  async listConversations() {
    const response = await fetchWithAuth(`${API_BASE}/api/conversations`);
    if (!response.ok) throw new Error('Failed to list conversations');
    return response.json();
  },

  async createConversation() {
    const response = await fetchWithAuth(`${API_BASE}/api/conversations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    if (!response.ok) throw new Error('Failed to create conversation');
    return response.json();
  },

  async getConversation(conversationId, signal) {
    const response = await fetchWithAuth(`${API_BASE}/api/conversations/${conversationId}`, { signal });
    if (!response.ok) throw new Error('Failed to get conversation');
    return response.json();
  },

  async deleteConversation(conversationId) {
    const response = await fetchWithAuth(`${API_BASE}/api/conversations/${conversationId}`, {
      method: 'DELETE',
    });
    if (!response.ok) throw new Error('Failed to delete conversation');
    return response.json();
  },

  async renameConversation(conversationId, title) {
    const response = await fetchWithAuth(`${API_BASE}/api/conversations/${conversationId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    });
    if (!response.ok) throw new Error('Failed to rename conversation');
    return response.json();
  },

  //  Model Sets 

  async listModelSets() {
    const response = await fetchWithAuth(`${API_BASE}/api/model-sets`);
    if (!response.ok) throw new Error('Failed to list model sets');
    return response.json(); // { sets: {...}, active: "free" }
  },

  async setActiveModelSet(setId) {
    const response = await fetchWithAuth(`${API_BASE}/api/model-sets/active`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ set_id: setId }),
    });
    if (!response.ok) throw new Error('Failed to set model set');
    return response.json();
  },

  async createModelSet(data) {
    const response = await fetchWithAuth(`${API_BASE}/api/model-sets`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to create model set');
    }
    return response.json();
  },

  async updateModelSet(setId, data) {
    const response = await fetchWithAuth(`${API_BASE}/api/model-sets/${encodeURIComponent(setId)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to update model set');
    }
    return response.json();
  },

  async deleteModelSet(setId) {
    const response = await fetchWithAuth(`${API_BASE}/api/model-sets/${encodeURIComponent(setId)}`, {
      method: 'DELETE',
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to delete model set');
    }
    return response.json();
  },

  async listAvailableModels() {
    const response = await fetchWithAuth(`${API_BASE}/api/available-models`);
    if (!response.ok) throw new Error('Failed to fetch available models');
    return response.json();
  },

  //  Providers

  async listProviders() {
    const response = await fetchWithAuth(`${API_BASE}/api/providers`);
    if (!response.ok) throw new Error('Failed to list providers');
    return response.json();
  },

  async createProvider(data) {
    const response = await fetchWithAuth(`${API_BASE}/api/providers`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to create provider');
    }
    return response.json();
  },

  async updateProvider(name, data) {
    const response = await fetchWithAuth(`${API_BASE}/api/providers/${encodeURIComponent(name)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to update provider');
    }
    return response.json();
  },

  async deleteProvider(name) {
    const response = await fetchWithAuth(`${API_BASE}/api/providers/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to delete provider');
    }
    return response.json();
  },

  //  File Uploads

  async uploadFile(file) {
    const formData = new FormData();
    formData.append('file', file);
    const response = await fetchWithAuth(`${API_BASE}/api/upload`, {
      method: 'POST',
      body: formData,
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to upload file');
    }
    return response.json();
  },

  //  Streaming

  async sendMessageStream(conversationId, content, onEvent, modelSet = null, quick = false, signal = null, files = []) {
    const body = { content, quick, files };
    if (modelSet) body.model_set = modelSet;

    const response = await fetchWithAuth(
      `${API_BASE}/api/conversations/${conversationId}/message/stream`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal,
      }
    );

    if (!response.ok) throw new Error('Failed to send message');

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    let eventData = '';

    const flushEvent = () => {
      if (eventData) {
        try {
          const event = JSON.parse(eventData);
          onEvent(event.type, event);
        } catch (e) {
          console.error('SSE parse error:', e, eventData);
        }
        eventData = '';
      }
    };

    try {
      while (!signal?.aborted) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        let eventEnd;
        while ((eventEnd = buffer.indexOf('\n\n')) !== -1) {
          const event = buffer.slice(0, eventEnd);
          buffer = buffer.slice(eventEnd + 2);
          const lines = event.split('\n');
          for (const line of lines) {
            if (line.startsWith('data:')) {
              eventData += (eventData ? '\n' : '') + line.slice(5).trim();
            }
            // ignore event:, id:, retry: - we use JSON 'type' field instead
          }
          // Flush on blank line (end of SSE event)
          flushEvent();
        }
      }
    } finally {
      reader.cancel().catch(() => {});
      // Process any remaining event
      if (eventData) {
        flushEvent();
      }
    }
  },
};

export { api as default };