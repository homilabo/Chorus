"""Claude CLI provider."""

import json
import logging
import os
import subprocess
import time
from typing import Optional

from chorus.config import get_provider_config
from chorus.models import LLMResponse
from chorus.providers.base import check_cli_available

logger = logging.getLogger(__name__)

MODELS = [
    {"id": "sonnet", "name": "Claude Sonnet 4.6"},
    {"id": "opus", "name": "Claude Opus 4.6"},
    {"id": "haiku", "name": "Claude Haiku 4.5"},
]


class ClaudeCLIProvider:
    @property
    def name(self) -> str:
        return "claude"

    def get_models(self) -> list[dict]:
        config = get_provider_config("claude") or {}
        return config.get("models", MODELS)

    def is_available(self) -> bool:
        config = get_provider_config("claude")
        if not config or not config.get("enabled", True):
            return False
        return check_cli_available(config.get("cli_command", "claude"))

    def generate(self, prompt: str, model: str, session_id: Optional[str] = None, cwd: Optional[str] = None) -> LLMResponse:
        config = get_provider_config("claude") or {}
        cli_cmd = config.get("cli_command", "claude")
        timeout = config.get("timeout", 300)
        max_turns = str(config.get("max_turns", 10))

        cmd = [
            cli_cmd, "-p", prompt,
            "--output-format", "json",
            "--model", model,
            "--dangerously-skip-permissions",
            "--max-turns", max_turns,
        ]
        if session_id:
            cmd.extend(["--resume", session_id])

        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        start = time.time()
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout, cwd=cwd, env=env,
            )
            duration_ms = int((time.time() - start) * 1000)

            if result.returncode != 0:
                error_msg = result.stderr.strip()[:200] if result.stderr else "Unknown error"
                return LLMResponse(text="", model=model, provider="claude", error=error_msg, duration_ms=duration_ms)

            data = json.loads(result.stdout)
            text = data.get("result", data.get("text", ""))
            new_session_id = data.get("session_id", session_id)
            is_error = data.get("is_error", False)
            num_turns = data.get("num_turns", 0)
            subtype = data.get("subtype", "")

            if not text or not str(text).strip():
                if is_error and subtype == "error_max_turns":
                    text = f"[Claude used all {num_turns} turns but didn't produce a final text response.]"
                elif is_error:
                    return LLMResponse(text="", model=model, provider="claude", error=subtype or "Unknown error", duration_ms=duration_ms, session_id=new_session_id)
                else:
                    text = f"[Claude completed in {num_turns} turn(s) but returned no text.]"

            return LLMResponse(text=str(text), model=model, provider="claude", session_id=new_session_id, duration_ms=duration_ms)

        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start) * 1000)
            return LLMResponse(text="", model=model, provider="claude", error=f"Timeout after {timeout}s", duration_ms=duration_ms)
        except json.JSONDecodeError:
            duration_ms = int((time.time() - start) * 1000)
            raw = result.stdout.strip() if result.stdout else ""
            return LLMResponse(text=raw, model=model, provider="claude", session_id=session_id, duration_ms=duration_ms)
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            return LLMResponse(text="", model=model, provider="claude", error=str(e), duration_ms=duration_ms)
