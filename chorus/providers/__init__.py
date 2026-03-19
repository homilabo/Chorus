"""Provider package - registers all CLI providers on import."""

from chorus.providers.base import register_provider, get_available_providers, get_provider
from chorus.providers.claude_cli import ClaudeCLIProvider
from chorus.providers.gemini_cli import GeminiCLIProvider
from chorus.providers.copilot_cli import CopilotCLIProvider
from chorus.providers.codex_cli import CodexCLIProvider


def init_providers() -> None:
    """Initialize and register all providers."""
    register_provider(ClaudeCLIProvider())
    register_provider(GeminiCLIProvider())
    register_provider(CopilotCLIProvider())
    register_provider(CodexCLIProvider())
