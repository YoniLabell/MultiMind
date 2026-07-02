// MultiMind chat UI: conversation sidebar + per-message provider switching.

const PROVIDER_LABELS = {
  openai: 'OpenAI',
  claude: 'Claude',
  gemini: 'Gemini',
  grok: 'Grok',
  deepseek: 'DeepSeek',
};

const STORAGE_KEY = 'multimind:conversation-id';

const conversationListEl = document.getElementById('conversation-list');
const messagesEl = document.getElementById('messages');
const emptyStateEl = document.getElementById('empty-state');
const chatForm = document.getElementById('chat-form');
const messageInput = document.getElementById('message-input');
const providerSelect = document.getElementById('provider-select');
const sendBtn = document.getElementById('send-btn');
const newChatBtn = document.getElementById('new-chat-btn');

let currentConversationId = null;
let sending = false;

function relativeTime(isoString) {
  const seconds = (Date.now() - new Date(isoString).getTime()) / 1000;
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(isoString).toLocaleDateString();
}

function providerLabel(provider) {
  return PROVIDER_LABELS[provider] || provider;
}

// ---------- Sidebar ----------

async function refreshSidebar() {
  const conversations = await getConversations();
  conversationListEl.innerHTML = '';
  for (const conversation of conversations) {
    const li = document.createElement('li');
    li.className = 'conversation-item' + (conversation.id === currentConversationId ? ' active' : '');

    const main = document.createElement('button');
    main.type = 'button';
    main.className = 'conversation-open';
    const title = document.createElement('span');
    title.className = 'conversation-title';
    title.textContent = conversation.title || 'Untitled';
    const time = document.createElement('span');
    time.className = 'conversation-time';
    time.textContent = relativeTime(conversation.updated_at);
    main.append(title, time);
    main.addEventListener('click', () => openConversation(conversation.id));

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'conversation-delete';
    del.title = 'Delete conversation';
    del.textContent = '×';
    del.addEventListener('click', async (event) => {
      event.stopPropagation();
      if (!confirm('Delete this conversation?')) return;
      await deleteConversation(conversation.id);
      if (conversation.id === currentConversationId) startNewChat();
      refreshSidebar();
    });

    li.append(main, del);
    conversationListEl.appendChild(li);
  }
}

// ---------- Message rendering ----------

function clearMessages() {
  messagesEl.innerHTML = '';
}

function showEmptyState() {
  clearMessages();
  messagesEl.appendChild(emptyStateEl);
  emptyStateEl.style.display = '';
}

function appendUserMessage(content) {
  const div = document.createElement('div');
  div.className = 'message user';
  const body = document.createElement('div');
  body.className = 'bubble';
  body.textContent = content;
  div.appendChild(body);
  messagesEl.appendChild(div);
  scrollToBottom();
  return div;
}

function appendAssistantMessage(content, provider, model) {
  const div = document.createElement('div');
  div.className = 'message assistant';
  const meta = document.createElement('div');
  meta.className = 'meta';
  meta.textContent = `Assistant · ${providerLabel(provider)}${model ? ' · ' + model : ''}`;
  const body = document.createElement('div');
  body.className = 'bubble';
  body.textContent = content;
  div.append(meta, body);
  messagesEl.appendChild(div);
  scrollToBottom();
  return div;
}

function appendErrorCard(provider, errorText) {
  const div = document.createElement('div');
  div.className = 'message assistant error';
  const meta = document.createElement('div');
  meta.className = 'meta';
  meta.textContent = `${providerLabel(provider)} · failed`;
  const body = document.createElement('div');
  body.className = 'bubble';
  body.textContent = errorText;
  div.append(meta, body);
  messagesEl.appendChild(div);
  scrollToBottom();
  return div;
}

function appendSpinner(label) {
  const div = document.createElement('div');
  div.className = 'message assistant pending';
  const body = document.createElement('div');
  body.className = 'bubble';
  body.innerHTML = `<span class="spinner"></span> ${label}`;
  div.appendChild(body);
  messagesEl.appendChild(div);
  scrollToBottom();
  return div;
}

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

// ---------- Conversation state ----------

async function openConversation(conversationId) {
  try {
    const conversation = await getConversation(conversationId);
    currentConversationId = conversation.id;
    localStorage.setItem(STORAGE_KEY, String(conversation.id));
    clearMessages();
    if (conversation.messages.length === 0) {
      showEmptyState();
    } else {
      for (const message of conversation.messages) {
        if (message.role === 'user') {
          appendUserMessage(message.content);
        } else {
          appendAssistantMessage(message.content, message.provider, message.model);
        }
      }
    }
    refreshSidebar();
  } catch {
    // Stale/deleted conversation id — fall back to a fresh chat.
    startNewChat();
  }
}

function startNewChat() {
  currentConversationId = null;
  localStorage.removeItem(STORAGE_KEY);
  showEmptyState();
  refreshSidebar();
}

// ---------- Sending ----------

function setSending(isSending) {
  sending = isSending;
  sendBtn.disabled = isSending;
  providerSelect.disabled = isSending;
  sendBtn.textContent = isSending ? '…' : 'Send';
}

async function handleSend(event) {
  event.preventDefault();
  const message = messageInput.value.trim();
  if (!message || sending) return;
  const provider = providerSelect.value;

  emptyStateEl.style.display = 'none';
  appendUserMessage(message);
  messageInput.value = '';
  setSending(true);

  const spinnerLabel = provider === 'compare' ? 'Asking all providers…' : 'Thinking…';
  const spinner = appendSpinner(spinnerLabel);

  try {
    const result = await sendMessage(currentConversationId, message, provider);
    spinner.remove();

    currentConversationId = result.conversation_id;
    localStorage.setItem(STORAGE_KEY, String(currentConversationId));

    if (result.provider === 'compare') {
      for (const [name, answer] of Object.entries(result.answers)) {
        if (answer.error) {
          appendErrorCard(name, answer.error);
        } else {
          appendAssistantMessage(answer.content, name, answer.model);
        }
      }
    } else {
      const m = result.message;
      appendAssistantMessage(m.content, m.provider, m.model);
    }

    refreshSidebar();
  } catch (error) {
    spinner.remove();
    appendErrorCard(provider === 'auto' ? 'auto' : provider, error.message);
  } finally {
    setSending(false);
    messageInput.focus();
  }
}

// ---------- Init ----------

chatForm.addEventListener('submit', handleSend);
newChatBtn.addEventListener('click', startNewChat);
messageInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    chatForm.requestSubmit();
  }
});

(async function init() {
  await refreshSidebar();
  const savedId = localStorage.getItem(STORAGE_KEY);
  if (savedId) {
    await openConversation(Number(savedId));
  } else {
    showEmptyState();
  }
})();
