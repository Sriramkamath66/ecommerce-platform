from pydantic import BaseModel
from typing import List


class SessionCreateResponse(BaseModel):
    session_id: str


class MessageRequest(BaseModel):
    content: str


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class HistoryResponse(BaseModel):
    session_id: str
    messages: List[ChatMessage]
    count: int
