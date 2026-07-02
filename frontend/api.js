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
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return response.json();
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

function sendMessage(conversationId, message, provider) {
  return request('/chat', {
    method: 'POST',
    body: JSON.stringify({ conversation_id: conversationId, message, provider }),
  });
}

function deleteConversation(conversationId) {
  return request(`/conversations/${conversationId}`, { method: 'DELETE' });
}
