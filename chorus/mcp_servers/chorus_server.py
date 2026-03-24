"""Chorus MCP server — multi-model orchestration via CLI subscriptions."""

import asyncio

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "chorus",
    instructions="""You have access to multiple AI models via Chorus tools.

Model tools:
- ask: Ask a specific model (provider: gemini, copilot, codex, claude)
- ask_all: Ask all models in parallel — use for comparisons, research, multiple perspectives
- parallel_ask: Run multiple calls (same or different providers) simultaneously
- debate: Multi-round debate between models
- cross_send: Send one model's response to another for critique

Memory tools:
- search_memory: Search past conversations
- save_to_memory: Save important content for future retrieval
- save_session_summary: Save session summary at end of discussion
- search_summaries: Search session summaries

Guidelines:
- Match the user's language
- For simple questions, answer directly without calling models
- For comparisons, debates, research — use ask_all for multiple perspectives
- Tell the user what you're about to do before calling tools
- Synthesize results by highlighting agreements, disagreements, and insights
- Be natural and conversational""",
)

AVAILABLE_PROVIDERS = ["gemini", "copilot", "codex", "claude"]


def _get_provider_fn(name: str):
    """Get the CLI call function for a provider."""
    from chorus.cli import call_gemini, call_copilot, call_codex, call_claude
    return {
        "gemini": call_gemini,
        "copilot": call_copilot,
        "codex": call_codex,
        "claude": call_claude,
    }.get(name)


# ─── Core tools ───

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


@mcp.tool()
async def debate(prompt: str, rounds: int = 2, exclude: list[str] = None, cwd: str = ".") -> str:
    """Run a multi-round debate between models on a topic.

    Each round, all models see the previous round's responses and can agree, disagree, or refine.

    Args:
        prompt: The debate topic
        rounds: Number of rounds (default 2)
        exclude: Models to skip
        cwd: Working directory for file operations
    """
    all_rounds = []

    first = await ask_all(prompt, exclude, cwd)
    all_rounds.append(f"**Round 1:**\n{first}")

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
    from_fn = _get_provider_fn(from_model)
    to_fn = _get_provider_fn(to_model)

    if not from_fn:
        return f"Unknown model: {from_model}. Available: {', '.join(AVAILABLE_PROVIDERS)}"
    if not to_fn:
        return f"Unknown model: {to_model}. Available: {', '.join(AVAILABLE_PROVIDERS)}"

    loop = asyncio.get_event_loop()

    source_result = await loop.run_in_executor(
        None, lambda: from_fn(context or "Share your analysis", cwd=cwd))

    if source_result.error:
        return f"[{from_model} ERROR] {source_result.error}"

    cross_prompt = f"""{from_model.upper()} said:
{source_result.text}

{context or 'Review the above. What is good? What is wrong or missing? Provide your critique.'}"""

    target_result = await loop.run_in_executor(
        None, lambda: to_fn(cross_prompt, cwd=cwd))

    if target_result.error:
        return f"[{to_model} ERROR] {target_result.error}"

    return f"**{from_model.upper()}:**\n{source_result.text}\n\n---\n\n**{to_model.upper()} (critique):**\n{target_result.text}"


# ─── Memory tools ───

_current_session_id = None


def _get_memory():
    from chorus.memory import Memory
    if not hasattr(_get_memory, "_instance"):
        _get_memory._instance = Memory()
    return _get_memory._instance


def _ensure_session():
    global _current_session_id
    if not _current_session_id:
        memory = _get_memory()
        session = memory.create_session()
        _current_session_id = session.id
    return _current_session_id


@mcp.tool()
async def save_to_memory(content: str, provider: str = "user", role: str = "assistant") -> str:
    """Save content to Chorus memory for future retrieval via search_memory.

    Call this to persist important findings, research results, or decisions.

    Args:
        content: The text content to save
        provider: Who produced this (e.g. "gemini", "codex", "user", "chorus")
        role: Message role — "user" or "assistant"
    """
    from chorus.models import Message
    memory = _get_memory()
    session_id = _ensure_session()
    memory.save_message(session_id, Message(role=role, content=content, provider=provider))
    return f"Saved to memory (session {session_id[:8]})."


@mcp.tool()
async def save_session_summary(summary: str, topics: str = "") -> str:
    """Save a summary for the current session. Call this at the end of a research or discussion.

    Args:
        summary: 2-3 sentence summary of what was discussed
        topics: Comma-separated key topics (e.g. "karavan, DMK, price")
    """
    memory = _get_memory()
    session_id = _ensure_session()
    msg_count = memory.get_message_count(session_id)
    memory.save_session_summary(session_id, summary, topics, msg_count)
    return f"Session summary saved (session {session_id[:8]})."


@mcp.tool()
async def search_memory(query: str, limit: int = 10) -> str:
    """Search past conversations for relevant content using full-text search.

    Args:
        query: Search query (supports multiple words with OR logic)
        limit: Maximum number of results to return
    """
    memory = _get_memory()
    results = memory.search(query, limit)
    if not results:
        return "No results found."
    parts = []
    for r in results:
        provider = r.get("provider", "?")
        timestamp = r.get("timestamp", "")[:16]
        content = r["content"][:300]
        parts.append(f"[{provider}] {timestamp}:\n{content}")
    return "\n\n---\n\n".join(parts)


@mcp.tool()
async def search_summaries(query: str, limit: int = 5) -> str:
    """Search session summaries for high-level topic matching.

    Args:
        query: Search query
        limit: Maximum number of results
    """
    memory = _get_memory()
    results = memory.search_summaries(query, limit)
    if not results:
        return "No matching summaries found."
    parts = []
    for s in results:
        date = s.get("created_at", "")[:10]
        summary = s.get("summary", "")
        topics = s.get("key_topics", "")
        line = f"[{date}] {summary}"
        if topics:
            line += f" (topics: {topics})"
        parts.append(line)
    return "\n\n".join(parts)


if __name__ == "__main__":
    mcp.run(transport="stdio")
