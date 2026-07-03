"""SQLite persistence layer for MultiMind conversations and messages."""

import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))

VALID_ROLES = ("user", "assistant", "system")
VALID_PROVIDERS = ("openai", "claude", "gemini", "grok", "deepseek", "compare")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
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
    # Migration runs on a plain connection (foreign keys off) so the old
    # Google-auth tables can be dropped cleanly.
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    try:
        # Databases from the earlier Google-sign-in version: accounts can't be
        # carried over, so reset users/sessions and their conversations.
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        if "google_sub" in columns:
            conn.executescript(
                "DELETE FROM messages WHERE conversation_id IN"
                " (SELECT id FROM conversations WHERE user_id IS NOT NULL);"
                "DELETE FROM conversations WHERE user_id IS NOT NULL;"
                "DROP TABLE IF EXISTS sessions;"
                "DROP TABLE IF EXISTS users;"
            )
        conn.executescript(_SCHEMA)
        # Databases from before per-user conversations existed.
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(conversations)")}
        if "user_id" not in columns:
            conn.execute(
                "ALTER TABLE conversations ADD COLUMN user_id INTEGER REFERENCES users(id)"
            )
        conn.commit()
    finally:
        conn.close()


# ---------- Users & sessions ----------


def create_user(username: str, password_hash: str) -> dict | None:
    """Create a user; returns None if the (case-insensitive) name is taken."""
    now = _now()
    with get_connection() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, password_hash, now),
            )
        except sqlite3.IntegrityError:
            return None
        return {"id": cur.lastrowid, "username": username}


def get_user_by_username(username: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        return dict(row) if row else None


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
            (token, user_id, _now()),
        )
    return token


def get_session_user(token: str, max_age_days: int = 30) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT u.id, u.username, s.created_at AS session_created"
            " FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?",
            (token,),
        ).fetchone()
    if row is None:
        return None
    created = datetime.fromisoformat(row["session_created"])
    if (datetime.now(timezone.utc) - created).days >= max_age_days:
        delete_session(token)
        return None
    user = dict(row)
    user.pop("session_created")
    return user


def delete_session(token: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


# ---------- Conversations & messages ----------


def create_conversation(title: str | None = None, user_id: int | None = None) -> dict:
    now = _now()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO conversations (user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (user_id, title, now, now),
        )
        return {"id": cur.lastrowid, "title": title, "created_at": now, "updated_at": now}


def get_conversations(user_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations"
            " WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_conversation(conversation_id: int, user_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations"
            " WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        ).fetchone()
        return dict(row) if row else None


def get_conversation_with_messages(conversation_id: int, user_id: int) -> dict | None:
    conversation = get_conversation(conversation_id, user_id)
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


def delete_conversation(conversation_id: int, user_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.execute(
            "DELETE FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        )
        return cur.rowcount > 0
