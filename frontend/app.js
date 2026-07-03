// MultiMind chat UI: conversation sidebar, per-message provider + model switching.

const PROVIDER_LABELS = {
  openai: 'OpenAI',
  claude: 'Claude',
  gemini: 'Gemini',
  grok: 'Grok',
  deepseek: 'DeepSeek',
};

const STORAGE_KEY = 'multimind:conversation-id';
const MODELS_KEY = 'multimind:models';

const conversationListEl = document.getElementById('conversation-list');
const messagesEl = document.getElementById('messages');
const emptyStateEl = document.getElementById('empty-state');
const chatForm = document.getElementById('chat-form');
const messageInput = document.getElementById('message-input');
const providerSelect = document.getElementById('provider-select');
const modelSelect = document.getElementById('model-select');
const sendBtn = document.getElementById('send-btn');
const newChatBtn = document.getElementById('new-chat-btn');
const topbarNewBtn = document.getElementById('topbar-new-btn');
const menuBtn = document.getElementById('menu-btn');
const sidebar = document.getElementById('sidebar');
const backdrop = document.getElementById('backdrop');

const authOverlay = document.getElementById('auth-overlay');
const authError = document.getElementById('auth-error');
const authForm = document.getElementById('auth-form');
const authNameInput = document.getElementById('auth-name');
const authPasswordInput = document.getElementById('auth-password');
const registerBtn = document.getElementById('register-btn');
const userArea = document.getElementById('user-area');

let currentConversationId = null;
let sending = false;
let modelCatalog = {};            // provider -> {label, default, models[]}
let selectedModels = {};          // provider -> chosen model (persisted)
let currentUser = null;

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

// ---------- Auth ----------

function showAuthOverlay() {
  currentUser = null;
  authOverlay.classList.remove('hidden');
  authNameInput.focus();
}

function hideAuthOverlay() {
  authOverlay.classList.add('hidden');
  authError.textContent = '';
  authForm.reset();
}

function isUnauthorized(error) {
  return error && error.status === 401;
}

async function submitAuth(apiCall) {
  authError.textContent = '';
  try {
    const result = await apiCall(authNameInput.value.trim(), authPasswordInput.value);
    await signIn(result.user);
  } catch (error) {
    authError.textContent = error.message;
  }
}

authForm.addEventListener('submit', (event) => {
  event.preventDefault();
  submitAuth(login);
});

registerBtn.addEventListener('click', () => {
  if (!authForm.reportValidity()) return;
  submitAuth(registerAccount);
});

async function signIn(user) {
  currentUser = user;
  hideAuthOverlay();
  renderUserArea();
  await bootData();
}

function renderUserArea() {
  userArea.innerHTML = '';
  if (!currentUser) return;
  const chip = document.createElement('div');
  chip.className = 'user-chip';
  const avatar = document.createElement('span');
  avatar.className = 'avatar';
  avatar.textContent = (currentUser.name || '?').charAt(0).toUpperCase();
  const name = document.createElement('span');
  name.className = 'user-name';
  name.textContent = currentUser.name || 'Signed in';
  chip.append(avatar, name);

  const signOutBtn = document.createElement('button');
  signOutBtn.type = 'button';
  signOutBtn.className = 'signout-btn';
  signOutBtn.textContent = 'Sign out';
  signOutBtn.addEventListener('click', async () => {
    try { await logout(); } catch { /* session already gone */ }
    localStorage.removeItem(STORAGE_KEY);
    currentConversationId = null;
    conversationListEl.innerHTML = '';
    showEmptyState();
    renderUserArea();
    closeDrawer();
    showAuthOverlay();
  });

  userArea.append(chip, signOutBtn);
}

// ---------- Mobile drawer ----------

function openDrawer() {
  sidebar.classList.add('open');
  backdrop.classList.add('visible');
}

function closeDrawer() {
  sidebar.classList.remove('open');
  backdrop.classList.remove('visible');
}

menuBtn.addEventListener('click', openDrawer);
backdrop.addEventListener('click', closeDrawer);

// ---------- Model selection ----------

function loadSelectedModels() {
  try {
    selectedModels = JSON.parse(localStorage.getItem(MODELS_KEY)) || {};
  } catch {
    selectedModels = {};
  }
}

function saveSelectedModels() {
  localStorage.setItem(MODELS_KEY, JSON.stringify(selectedModels));
}

function modelFor(provider) {
  const info = modelCatalog[provider];
  if (!info) return null;
  const chosen = selectedModels[provider];
  return chosen && info.models.includes(chosen) ? chosen : info.default;
}

