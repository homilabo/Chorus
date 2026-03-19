"""GitHub Copilot CLI provider."""

import logging
import subprocess
import time
from typing import Optional

from chorus.config import get_provider_config
from chorus.models import LLMResponse
from chorus.providers.base import check_cli_available

logger = logging.getLogger(__name__)

MODELS = [
    {"id": "gpt-4.1", "name": "GPT-4.1"},
    {"id": "gpt-5", "name": "GPT-5"},
    {"id": "gpt-5-mini", "name": "GPT-5 Mini"},
    {"id": "gpt-5.1", "name": "GPT-5.1"},
    {"id": "gpt-5.1-codex", "name": "GPT-5.1 Codex"},
    {"id": "gpt-5.1-codex-max", "name": "GPT-5.1 Codex Max"},
    {"id": "gpt-5.2", "name": "GPT-5.2"},
    {"id": "gpt-5.2-codex", "name": "GPT-5.2 Codex"},
    {"id": "claude-sonnet-4.5", "name": "Claude Sonnet 4.5"},
    {"id": "claude-opus-4.6", "name": "Claude Opus 4.6"},
    {"id": "claude-haiku-4.5", "name": "Claude Haiku 4.5"},
    {"id": "gemini-3-pro-preview", "name": "Gemini 3 Pro Preview"},
]


class CopilotCLIProvider:
    @property
    def name(self) -> str:
        return "copilot"

    def get_models(self) -> list[dict]:
        config = get_provider_config("copilot") or {}
        return config.get("models", MODELS)

    def is_available(self) -> bool:
        config = get_provider_config("copilot")
        if not config or not config.get("enabled", True):
            return False
        return check_cli_available(config.get("cli_command", "copilot"))

    def generate(self, prompt: str, model: str, session_id: Optional[str] = None, cwd: Optional[str] = None) -> LLMResponse:
        config = get_provider_config("copilot") or {}
        cli_cmd = config.get("cli_command", "copilot")
        timeout = config.get("timeout", 300)

        cmd = [
            cli_cmd, "-p", prompt,
            "--model", model,
            "--allow-all", "--silent", "--no-color",
        ]
        if session_id:
            cmd.extend(["--resume", session_id])

        start = time.time()
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout, cwd=cwd,
            )
            duration_ms = int((time.time() - start) * 1000)

            if result.returncode != 0:
                error_msg = result.stderr.strip()[:200] if result.stderr else "Unknown error"
                return LLMResponse(text="", model=model, provider="copilot", error=error_msg, duration_ms=duration_ms)

            text = result.stdout.strip()
            if not text:
                stderr_info = result.stderr.strip()[:300] if result.stderr else ""
                text = f"[Copilot completed but returned no text.{f' stderr: {stderr_info}' if stderr_info else ''}]"

            return LLMResponse(text=text, model=model, provider="copilot", session_id=session_id, duration_ms=duration_ms)

        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start) * 1000)
            return LLMResponse(text="", model=model, provider="copilot", error=f"Timeout after {timeout}s", duration_ms=duration_ms)
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            return LLMResponse(text="", model=model, provider="copilot", error=str(e), duration_ms=duration_ms)
