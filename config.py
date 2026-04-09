"""Configuration — loads provider and role settings from ~/.chorus/config.yaml."""

import logging
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".chorus" / "config.yaml"

DEFAULT_CONFIG: dict[str, Any] = {
    "providers": {
        "claude": {"model": "sonnet", "timeout": 300, "max_turns": 10},
        "gemini": {"model": "gemini-2.5-pro", "timeout": 300},
        "copilot": {"model": "gpt-5-mini", "timeout": 300},
        "codex": {"model": "gpt-5.4", "timeout": 300},
    },
    "roles": {
        "researcher": {"provider": "gemini", "model": "gemini-2.5-pro", "description": "Deep internet research, large context analysis"},
        "coder": {"provider": "codex", "model": "gpt-5.4", "description": "Code generation, complex logic, refactoring"},
        "reasoner": {"provider": "claude", "model": "opus", "description": "Complex reasoning, architecture, analysis"},
        "reviewer": {"provider": "copilot", "model": "gpt-5-mini", "description": "Quick code review, best practices"},
    },
}

_config: dict[str, Any] = {}


def load_config(config_path: Optional[Path] = None) -> dict[str, Any]:
    """Load config from YAML, merging with defaults."""
    global _config
    path = config_path or CONFIG_PATH
    config = DEFAULT_CONFIG.copy()
    if path.exists():
        try:
            with open(path) as f:
                file_config = yaml.safe_load(f) or {}
            config = _deep_merge(DEFAULT_CONFIG, file_config)
        except Exception as e:
            logger.warning("Failed to load config from %s: %s", path, e)
    _config = config
    return config


def get_config() -> dict[str, Any]:
    load_config()
    return _config


def get_provider_config(provider_key: str) -> Optional[dict[str, Any]]:
    return get_config().get("providers", {}).get(provider_key)


def get_roles() -> dict[str, dict]:
    return get_config().get("roles", {})


def get_role(role_name: str) -> Optional[dict[str, Any]]:
    return get_roles().get(role_name)


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
