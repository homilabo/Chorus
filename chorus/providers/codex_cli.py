"""OpenAI Codex CLI provider."""

import json
import logging
import subprocess
import time
from typing import Optional

from chorus.config import get_provider_config
from chorus.models import LLMResponse
from chorus.providers.base import check_cli_available

logger = logging.getLogger(__name__)

MODELS = [
    {"id": "gpt-5.4", "name": "GPT-5.4"},
]


class CodexCLIProvider:
    @property
    def name(self) -> str:
        return "codex"

    def get_models(self) -> list[dict]:
        config = get_provider_config("codex") or {}
        return config.get("models", MODELS)

    def is_available(self) -> bool:
        config = get_provider_config("codex")
        if not config or not config.get("enabled", True):
            return False
        return check_cli_available(config.get("cli_command", "codex"))

    def generate(self, prompt: str, model: str, session_id: Optional[str] = None, cwd: Optional[str] = None) -> LLMResponse:
        config = get_provider_config("codex") or {}
        cli_cmd = config.get("cli_command", "codex")
        timeout = config.get("timeout", 300)

        if session_id:
            cmd = [cli_cmd, "exec", "resume", session_id, prompt, "--model", model, "--full-auto", "--json", "--skip-git-repo-check"]
        else:
            cmd = [cli_cmd, "exec", prompt, "--model", model, "--full-auto", "--json", "--skip-git-repo-check"]

        start = time.time()
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout, cwd=cwd,
            )
            duration_ms = int((time.time() - start) * 1000)

            if result.returncode != 0 and not result.stdout.strip():
                error_msg = result.stderr.strip()[:200] if result.stderr else "Unknown error"
                return LLMResponse(text="", model=model, provider="codex", error=error_msg, duration_ms=duration_ms)

            stdout = result.stdout.strip()
            text = ""
            new_session_id = session_id

            for line in stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if event.get("thread_id"):
                    new_session_id = event["thread_id"]
                if event.get("session_id"):
                    new_session_id = event["session_id"]

                event_type = event.get("type", "")
                if event_type == "message" and event.get("role") == "assistant":
                    content = event.get("content", "")
                    if isinstance(content, list):
                        parts = [p.get("text", "") for p in content if p.get("type") == "output_text"]
                        if parts:
                            text = "\n".join(parts)
                    elif isinstance(content, str):
                        text = content

                if event_type == "item.completed":
                    item = event.get("item", {})
                    if item.get("type") == "agent_message" and item.get("text"):
                        text = item["text"]
                    elif item.get("type") == "message" and item.get("role") == "assistant":
                        content = item.get("content", [])
                        if isinstance(content, list):
                            parts = [p.get("text", "") for p in content if p.get("type") == "output_text"]
                            if parts:
                                text = "\n".join(parts)

            if not text:
                text = stdout
            if not text or not text.strip():
                text = "[Codex completed but returned no text.]"

            return LLMResponse(text=text, model=model, provider="codex", session_id=new_session_id, duration_ms=duration_ms)

        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start) * 1000)
            return LLMResponse(text="", model=model, provider="codex", error=f"Timeout after {timeout}s", duration_ms=duration_ms)
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            return LLMResponse(text="", model=model, provider="codex", error=str(e), duration_ms=duration_ms)
