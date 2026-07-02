"""SQLite persistence layer for MultiMind conversations and messages."""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))

VALID_ROLES = ("user", "assistant", "system")
VALID_PROVIDERS = ("openai", "claude", "gemini", "grok", "deepseek", "compare")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
    provider        TEXT CHECK (provider IN ('openai','claude','gemini','grok','deepseek','compare') OR provider IS NULL),
    model           TEXT,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages (conversation_id, created_at);
"""


def db_path() -> str:
    """Resolve the SQLite file path from DATABASE_URL / DB_PATH env vars."""
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///"):]
    return os.environ.get("DB_PATH", os.path.join(_BACKEND_DIR, "multimind.db"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_connection():
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(_SCHEMA)


def create_conversation(title: str | None = None) -> dict:
    now = _now()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO conversations (title, created_at, updated_at) VALUES (?, ?, ?)",
            (title, now, now),
        )
        return {"id": cur.lastrowid, "title": title, "created_at": now, "updated_at": now}


def get_conversations() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_conversation(conversation_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        return dict(row) if row else None


def get_conversation_with_messages(conversation_id: int) -> dict | None:
    conversation = get_conversation(conversation_id)
    if conversation is None:
        return None
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, role, content, provider, model, created_at"
            " FROM messages WHERE conversation_id = ? ORDER BY created_at ASC, id ASC",
            (conversation_id,),
        ).fetchall()
    conversation["messages"] = [dict(r) for r in rows]
    return conversation


def add_message(
    conversation_id: int,
    role: str,
    content: str,
    provider: str | None = None,
    model: str | None = None,
) -> dict:
    now = _now()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO messages (conversation_id, role, provider, model, content, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (conversation_id, role, provider, model, content, now),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )
        return {
            "id": cur.lastrowid,
            "conversation_id": conversation_id,
            "role": role,
            "provider": provider,
            "model": model,
            "content": content,
            "created_at": now,
        }


def get_last_messages(conversation_id: int, limit: int = 20) -> list[dict]:
    """Latest `limit` messages of a conversation, in chronological order."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, role, content, provider, model, created_at"
            " FROM messages WHERE conversation_id = ?"
            " ORDER BY created_at DESC, id DESC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def touch_conversation(conversation_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (_now(), conversation_id),
        )


def delete_conversation(conversation_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        return cur.rowcount > 0
