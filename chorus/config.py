"""Configuration manager - loads provider settings from YAML."""

import logging
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG: dict[str, Any] = {
    "providers": {
        "claude": {
            "enabled": True,
            "model": "sonnet",
            "cli_command": "claude",
            "timeout": 300,
            "display_name": "Claude",
            "max_turns": 10,
        },
        "gemini": {
            "enabled": True,
            "model": "gemini-2.5-pro",
            "cli_command": "gemini",
            "timeout": 300,
            "display_name": "Gemini",
        },
        "copilot": {
            "enabled": True,
            "model": "gpt-4.1",
            "cli_command": "copilot",
            "timeout": 300,
            "display_name": "Copilot",
        },
        "codex": {
            "enabled": True,
            "model": "gpt-5.4",
            "cli_command": "codex",
            "timeout": 300,
            "display_name": "Codex",
        },
    },
    "debate": {
        "default_rounds": 2,
        "max_rounds": 5,
    },
}

CONFIG_PATH = Path.home() / ".chorus" / "config.yaml"
_config: dict[str, Any] = {}


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(config_path: Optional[Path] = None) -> dict[str, Any]:
    global _config
    path = config_path or CONFIG_PATH

    config = DEFAULT_CONFIG.copy()
    if path.exists():
        try:
            with open(path) as f:
                file_config = yaml.safe_load(f) or {}
            config = _deep_merge(DEFAULT_CONFIG, file_config)
            logger.info("Config loaded from %s", path)
        except Exception as e:
            logger.warning("Failed to load config from %s: %s", path, e)
    _config = config
    return config


def get_config() -> dict[str, Any]:
    if not _config:
        load_config()
    return _config


def get_provider_config(provider_key: str) -> Optional[dict[str, Any]]:
    return get_config().get("providers", {}).get(provider_key)


def ensure_config():
    """Create default config file if it doesn't exist."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(DEFAULT_CONFIG, f, default_flow_style=False, sort_keys=False)
        logger.info("Created default config at %s", CONFIG_PATH)
