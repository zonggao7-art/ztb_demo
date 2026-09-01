"""FastAPI request contracts."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)
    thread_id: str = "default"
    deadline_s: float | None = Field(default=None, gt=0, le=120)
