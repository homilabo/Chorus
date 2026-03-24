"""CLI provider functions — call each provider via subprocess."""

import json
import os
import subprocess
import time
from dataclasses import dataclass

from chorus.config import get_provider_config


@dataclass
class CLIResult:
    text: str
    error: str = ""
    duration_ms: int = 0


CLEAN_ENV = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}


def call_gemini(prompt: str, model: str = None, timeout: int = 300, cwd: str = None) -> CLIResult:
    """Call Gemini CLI."""
    config = get_provider_config("gemini") or {}
    model = model or config.get("model", "gemini-2.5-pro")
    timeout = config.get("timeout", timeout)
    cmd = ["gemini", "-p", prompt, "--model", model, "--sandbox", "false", "--allowed-mcp-server-names", ""]
    return _run(cmd, timeout, cwd)


def call_copilot(prompt: str, model: str = None, timeout: int = 300, cwd: str = None) -> CLIResult:
    """Call Copilot CLI."""
    config = get_provider_config("copilot") or {}
    model = model or config.get("model", "gpt-5-mini")
    timeout = config.get("timeout", timeout)
    cmd = ["copilot", "-p", prompt, "--model", model, "--allow-all", "--no-ask-user", "-s"]
    return _run(cmd, timeout, cwd)


def call_codex(prompt: str, model: str = None, timeout: int = 300, cwd: str = None) -> CLIResult:
    """Call Codex CLI."""
    config = get_provider_config("codex") or {}
    model = model or config.get("model", "gpt-5.4")
    timeout = config.get("timeout", timeout)
    cmd = ["codex", "exec", prompt, "--model", model, "--full-auto", "--json", "--skip-git-repo-check"]
    return _run(cmd, timeout, cwd)


def call_claude(prompt: str, model: str = None, timeout: int = 300, cwd: str = None) -> CLIResult:
    """Call Claude CLI (used when Gemini is the conductor)."""
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
    return _run(cmd, timeout, cwd, env=CLEAN_ENV)


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

        # If non-zero exit but stdout has content, prefer stdout (some CLIs emit warnings to stderr)
        if result.returncode != 0 and not text:
            return CLIResult(text="", error=result.stderr.strip()[:500], duration_ms=duration)

        if not text:
            return CLIResult(text="", error="Empty response", duration_ms=duration)

        # Try JSON parse, fall back to raw text
        try:
            data = json.loads(text)
            parsed = data.get("result", data.get("response", data.get("text", "")))
            if parsed:
                text = str(parsed)
        except json.JSONDecodeError:
            # Try JSONL (Codex outputs newline-delimited JSON events)
            last_text = None
            last_error = None
            for line in text.split('\n'):
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    etype = event.get("type", "")
                    if etype == "item.completed":
                        item_text = event.get("item", {}).get("text", "")
                        if item_text:
                            last_text = item_text
                    elif etype in ("error", "turn.failed"):
                        last_error = event.get("message", event.get("error", {}).get("message", ""))
                except json.JSONDecodeError:
                    continue
            if last_text:
                text = last_text
            elif last_error:
                return CLIResult(text="", error=last_error, duration_ms=duration)

        return CLIResult(text=text, duration_ms=duration)

    except subprocess.TimeoutExpired:
        duration = int((time.time() - start) * 1000)
        return CLIResult(text="", error=f"Timeout ({timeout}s)", duration_ms=duration)
    except FileNotFoundError:
        return CLIResult(text="", error=f"CLI not found: {cmd[0]}")
    except Exception as e:
        return CLIResult(text="", error=str(e))
