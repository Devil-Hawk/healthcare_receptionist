from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolRequest(BaseModel):
    tool_name: str = Field(..., description="Name of the tool/function Retell wants to invoke")
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str | None = Field(default=None, description="Retell call identifier")
    session_id: str | None = Field(default=None, description="Conversation session identifier")
