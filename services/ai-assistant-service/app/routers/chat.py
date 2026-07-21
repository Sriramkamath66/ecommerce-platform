from __future__ import annotations

import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from ..auth import get_current_user_id
from ..schemas.chat import (
    ChatMessage,
    HistoryResponse,
    MessageRequest,
    SessionCreateResponse,
)
from ..services import session_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


# ---------------------------------------------------------------------------
# POST /chat/sessions — create a new session
# ---------------------------------------------------------------------------
@router.post("/sessions", response_model=SessionCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> SessionCreateResponse:
    redis = request.app.state.redis
    settings = request.app.state.settings
    session_id = await session_service.create_session(
        redis, user_id, settings.SESSION_TTL
    )
    return SessionCreateResponse(session_id=session_id)


# ---------------------------------------------------------------------------
# POST /chat/sessions/{session_id}/messages — send a message (SSE stream)
# ---------------------------------------------------------------------------
@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    body: MessageRequest,
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> StreamingResponse:
    redis = request.app.state.redis
    settings = request.app.state.settings
    assistant_service = request.app.state.assistant_service

    # Validate session ownership
    owner = await session_service.get_session_user(redis, session_id)
    if owner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if owner != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    # Fetch existing history
    history = await session_service.get_history(redis, session_id)

    async def event_generator() -> AsyncGenerator[str, None]:
        collected_chunks: list[str] = []
        try:
            gen = await assistant_service.process_message(
                session_id=session_id,
                user_id=user_id,
                message=body.content,
                history=history,
            )
            async for text in gen:
                collected_chunks.append(text)
                yield f"data: {json.dumps({'type': 'text_delta', 'text': text})}\n\n"

        except Exception as exc:  # noqa: BLE001
            logger.error("Streaming error in session %s: %s", session_id, exc)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
            return

        # Persist both the user message and the complete assistant reply
        full_response = "".join(collected_chunks)
        await session_service.append_message(
            redis,
            session_id,
            role="user",
            content=body.content,
            max_turns=settings.MAX_HISTORY_TURNS,
            session_ttl=settings.SESSION_TTL,
        )
        await session_service.append_message(
            redis,
            session_id,
            role="assistant",
            content=full_response,
            max_turns=settings.MAX_HISTORY_TURNS,
            session_ttl=settings.SESSION_TTL,
        )

        yield f"data: {json.dumps({'type': 'message_stop'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# GET /chat/sessions/{session_id}/history — retrieve conversation history
# ---------------------------------------------------------------------------
@router.get("/sessions/{session_id}/history", response_model=HistoryResponse)
async def get_history(
    session_id: str,
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> HistoryResponse:
    redis = request.app.state.redis

    owner = await session_service.get_session_user(redis, session_id)
    if owner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if owner != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    raw_history = await session_service.get_history(redis, session_id)
    messages = [ChatMessage(role=m["role"], content=m["content"]) for m in raw_history]
    return HistoryResponse(session_id=session_id, messages=messages, count=len(messages))


# ---------------------------------------------------------------------------
# DELETE /chat/sessions/{session_id} — remove a session
# ---------------------------------------------------------------------------
@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    redis = request.app.state.redis

    owner = await session_service.get_session_user(redis, session_id)
    if owner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if owner != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    await session_service.delete_session(redis, session_id)
    return {"status": "deleted"}
