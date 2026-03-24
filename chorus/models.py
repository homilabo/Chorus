"""Chorus data models."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Message:
    """A single message in the conversation."""
    role: str  # "user" | "assistant"
    content: str
    provider: Optional[str] = None
    model: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    duration_ms: int = 0
    cross_from: Optional[str] = None


@dataclass
class Session:
    """A conversation session."""
    id: str = ""
    title: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
