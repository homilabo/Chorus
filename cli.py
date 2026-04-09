"""CLI provider functions — call each provider via subprocess."""

import json
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from config import get_provider_config


@dataclass
class CLIResult:
    text: str
    error: str = ""
    duration_ms: int = 0
    session_id: Optional[str] = None


CLEAN_ENV = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

# In-memory session tracking — persists across MCP tool calls within same server process
_sessions: dict[str, str] = {}


def get_session(key: str) -> Optional[str]:
    return _sessions.get(key)


def set_session(key: str, session_id: str):
    if session_id:
        _sessions[key] = session_id


def clear_sessions():
    _sessions.clear()


def call_gemini(prompt: str, model: str = None, timeout: int = 300, cwd: str = None, session_key: str = None) -> CLIResult:
    """Call Gemini CLI."""
    key = session_key or "gemini"
    config = get_provider_config("gemini") or {}
    model = model or config.get("model", "auto")
    timeout = config.get("timeout", timeout)
    cmd = ["gemini", "-p", prompt, "--sandbox", "false", "--allowed-mcp-server-names", "", "--output-format", "json"]
    if model and model != "auto":
        cmd.extend(["--model", model])
    session_id = get_session(key)
    if session_id:
        cmd.extend(["--resume", session_id])
    result = _run(cmd, timeout, cwd)
    if result.session_id:
        set_session(key, result.session_id)
    return result


def call_copilot(prompt: str, model: str = None, timeout: int = 300, cwd: str = None, session_key: str = None) -> CLIResult:
    """Call Copilot CLI."""
    key = session_key or "copilot"
    config = get_provider_config("copilot") or {}
    model = model or config.get("model", "gpt-5-mini")
    timeout = config.get("timeout", timeout)
    cmd = ["copilot", "-p", prompt, "--model", model, "--allow-all", "--no-ask-user", "--output-format", "json"]
    session_id = get_session(key)
    if session_id:
        cmd.append("--continue")
    env = {**os.environ, **{k: v for k, v in config.items() if k.startswith("env_")}}
    # BYOK: inject provider env vars from config
    if "base_url" in config:
        env["COPILOT_PROVIDER_BASE_URL"] = config["base_url"]
    if "api_key" in config:
        env["COPILOT_PROVIDER_API_KEY"] = config["api_key"]
    if config.get("offline"):
        env["COPILOT_OFFLINE"] = "true"
    result = _run(cmd, timeout, cwd, env=env)
    if not result.error:
        set_session(key, "active")
    return result


def call_codex(prompt: str, model: str = None, timeout: int = 300, cwd: str = None, session_key: str = None) -> CLIResult:
    """Call Codex CLI."""
    key = session_key or "codex"
    config = get_provider_config("codex") or {}
    model = model or config.get("model", "gpt-5.4")
    timeout = config.get("timeout", timeout)
    session_id = get_session(key)
    if session_id:
        cmd = ["codex", "exec", "resume", session_id, prompt, "--model", model, "--full-auto", "--json", "--skip-git-repo-check"]
    else:
        cmd = ["codex", "exec", prompt, "--model", model, "--full-auto", "--json", "--skip-git-repo-check"]
    result = _run(cmd, timeout, cwd)
    if result.session_id:
        set_session(key, result.session_id)
    return result


def call_claude(prompt: str, model: str = None, timeout: int = 300, cwd: str = None, session_key: str = None) -> CLIResult:
    """Call Claude CLI (used when another model is the conductor)."""
    key = session_key or "claude"
    config = get_provider_config("claude") or {}
    model = model or config.get("model", "sonnet")
    timeout = config.get("timeout", timeout)
    max_turns = str(config.get("max_turns", 10))
    cmd = [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--model", model,
        "--dangerously-skip-permissions",
        "--max-turns", max_turns,
    ]
    session_id = get_session(key)
    if session_id:
        cmd.extend(["--resume", session_id])
    result = _run(cmd, timeout, cwd, env=CLEAN_ENV)
    if result.session_id:
        set_session(key, result.session_id)
    return result


def _run(cmd: list, timeout: int, cwd: str = None, env: dict = None) -> CLIResult:
    """Run subprocess, return result."""
    start = time.time()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, cwd=cwd, env=env,
        )
        duration = int((time.time() - start) * 1000)

        text = result.stdout.strip()

        if result.returncode != 0 and not text:
            return CLIResult(text="", error=result.stderr.strip()[:500], duration_ms=duration)

        if not text:
            return CLIResult(text="", error="Empty response", duration_ms=duration)

        # Try JSON parse, fall back to raw text
        session_id = None
        try:
            data = json.loads(text)
            parsed = data.get("result", data.get("response", data.get("text", "")))
            session_id = data.get("session_id", data.get("sessionId"))
            if parsed:
                text = str(parsed)
        except json.JSONDecodeError:
            # Try JSONL (Codex and Copilot output newline-delimited JSON events)
            last_text = None
            last_error = None
            for line in text.split('\n'):
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    etype = event.get("type", "")
                    data = event.get("data", event.get("item", {}))
                    if etype == "item.completed":
                        item_text = data.get("text", "")
                        if item_text:
                            last_text = item_text
                    elif etype == "assistant.message":
                        msg_text = data.get("content", "")
                        if msg_text:
                            last_text = msg_text
                    elif etype == "thread.started":
                        session_id = event.get("thread_id") or event.get("session_id")
                    elif etype in ("error", "turn.failed"):
                        last_error = event.get("message", data.get("message", ""))
                except json.JSONDecodeError:
                    continue
            if last_text:
                text = last_text
            elif last_error:
                return CLIResult(text="", error=last_error, duration_ms=duration, session_id=session_id)

        return CLIResult(text=text, duration_ms=duration, session_id=session_id)

    except subprocess.TimeoutExpired:
        duration = int((time.time() - start) * 1000)
        return CLIResult(text="", error=f"Timeout ({timeout}s)", duration_ms=duration)
    except FileNotFoundError:
        return CLIResult(text="", error=f"CLI not found: {cmd[0]}")
    except Exception as e:
        return CLIResult(text="", error=str(e))
