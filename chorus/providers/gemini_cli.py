"""Gemini CLI provider."""

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
    {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro"},
    {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash"},
    {"id": "gemini-3-pro", "name": "Gemini 3 Pro"},
    {"id": "gemini-3-flash", "name": "Gemini 3 Flash"},
    {"id": "gemini-3.1-pro-preview", "name": "Gemini 3.1 Pro Preview"},
]


class GeminiCLIProvider:
    @property
    def name(self) -> str:
        return "gemini"

    def get_models(self) -> list[dict]:
        config = get_provider_config("gemini") or {}
        return config.get("models", MODELS)

    def is_available(self) -> bool:
        config = get_provider_config("gemini")
        if not config or not config.get("enabled", True):
            return False
        return check_cli_available(config.get("cli_command", "gemini"))

    def generate(self, prompt: str, model: str, session_id: Optional[str] = None, cwd: Optional[str] = None) -> LLMResponse:
        config = get_provider_config("gemini") or {}
        cli_cmd = config.get("cli_command", "gemini")
        timeout = config.get("timeout", 300)

        cmd = [
            cli_cmd, "-p", prompt,
            "-m", model,
            "--output-format", "json",
            "--yolo", "--sandbox", "false",
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

            if result.returncode != 0 and not result.stdout.strip():
                noise = {"yolo mode", "loaded cached", "all tool calls"}
                stderr_lines = (result.stderr or "").strip().splitlines()
                error_lines = [l.strip() for l in stderr_lines if l.strip() and not any(n in l.lower() for n in noise)]
                error_msg = "\n".join(error_lines)[:500] if error_lines else "Unknown error"
                return LLMResponse(text="", model=model, provider="gemini", error=error_msg, duration_ms=duration_ms)

            stdout = result.stdout.strip()
            try:
                data = json.loads(stdout)
            except json.JSONDecodeError:
                end_idx = stdout.rfind("}")
                if end_idx != -1:
                    data = json.loads(stdout[:end_idx + 1])
                else:
                    return LLMResponse(text=stdout, model=model, provider="gemini", session_id=session_id, duration_ms=duration_ms)

            text = data.get("response", data.get("result", data.get("text", stdout)))
            new_session_id = data.get("session_id", data.get("sessionId", session_id))

            if not text or not str(text).strip():
                text = "[Gemini completed but returned no text.]"

            return LLMResponse(text=str(text), model=model, provider="gemini", session_id=new_session_id, duration_ms=duration_ms)

        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start) * 1000)
            return LLMResponse(text="", model=model, provider="gemini", error=f"Timeout after {timeout}s", duration_ms=duration_ms)
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            return LLMResponse(text="", model=model, provider="gemini", error=str(e), duration_ms=duration_ms)
