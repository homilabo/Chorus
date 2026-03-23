"""MCP server — Multi-model orchestration (ask_all, debate, cross_send)."""

import asyncio

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("chorus-orchestra")


def _get_providers():
    """Lazy import to avoid circular imports at module load."""
    from chorus.cli import call_gemini, call_copilot, call_codex, call_claude
    return {
        "gemini": call_gemini,
        "copilot": call_copilot,
        "codex": call_codex,
        "claude": call_claude,
    }


@mcp.tool()
async def ask_all(prompt: str, exclude: list[str] = None, cwd: str = ".") -> str:
    """Ask ALL available models the same question in parallel and return all responses.

    Use this for comparisons, research, or getting multiple perspectives on a topic.

    Args:
        prompt: The question to ask all models
        exclude: Models to skip (e.g. ["copilot", "claude"])
        cwd: Working directory for file operations
    """
    providers = _get_providers()
    exclude = exclude or []
    active = {k: v for k, v in providers.items() if k not in exclude}

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
async def debate(prompt: str, rounds: int = 2, exclude: list[str] = None, cwd: str = ".") -> str:
    """Run a multi-round debate between models on a topic.

    Each round, all models see the previous round's responses and can agree, disagree, or refine their position.

    Args:
        prompt: The debate topic
        rounds: Number of rounds (default 2)
        exclude: Models to skip
        cwd: Working directory for file operations
    """
    all_rounds = []

    # Round 1: initial responses
    first = await ask_all(prompt, exclude, cwd)
    all_rounds.append(f"**Round 1:**\n{first}")

    # Subsequent rounds: each model sees previous responses
    prev = first
    for r in range(1, rounds):
        debate_prompt = f"""This is round {r + 1} of a multi-model debate.

Topic: {prompt}

Previous round responses:
{prev}

Respond again. Consider what other models said. Where do you agree? Disagree? Be specific and constructive."""

        round_result = await ask_all(debate_prompt, exclude, cwd)
        all_rounds.append(f"**Round {r + 1}:**\n{round_result}")
        prev = round_result

    return "\n\n===\n\n".join(all_rounds)


@mcp.tool()
async def cross_send(from_model: str, to_model: str, context: str = "", cwd: str = ".") -> str:
    """Send one model's response to another model for critique or follow-up.

    Useful for peer review, fact-checking, or getting a second opinion.

    Args:
        from_model: Source model name (gemini, copilot, codex, claude)
        to_model: Target model name
        context: Additional context or specific question for the target model
        cwd: Working directory for file operations
    """
    providers = _get_providers()

    if from_model not in providers:
        return f"Unknown model: {from_model}. Available: {', '.join(providers.keys())}"
    if to_model not in providers:
        return f"Unknown model: {to_model}. Available: {', '.join(providers.keys())}"

    loop = asyncio.get_event_loop()

    # Get response from source model
    source_result = await loop.run_in_executor(
        None, lambda: providers[from_model](context or "Share your analysis", cwd=cwd))

    if source_result.error:
        return f"[{from_model} ERROR] {source_result.error}"

    # Send source response to target model for critique
    cross_prompt = f"""{from_model.upper()} said:
{source_result.text}

{context or 'Review the above. What is good? What is wrong or missing? Provide your critique.'}"""

    target_result = await loop.run_in_executor(
        None, lambda: providers[to_model](cross_prompt, cwd=cwd))

    if target_result.error:
        return f"[{to_model} ERROR] {target_result.error}"

    return f"**{from_model.upper()}:**\n{source_result.text}\n\n---\n\n**{to_model.upper()} (critique):**\n{target_result.text}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
