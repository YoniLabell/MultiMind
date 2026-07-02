# MultiMind

A multi-provider AI chat app with **persistent conversations** and **per-message provider switching**. A conversation is a shared chat history — it is *not* tied to one provider. You can ask OpenAI a question, follow up with Claude, then compare all five providers side by side, all in the same thread.

Supported providers: **OpenAI, Claude (Anthropic), Gemini (Google), Grok (xAI), DeepSeek** — plus **Auto** (server default) and **Compare** (all five at once).

## How it works

### Switching providers inside a conversation

Every message you send carries a `provider` field. The backend loads the conversation's shared history from SQLite, formats it for the chosen provider, and calls it. Each **assistant** message records which `provider` and `model` actually produced it (e.g. `Assistant · OpenAI · gpt-4.1-mini`); **user** messages store `provider = NULL`.

Before calling a provider, the backend:

1. Takes only the **latest 20 messages** in chronological order.
2. Sends only `role` and `content` (never provider/model metadata).
3. **Merges consecutive assistant messages** (produced by compare mode) into one assistant message, because some APIs reject non-alternating roles:

   ```
   [openai]: first answer

   [claude]: second answer
   ```

4. Converts to each provider's wire format inside that provider's function (Gemini's `model` role, Anthropic's `system` parameter, etc.). The DB format stays uniform.

### Compare mode

`provider = "compare"` saves your message once, calls **all five providers concurrently**, and saves each successful answer as its own assistant message (with its own provider/model). Partial failures don't fail the request — failed providers return an `{"error": "..."}` entry and are rendered as small error cards in the UI.

### Persistence

Chat history is stored in **SQLite** and survives page reloads and server restarts. Schema summary:

```sql
conversations (id, title, created_at, updated_at)
messages      (id, conversation_id → conversations.id ON DELETE CASCADE,
               role IN ('user','assistant','system'),
               provider,   -- NULL for user messages
               model,      -- actual model that answered, e.g. gpt-4.1-mini
               content, created_at)
```

Adding a message bumps the conversation's `updated_at`; the sidebar is ordered by it. A conversation created without a title gets one auto-generated from the first ~50 characters of the first user message. The frontend remembers the active conversation in `localStorage` and restores it on reload.

## API

| Method & path | Purpose |
|---|---|
| `POST /conversations` | Create a conversation (`{"title": "optional"}`) |
| `GET /conversations` | List conversations, newest activity first |
| `GET /conversations/{id}` | Conversation + all messages (ascending) |
| `DELETE /conversations/{id}` | Delete a conversation and its messages |
| `POST /chat` | `{"conversation_id": 1 \| null, "message": "...", "provider": "auto\|openai\|claude\|gemini\|grok\|deepseek\|compare"}` — omitting `conversation_id` auto-creates a conversation |

Errors: `404` for unknown `conversation_id`, `400` for an invalid `provider` or empty `message`, `502` when a single-provider call fails.

## Running locally

Backend (also serves the frontend):

```bash
pip install -r backend/requirements.txt
cd backend
uvicorn app:app --reload --port 8000
```

Open http://localhost:8000 — the FastAPI app serves the static frontend from `frontend/`, so no separate frontend server is needed.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DB_PATH` | `./multimind.db` (in `backend/`) | SQLite file location. `DATABASE_URL=sqlite:///path` is also accepted. |
| `DEFAULT_PROVIDER` | `openai` | Provider used when the user picks **Auto**. |
| `OPENAI_API_KEY` | — | OpenAI |
| `ANTHROPIC_API_KEY` | — | Claude |
| `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | — | Gemini |
| `XAI_API_KEY` | — | Grok |
| `DEEPSEEK_API_KEY` | — | DeepSeek |
| `OPENAI_MODEL` / `CLAUDE_MODEL` / `GEMINI_MODEL` / `GROK_MODEL` / `DEEPSEEK_MODEL` | `gpt-4.1-mini` / `claude-sonnet-4-6` / `gemini-2.5-flash` / `grok-3-mini` / `deepseek-chat` | Model overrides per provider |

A provider whose key is missing simply fails with a clear error (and shows an error card in compare mode) — the rest keep working.

## Render deployment

Settings (also codified in `render.yaml`):

- **Build command:** `pip install -r backend/requirements.txt`
- **Start command:** `cd backend && uvicorn app:app --host 0.0.0.0 --port $PORT`
- **Env vars:** the provider API keys above, plus optionally `DEFAULT_PROVIDER` and `DB_PATH`.

> ⚠️ **Ephemeral storage:** Render free instances have ephemeral disks, so the SQLite database may reset on every redeploy or restart. That's acceptable for this MVP; for production, migrate to PostgreSQL (or attach a Render persistent disk and point `DB_PATH` at it).

## Future improvements

- PostgreSQL instead of SQLite
- Authentication and per-user conversations
- Token usage and cost tracking
- Streaming responses
- Context summarization for long conversations (currently only the last 20 messages are sent)
