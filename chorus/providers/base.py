"""Provider base protocol and registry."""

import shutil
from typing import Optional, Protocol

from chorus.models import LLMResponse


class LLMProvider(Protocol):
    """Protocol that all CLI providers must implement."""

    @property
    def name(self) -> str: ...

    def get_models(self) -> list[dict]: ...

    def generate(self, prompt: str, model: str, session_id: Optional[str] = None, cwd: Optional[str] = None) -> LLMResponse: ...

    def is_available(self) -> bool: ...


_providers: dict[str, LLMProvider] = {}


def register_provider(provider: LLMProvider) -> None:
    _providers[provider.name] = provider


def get_provider(name: str) -> Optional[LLMProvider]:
    return _providers.get(name)


def get_available_providers() -> dict[str, LLMProvider]:
    return {k: v for k, v in _providers.items() if v.is_available()}


def get_all_providers() -> dict[str, LLMProvider]:
    return dict(_providers)


def check_cli_available(command: str) -> bool:
    return shutil.which(command) is not None