function updateModelSelect() {
  const provider = providerSelect.value;
  modelSelect.innerHTML = '';
  if (provider === 'auto' || provider === 'compare' || !modelCatalog[provider]) {
    modelSelect.classList.add('hidden');
    return;
  }
  modelSelect.classList.remove('hidden');
  for (const model of modelCatalog[provider].models) {
    const option = document.createElement('option');
    option.value = model;
    option.textContent = model;
    modelSelect.appendChild(option);
  }
  modelSelect.value = modelFor(provider);
}

providerSelect.addEventListener('change', updateModelSelect);
modelSelect.addEventListener('change', () => {
  selectedModels[providerSelect.value] = modelSelect.value;
  saveSelectedModels();
});

// ---------- Sidebar ----------

async function refreshSidebar() {
  let conversations;
  try {
    conversations = await getConversations();
  } catch (error) {
    if (isUnauthorized(error)) showAuthOverlay();
    return;
  }
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
    main.addEventListener('click', () => {
      closeDrawer();
      openConversation(conversation.id);
    });

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
  const badge = document.createElement('span');
  badge.className = `badge ${provider || ''}`;
  badge.textContent = providerLabel(provider);
  meta.appendChild(badge);
  if (model) {
    const modelEl = document.createElement('span');
    modelEl.className = 'model-name';
    modelEl.textContent = model;
    meta.appendChild(modelEl);
  }
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
  const badge = document.createElement('span');
  badge.className = `badge ${provider || ''}`;
  badge.textContent = providerLabel(provider);
  const failed = document.createElement('span');
  failed.className = 'model-name';
  failed.textContent = 'failed';
  meta.append(badge, failed);
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
  } catch (error) {
    if (isUnauthorized(error)) {
      showAuthOverlay();
      return;
    }
    // Stale/deleted conversation id — fall back to a fresh chat.
    startNewChat();
  }
}

function startNewChat() {
  currentConversationId = null;
  localStorage.removeItem(STORAGE_KEY);
  showEmptyState();
  refreshSidebar();
  closeDrawer();
}

// ---------- Sending ----------

function setSending(isSending) {
  sending = isSending;
  sendBtn.disabled = isSending;
  providerSelect.disabled = isSending;
  modelSelect.disabled = isSending;
}

function compareModelsMap() {
  const map = {};
  for (const provider of Object.keys(modelCatalog)) {
    const model = modelFor(provider);
    if (model) map[provider] = model;
  }
  return map;
}

async function handleSend(event) {
  event.preventDefault();
  const message = messageInput.value.trim();
  if (!message || sending) return;
  const provider = providerSelect.value;
  const isConcrete = provider !== 'auto' && provider !== 'compare';
  const model = isConcrete ? modelFor(provider) : null;
  const models = provider === 'compare' ? compareModelsMap() : null;

  emptyStateEl.style.display = 'none';
  appendUserMessage(message);
  messageInput.value = '';
  messageInput.style.height = 'auto';
  setSending(true);

  const spinnerLabel = provider === 'compare' ? 'Asking all five providers…' : 'Thinking…';
  const spinner = appendSpinner(spinnerLabel);

  try {
    const result = await sendMessage(currentConversationId, message, provider, model, models);
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
    if (isUnauthorized(error)) {
      showAuthOverlay();
    } else {
      appendErrorCard(provider === 'auto' ? 'auto' : provider, error.message);
    }
  } finally {
    setSending(false);
    messageInput.focus();
  }
}

// ---------- Init ----------

chatForm.addEventListener('submit', handleSend);
newChatBtn.addEventListener('click', startNewChat);
topbarNewBtn.addEventListener('click', startNewChat);
messageInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    chatForm.requestSubmit();
  }
});
messageInput.addEventListener('input', () => {
  messageInput.style.height = 'auto';
  messageInput.style.height = Math.min(messageInput.scrollHeight, 160) + 'px';
});

async function bootData() {
  loadSelectedModels();
  try {
    const catalog = await getModels();
    modelCatalog = catalog.providers;
  } catch {
    modelCatalog = {};
  }
  updateModelSelect();

  await refreshSidebar();
  const savedId = localStorage.getItem(STORAGE_KEY);
  if (savedId) {
    await openConversation(Number(savedId));
  } else {
    showEmptyState();
  }
}

(async function init() {
  try {
    const me = await getMe();
    await signIn(me.user);
  } catch {
    showEmptyState();
    showAuthOverlay();
  }
})();
