/**
 * API client for the LLM Council backend.
 *
 * By default this uses relative paths (e.g. "/api/conversations"), so requests
 * go to whatever origin served the page, and nginx's /api/ proxy (see
 * Dockerfile.frontend) forwards them to the backend container internally.
 * This works unmodified whether you're on the LAN IP, localhost, or a
 * reverse-proxied domain like https://llmcouncil.htm.synology.me.
 *
 * Set VITE_API_URL at build time only if you need to point at a backend on a
 * different host/port than the one serving the frontend.
 */
const API_BASE = import.meta.env.VITE_API_URL || '';

// Frontend credentials (injected at build time via VITE_ env vars)
const FRONTEND_USERNAME = import.meta.env.VITE_AUTH_USERNAME || '';
const FRONTEND_PASSWORD = import.meta.env.VITE_AUTH_PASSWORD || '';

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

function validateCredentials(username, password) {
  return username === FRONTEND_USERNAME && password === FRONTEND_PASSWORD;
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

async function fetchWithAuth(url, options = {}) {
  const headers = {
    ...options.headers,
  };

  // Add Basic Auth if credentials are available
  if (authCredentials) {
    const token = btoa(`${authCredentials.username}:${authCredentials.password}`);
    headers['Authorization'] = `Basic ${token}`;
  }

  const response = await fetch(url, {
    ...options,
    headers,
  });

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

    let currentEventData = [];

    const processLine = (line) => {
      if (line.startsWith('data:')) {
        // SSE data field can span multiple lines - each line starts with "data:"
        currentEventData.push(line.slice(5).trim());
      } else if (line === '') {
        // Blank line ends the event - join multi-line data with \n
        if (currentEventData.length > 0) {
          eventData = currentEventData.join('\n');
          currentEventData = [];
          flushEvent();
        }
      } else if (line.startsWith('event:') || line.startsWith('id:') || line.startsWith('retry:')) {
        // Other SSE fields we ignore (we use JSON 'type' field instead)
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
            processLine(line);
          }
          // In case the event doesn't end with blank line but we have complete JSON
          if (currentEventData.length > 0) {
            eventData = currentEventData.join('\n');
            currentEventData = [];
            flushEvent();
          }
        }
      }
    } finally {
      reader.cancel().catch(() => {});
      // Process any remaining event
      if (currentEventData.length > 0) {
        eventData = currentEventData.join('\n');
        currentEventData = [];
        flushEvent();
      }
    }
  },
};

export function isAuthenticated() {
  return authCredentials !== null;
}

// Auth change listeners
const _authChangeListeners = new Set();

function notifyAuthChange() {
  _authChangeListeners.forEach(cb => cb());
}

export function onAuthChange(callback) {
  _authChangeListeners.add(callback);
  return () => _authChangeListeners.delete(callback);
}

// Simple in-memory rate limiter for login attempts
const _loginAttempts = new Map(); // ip -> [timestamps]
const MAX_LOGIN_ATTEMPTS = 5;
const LOGIN_WINDOW_MS = 5 * 60 * 1000; // 5 minutes

function getClientIP() {
  // In browser, we can't get real IP, but we can use a fingerprint
  // For simplicity, use a session-based identifier
  let fp = sessionStorage.getItem('llm_council_fingerprint');
  if (!fp) {
    fp = Math.random().toString(36).substring(2, 15);
    sessionStorage.setItem('llm_council_fingerprint', fp);
  }
  return fp;
}

function checkRateLimit() {
  const ip = getClientIP();
  const now = Date.now();
  const attempts = _loginAttempts.get(ip) || [];
  
  // Clean old attempts
  const recent = attempts.filter(t => now - t < 5 * 60 * 1000);
  
  if (recent.length >= 5) {
    return false;
  }
  
  recent.push(now);
  _loginAttempts.set(ip, recent);
  return true;
}

export function login(username, password) {
  return new Promise((resolve, reject) => {
    // Check rate limit
    if (!checkRateLimit()) {
      reject(new Error('Too many login attempts. Please try again in 5 minutes.'));
      return;
    }
    
    if (validateCredentials(username, password)) {
      setAuthCredentials(username, password);
      notifyAuthChange();
      resolve(true);
    } else {
      reject(new Error('Invalid credentials'));
    }
  });
}

export function logout() {
  clearAuthCredentials();
  notifyAuthChange();
}

// App.jsx calls these as api.isAuthenticated() / api.onAuthChange() / api.login() / api.logout(),
// so they need to live on the api object itself, not just as standalone named exports.
api.isAuthenticated = isAuthenticated;
api.onAuthChange = onAuthChange;
api.login = login;
api.logout = logout;

export { api as default };