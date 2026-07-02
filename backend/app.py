"""MultiMind backend: persistent conversations with per-message provider switching."""

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import auth
import db
import providers
from providers import PROVIDER_NAMES, ProviderError

VALID_CHAT_PROVIDERS = {"auto", "compare", *PROVIDER_NAMES}

_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="MultiMind", lifespan=lifespan)
app.include_router(auth.router)


class CreateConversationRequest(BaseModel):
    title: str | None = None


class ChatRequest(BaseModel):
    conversation_id: int | None = None
    message: str = ""
    provider: str = "auto"
    # Optional model override for single-provider requests.
    model: str | None = None
    # Optional per-provider model map used by compare mode, e.g. {"openai": "gpt-4.1"}.
    models: dict[str, str] | None = None


def _auto_title(message: str) -> str:
    title = message.strip().splitlines()[0][:50].strip()
    return title or "New chat"


def _default_provider() -> str:
    provider = os.environ.get("DEFAULT_PROVIDER", "openai")
    return provider if provider in PROVIDER_NAMES else "openai"


@app.get("/models")
def list_models():
    return {
        "default_provider": _default_provider(),
        "providers": providers.model_catalog(),
    }


@app.post("/conversations")
def create_conversation(request: CreateConversationRequest, user: dict = Depends(auth.require_user)):
    conversation = db.create_conversation(request.title or None, user["id"])
    return {"conversation_id": conversation["id"], "title": conversation["title"]}


@app.get("/conversations")
def list_conversations(user: dict = Depends(auth.require_user)):
    return db.get_conversations(user["id"])


@app.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, user: dict = Depends(auth.require_user)):
    conversation = db.get_conversation_with_messages(conversation_id, user["id"])
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: int, user: dict = Depends(auth.require_user)):
    if not db.delete_conversation(conversation_id, user["id"]):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"deleted": conversation_id}


@app.post("/chat")
async def chat(request: ChatRequest, user: dict = Depends(auth.require_user)):
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message must not be empty")
    if request.provider not in VALID_CHAT_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid provider '{request.provider}'."
            f" Expected one of: {', '.join(sorted(VALID_CHAT_PROVIDERS))}",
        )

    if request.conversation_id is None:
        conversation_id = db.create_conversation(_auto_title(message), user["id"])["id"]
    else:
        conversation_id = request.conversation_id
        if db.get_conversation(conversation_id, user["id"]) is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

    db.add_message(conversation_id, "user", message)
    history = providers.format_history(db.get_last_messages(conversation_id, limit=20))

    if request.provider == "compare":
        return await _chat_compare(conversation_id, history, request.models or {})

    provider = _default_provider() if request.provider == "auto" else request.provider
    model = request.model if request.provider != "auto" else None
    try:
        answer = await providers.call_provider(provider, history, model)
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail={"provider": provider, "error": str(exc)})

    db.add_message(conversation_id, "assistant", answer["content"], provider, answer["model"])
    return {
        "conversation_id": conversation_id,
        "message": {
            "role": "assistant",
            "content": answer["content"],
            "provider": provider,
            "model": answer["model"],
        },
    }


async def _chat_compare(conversation_id: int, history: list[dict], models: dict[str, str]):
    results = await asyncio.gather(
        *(providers.call_provider(name, history, models.get(name)) for name in PROVIDER_NAMES),
        return_exceptions=True,
    )
    answers = {}
    for name, result in zip(PROVIDER_NAMES, results):
        if isinstance(result, Exception):
            answers[name] = {"error": str(result)}
        else:
            db.add_message(
                conversation_id, "assistant", result["content"], name, result["model"]
            )
            answers[name] = {"content": result["content"], "model": result["model"]}
    return {"conversation_id": conversation_id, "provider": "compare", "answers": answers}


# Serve the frontend (mounted last so API routes take precedence).
app.mount("/", StaticFiles(directory=_FRONTEND_DIR, html=True), name="frontend")
