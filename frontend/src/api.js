/**
 * API client for the LLM Council backend.
 */
const port = import.meta.env.VITE_BACKEND_PORT || '8001';
const API_BASE = `${window.location.protocol}//${window.location.hostname}:${port}`;

export const api = {

  //  Conversations 

  async listConversations() {
    const response = await fetch(`${API_BASE}/api/conversations`);
    if (!response.ok) throw new Error('Failed to list conversations');
    return response.json();
  },

  async createConversation() {
    const response = await fetch(`${API_BASE}/api/conversations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    if (!response.ok) throw new Error('Failed to create conversation');
    return response.json();
  },

  async getConversation(conversationId) {
    const response = await fetch(`${API_BASE}/api/conversations/${conversationId}`);
    if (!response.ok) throw new Error('Failed to get conversation');
    return response.json();
  },

  async deleteConversation(conversationId) {
    const response = await fetch(`${API_BASE}/api/conversations/${conversationId}`, {
      method: 'DELETE',
    });
    if (!response.ok) throw new Error('Failed to delete conversation');
    return response.json();
  },

  //  Model Sets 

  async listModelSets() {
    const response = await fetch(`${API_BASE}/api/model-sets`);
    if (!response.ok) throw new Error('Failed to list model sets');
    return response.json(); // { sets: {...}, active: "free" }
  },

  async setActiveModelSet(setId) {
    const response = await fetch(`${API_BASE}/api/model-sets/active`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ set_id: setId }),
    });
    if (!response.ok) throw new Error('Failed to set model set');
    return response.json();
  },

  async createModelSet(data) {
    const response = await fetch(`${API_BASE}/api/model-sets`, {
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
    const response = await fetch(`${API_BASE}/api/model-sets/${encodeURIComponent(setId)}`, {
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
    const response = await fetch(`${API_BASE}/api/model-sets/${encodeURIComponent(setId)}`, {
      method: 'DELETE',
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to delete model set');
    }
    return response.json();
  },

  async listAvailableModels() {
    const response = await fetch(`${API_BASE}/api/available-models`);
    if (!response.ok) throw new Error('Failed to fetch available models');
    return response.json();
  },

  //  Providers

  async listProviders() {
    const response = await fetch(`${API_BASE}/api/providers`);
    if (!response.ok) throw new Error('Failed to list providers');
    return response.json();
  },

  async createProvider(data) {
    const response = await fetch(`${API_BASE}/api/providers`, {
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
    const response = await fetch(`${API_BASE}/api/providers/${encodeURIComponent(name)}`, {
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
    const response = await fetch(`${API_BASE}/api/providers/${encodeURIComponent(name)}`, {
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

  //  Streaming

  async sendMessageStream(conversationId, content, onEvent, modelSet = null, quick = false, signal = null, files = []) {
    const body = { content, quick, files };
    if (modelSet) body.model_set = modelSet;

    const response = await fetch(
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

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const chunk = decoder.decode(value);
      const lines = chunk.split('\n');

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const event = JSON.parse(line.slice(6));
            onEvent(event.type, event);
          } catch (e) {
            console.error('Failed to parse SSE event:', e);
          }
        }
      }
    }
  },
};