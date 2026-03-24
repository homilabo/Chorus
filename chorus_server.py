"""Chorus MCP server — multi-model orchestration via CLI subscriptions."""

import asyncio
import os

from mcp.server.fastmcp import FastMCP


def _detect_conductor() -> str | None:
    """Detect which CLI is the conductor (host) to prevent recursive calls."""
    if os.environ.get("CLAUDECODE"):
        return "claude"
    if os.environ.get("GEMINI_CLI_IDE_SERVER_PORT") or os.environ.get("GEMINI_CLI"):
        return "gemini"
    if os.environ.get("CODEX_CLI") or os.environ.get("CODEX"):
        return "codex"
    if os.environ.get("COPILOT_CLI") or "copilotCli" in os.environ.get("PATH", ""):
        return "copilot"
    return None


CONDUCTOR = _detect_conductor()

mcp = FastMCP(
    "chorus",
    instructions="""You have access to multiple AI models via Chorus tools.

Model tools:
- ask: Ask a specific model (provider: gemini, copilot, codex, claude)
- ask_all: Ask all models in parallel — use for comparisons, research, multiple perspectives
- parallel_ask: Run multiple calls (same or different providers) simultaneously

Guidelines:
- Match the user's language
- For simple questions, answer directly without calling models
- For comparisons, debates, research — use ask_all for multiple perspectives
- For debates: call ask_all for round 1, then ask_all again with previous responses as context for round 2+
- For critique/cross-review: use ask to send one model's response to another
- Tell the user what you're about to do before calling tools
- Synthesize results by highlighting agreements, disagreements, and insights
- Be natural and conversational""",
)

AVAILABLE_PROVIDERS = ["gemini", "copilot", "codex", "claude"]


def _get_provider_fn(name: str):
    """Get the CLI call function for a provider."""
    from cli import call_gemini, call_copilot, call_codex, call_claude
    return {
        "gemini": call_gemini,
        "copilot": call_copilot,
        "codex": call_codex,
        "claude": call_claude,
    }.get(name)


@mcp.tool()
async def ask(prompt: str, provider: str = "gemini", cwd: str = ".") -> str:
    """Ask a specific AI model a question.

    Available providers: gemini, copilot, codex, claude.
    - gemini: Google Gemini — internet research, analysis
    - copilot: GitHub Copilot — code tasks
    - codex: OpenAI Codex — code generation, analysis
    - claude: Claude — reasoning, coding (use when another model is conductor)

    Args:
        prompt: The question or instruction
        provider: Which model to ask (gemini, copilot, codex, claude)
        cwd: Working directory for file operations
    """
    if provider == CONDUCTOR:
        return f"[BLOCKED] {provider} is the current conductor — cannot call itself. Use a different provider."

    fn = _get_provider_fn(provider)
    if not fn:
        return f"Unknown provider: {provider}. Available: {', '.join(AVAILABLE_PROVIDERS)}"

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: fn(prompt, cwd=cwd))

    if result.error:
        return f"[{provider.upper()} ERROR] {result.error}"
    return result.text


@mcp.tool()
async def ask_all(prompt: str, exclude: list[str] = None, cwd: str = ".") -> str:
    """Ask ALL available models the same question in parallel and return all responses.

    Use this for comparisons, research, or getting multiple perspectives.
    All models run simultaneously — total time equals the slowest model.

    Args:
        prompt: The question to ask all models
        exclude: Models to skip (e.g. ["copilot", "claude"])
        cwd: Working directory for file operations
    """
    exclude = exclude or []
    # Auto-exclude conductor to prevent recursive calls
    if CONDUCTOR and CONDUCTOR not in exclude:
        exclude.append(CONDUCTOR)
    active = {name: _get_provider_fn(name) for name in AVAILABLE_PROVIDERS if name not in exclude}

    if not active:
        return "No providers available."

    loop = asyncio.get_event_loop()

    async def _call(name, fn):
        result = await loop.run_in_executor(None, lambda: fn(prompt, cwd=cwd))
        return name, result

    tasks = [_call(name, fn) for name, fn in active.items()]
    results = await asyncio.gather(*tasks)

    parts = []
    for name, result in results:
        if result.error:
            parts.append(f"**{name.upper()}**: ERROR — {result.error}")
        else:
            parts.append(f"**{name.upper()}** ({result.duration_ms}ms):\n{result.text}")

    return "\n\n---\n\n".join(parts)


@mcp.tool()
async def parallel_ask(tasks: list[dict], cwd: str = ".") -> str:
    """Run multiple ask calls in parallel. Each task specifies a provider and prompt.

    Use this when you need to call the same or different providers multiple times simultaneously.
    All tasks run at the same time — total time equals the slowest task.

    Args:
        tasks: List of objects with "provider" and "prompt" keys.
               Example: [{"provider": "copilot", "prompt": "question 1"}, {"provider": "gemini", "prompt": "question 2"}]
        cwd: Working directory for file operations
    """
    if not tasks:
        return "No tasks provided."

    loop = asyncio.get_event_loop()

    async def _call(idx, task):
        provider = task.get("provider", "gemini")
        prompt = task.get("prompt", "")
        if provider == CONDUCTOR:
            return idx, provider, None, f"{provider} is the conductor — cannot call itself"
        fn = _get_provider_fn(provider)
        if not fn:
            return idx, provider, None, f"Unknown provider: {provider}"
        result = await loop.run_in_executor(None, lambda: fn(prompt, cwd=cwd))
        return idx, provider, result, None

    jobs = [_call(i, t) for i, t in enumerate(tasks)]
    results = await asyncio.gather(*jobs)

    parts = []
    for idx, provider, result, error in sorted(results, key=lambda x: x[0]):
        label = f"**[{idx+1}] {provider.upper()}**"
        if error:
            parts.append(f"{label}: ERROR — {error}")
        elif result.error:
            parts.append(f"{label}: ERROR — {result.error}")
        else:
            parts.append(f"{label} ({result.duration_ms}ms):\n{result.text}")

    return "\n\n---\n\n".join(parts)


if __name__ == "__main__":
    mcp.run(transport="stdio")
