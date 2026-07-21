import uuid
import json
from datetime import datetime, timezone
from typing import Optional

from redis.asyncio import Redis

SESSION_KEY_PREFIX = "chat_session:"


def _meta_key(session_id: str) -> str:
    return f"{SESSION_KEY_PREFIX}{session_id}:meta"


def _history_key(session_id: str) -> str:
    return f"{SESSION_KEY_PREFIX}{session_id}:history"


async def create_session(redis: Redis, user_id: str, session_ttl: int) -> str:
    """Create a new chat session for *user_id* and return its UUID."""
    session_id = str(uuid.uuid4())
    meta = {
        "user_id": user_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # Store session metadata with TTL
    await redis.set(_meta_key(session_id), json.dumps(meta), ex=session_ttl)

    # Initialise an empty history list
    await redis.set(_history_key(session_id), json.dumps([]), ex=session_ttl)

    return session_id


async def get_session_user(redis: Redis, session_id: str) -> Optional[str]:
    """Return the owner *user_id* for *session_id*, or None if not found."""
    raw = await redis.get(_meta_key(session_id))
    if raw is None:
        return None
    meta = json.loads(raw)
    return meta.get("user_id")


async def get_history(redis: Redis, session_id: str) -> list[dict]:
    """Return the conversation history as a list of role/content dicts."""
    raw = await redis.get(_history_key(session_id))
    if raw is None:
        return []
    return json.loads(raw)


async def append_message(
    redis: Redis,
    session_id: str,
    role: str,
    content: str,
    max_turns: int,
    session_ttl: int,
) -> None:
    """Append one message to the history and enforce the sliding-window limit.

    Each *turn* consists of one user message and one assistant message,
    so we keep at most ``max_turns * 2`` messages in the buffer.
    """
    history = await get_history(redis, session_id)
    history.append({"role": role, "content": content})

    # Sliding-window truncation
    max_messages = max_turns * 2
    if len(history) > max_messages:
        history = history[-max_messages:]

    # Persist and refresh TTL on both keys
    serialised = json.dumps(history)
    await redis.set(_history_key(session_id), serialised, ex=session_ttl)

    # Also refresh metadata TTL so both keys expire together
    meta_raw = await redis.get(_meta_key(session_id))
    if meta_raw is not None:
        await redis.set(_meta_key(session_id), meta_raw, ex=session_ttl)


async def delete_session(redis: Redis, session_id: str) -> None:
    """Delete the metadata and history keys for *session_id*."""
    await redis.delete(_meta_key(session_id), _history_key(session_id))
