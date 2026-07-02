"""Provider functions: each accepts a list of {role, content} dicts and returns
a uniform {"content", "provider", "model"} dict, raising ProviderError on failure."""

import os

import httpx
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

PROVIDER_NAMES = ["openai", "claude", "gemini", "grok", "deepseek"]

DEFAULT_MODELS = {
    "openai": "gpt-4.1-mini",
    "claude": "claude-sonnet-4-6",
    "gemini": "gemini-2.5-flash",
    "grok": "grok-3-mini",
    "deepseek": "deepseek-chat",
}


class ProviderError(Exception):
    """A provider call failed; the message is safe to return to the client."""

    def __init__(self, provider: str, message: str):
        self.provider = provider
        super().__init__(message)


def _require_env(provider: str, *names: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    raise ProviderError(provider, f"{names[0]} is not set")


def _model_for(provider: str) -> str:
    return os.environ.get(f"{provider.upper()}_MODEL", DEFAULT_MODELS[provider])


def format_history(messages: list[dict], limit: int = 20) -> list[dict]:
    """Prepare DB messages for a provider call.

    Takes the latest `limit` messages in chronological order, keeps only role
    and content, and merges consecutive assistant messages (from compare mode)
    into a single assistant message, since some APIs reject non-alternating
    roles. Merged answers are labeled `[provider]: answer`.
    """
    recent = messages[-limit:]
    groups: list[list[dict]] = []
    for message in recent:
        if groups and message["role"] == "assistant" and groups[-1][0]["role"] == "assistant":
            groups[-1].append(message)
        else:
            groups.append([message])

    formatted = []
    for group in groups:
        if len(group) == 1:
            formatted.append({"role": group[0]["role"], "content": group[0]["content"]})
        else:
            merged = "\n\n".join(
                f"[{m.get('provider') or 'assistant'}]: {m['content']}" for m in group
            )
            formatted.append({"role": "assistant", "content": merged})
    return formatted


def _split_system(messages: list[dict]) -> tuple[str | None, list[dict]]:
    """Pull system messages out of the history for APIs with a system parameter."""
    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    chat = [m for m in messages if m["role"] != "system"]
    return ("\n\n".join(system_parts) if system_parts else None), chat


async def _call_openai_compatible(
    provider: str, key_env: str, base_url: str | None, messages: list[dict]
) -> dict:
    api_key = _require_env(provider, key_env)
    client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=90)
    response = await client.chat.completions.create(
        model=_model_for(provider), messages=messages
    )
    content = response.choices[0].message.content or ""
    return {"content": content, "provider": provider, "model": response.model}


async def call_openai(messages: list[dict]) -> dict:
    return await _call_openai_compatible("openai", "OPENAI_API_KEY", None, messages)


async def call_grok(messages: list[dict]) -> dict:
    return await _call_openai_compatible(
        "grok", "XAI_API_KEY", "https://api.x.ai/v1", messages
    )


async def call_deepseek(messages: list[dict]) -> dict:
    return await _call_openai_compatible(
        "deepseek", "DEEPSEEK_API_KEY", "https://api.deepseek.com", messages
    )


async def call_claude(messages: list[dict]) -> dict:
    api_key = _require_env("claude", "ANTHROPIC_API_KEY")
    client = AsyncAnthropic(api_key=api_key, timeout=90)
    system, chat = _split_system(messages)
    kwargs = {"system": system} if system else {}
    response = await client.messages.create(
        model=_model_for("claude"),
        max_tokens=4096,
        messages=chat,
        **kwargs,
    )
    content = "".join(block.text for block in response.content if block.type == "text")
    return {"content": content, "provider": "claude", "model": response.model}


async def call_gemini(messages: list[dict]) -> dict:
    api_key = _require_env("gemini", "GEMINI_API_KEY", "GOOGLE_API_KEY")
    model = _model_for("gemini")
    system, chat = _split_system(messages)
    body = {
        "contents": [
            {
                "role": "model" if m["role"] == "assistant" else "user",
                "parts": [{"text": m["content"]}],
            }
            for m in chat
        ]
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}

    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": api_key},
            json=body,
        )
    if response.status_code != 200:
        raise ProviderError("gemini", f"Gemini API error {response.status_code}: {response.text[:200]}")
    data = response.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):
        raise ProviderError("gemini", f"Unexpected Gemini response: {str(data)[:200]}")
    content = "".join(p.get("text", "") for p in parts)
    return {"content": content, "provider": "gemini", "model": data.get("modelVersion", model)}


PROVIDERS = {
    "openai": call_openai,
    "claude": call_claude,
    "gemini": call_gemini,
    "grok": call_grok,
    "deepseek": call_deepseek,
}


async def call_provider(provider: str, messages: list[dict]) -> dict:
    """Call one provider, normalizing any failure into ProviderError."""
    try:
        return await PROVIDERS[provider](messages)
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize SDK/network errors
        raise ProviderError(provider, str(exc)) from exc
