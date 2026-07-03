// Frontend API layer for the MultiMind backend (same origin).

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!response.ok) {
    let detail;
    try {
      detail = (await response.json()).detail;
    } catch {
      detail = response.statusText;
    }
    const error = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    error.status = response.status;
    throw error;
  }
  return response.json();
}

// ---------- Auth ----------

function getMe() {
  return request('/auth/me');
}

function registerAccount(name, password) {
  return request('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ name, password }),
  });
}

function login(name, password) {
  return request('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ name, password }),
  });
}

function logout() {
  return request('/auth/logout', { method: 'POST' });
}

function createConversation(title) {
  return request('/conversations', {
    method: 'POST',
    body: JSON.stringify({ title: title || null }),
  });
}

function getConversations() {
  return request('/conversations');
}

function getConversation(conversationId) {
  return request(`/conversations/${conversationId}`);
}

function getModels() {
  return request('/models');
}

function sendMessage(conversationId, message, provider, model, models) {
  return request('/chat', {
    method: 'POST',
    body: JSON.stringify({
      conversation_id: conversationId,
      message,
      provider,
      model: model || null,
      models: models || null,
    }),
  });
}

function deleteConversation(conversationId) {
  return request(`/conversations/${conversationId}`, { method: 'DELETE' });
}
