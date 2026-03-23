"""Chorus data models."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


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


@dataclass
class AgentDefinition:
    """An agent defined by a markdown file."""
    name: str
    description: str
    mode: str = "parallel"  # parallel | debate | sequential | cross-review | single
    providers: Any = "all"  # "all" | ["claude","gemini"] | {"exclude":["codex"]}
    rounds: int = 2
    prompt_body: str = ""   # markdown body after frontmatter
    source_path: str = ""   # file path for debugging
