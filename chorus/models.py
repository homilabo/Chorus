"""Chorus data models."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class LLMResponse:
    """Response from a CLI provider."""
    text: str
    model: str
    provider: str
    session_id: Optional[str] = None
    duration_ms: int = 0
    error: Optional[str] = None


@dataclass
class Message:
    """A single message in the conversation."""
    role: str  # "user" | "assistant" | "system"
    content: str
    provider: Optional[str] = None  # which model produced this
    model: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    duration_ms: int = 0
    cross_from: Optional[str] = None  # if this was a cross-send, from which provider


@dataclass
class Session:
    """A conversation session with shared history."""
    id: str = ""
    title: str = ""
    messages: list[Message] = field(default_factory=list)
    provider_sessions: dict[str, str] = field(default_factory=dict)  # provider -> cli session_id
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
