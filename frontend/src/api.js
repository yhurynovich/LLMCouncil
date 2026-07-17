/**
 * API client for the LLM Council backend.
 */
const API_BASE = `${window.location.protocol}//${window.location.hostname}:5174`;

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

  //  Streaming 

  async sendMessageStream(conversationId, content, onEvent, modelSet = null) {
    const body = { content };
    if (modelSet) body.model_set = modelSet;

    const response = await fetch(
      `${API_BASE}/api/conversations/${conversationId}/message/stream`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
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