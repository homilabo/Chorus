"""Chorus MCP server — all tools in a single server."""

import asyncio

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("chorus")


# ─── Individual model tools ───
# Model defaults come from ~/.chorus/config.yaml, not from these signatures.

@mcp.tool()
async def ask_gemini(prompt: str, cwd: str = ".") -> str:
    """Ask Google Gemini a question. Gemini can search the internet and do research.

    Args:
        prompt: The question or instruction to send to Gemini
        cwd: Working directory for file operations
    """
    from chorus.cli import call_gemini
    result = call_gemini(prompt, cwd=cwd)
    if result.error:
        return f"[Gemini ERROR] {result.error}"
    return result.text


@mcp.tool()
async def ask_copilot(prompt: str, cwd: str = ".") -> str:
    """Ask GitHub Copilot a question. Good for code-related tasks.

    Args:
        prompt: The question or instruction to send to Copilot
        cwd: Working directory for file operations
    """
    from chorus.cli import call_copilot
    result = call_copilot(prompt, cwd=cwd)
    if result.error:
        return f"[Copilot ERROR] {result.error}"
    return result.text


@mcp.tool()
async def ask_codex(prompt: str, cwd: str = ".") -> str:
    """Ask OpenAI Codex a question. Strong at code generation and analysis.

    Args:
        prompt: The question or instruction to send to Codex
        cwd: Working directory for file operations
    """
    from chorus.cli import call_codex
    result = call_codex(prompt, cwd=cwd)
    if result.error:
        return f"[Codex ERROR] {result.error}"
    return result.text


@mcp.tool()
async def ask_claude(prompt: str, cwd: str = ".") -> str:
    """Ask Claude a question. Strong at reasoning, analysis, and coding.
    Use this when another model (e.g. Gemini) is the conductor.

    Args:
        prompt: The question or instruction to send to Claude
        cwd: Working directory for file operations
    """
    from chorus.cli import call_claude
    result = call_claude(prompt, cwd=cwd)
    if result.error:
        return f"[Claude ERROR] {result.error}"
    return result.text


# ─── Orchestration tools ───

def _get_providers():
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
    providers = _get_providers()

    if from_model not in providers:
        return f"Unknown model: {from_model}. Available: {', '.join(providers.keys())}"
    if to_model not in providers:
        return f"Unknown model: {to_model}. Available: {', '.join(providers.keys())}"

    loop = asyncio.get_event_loop()

    source_result = await loop.run_in_executor(
        None, lambda: providers[from_model](context or "Share your analysis", cwd=cwd))

    if source_result.error:
        return f"[{from_model} ERROR] {source_result.error}"

    cross_prompt = f"""{from_model.upper()} said:
{source_result.text}

{context or 'Review the above. What is good? What is wrong or missing? Provide your critique.'}"""

    target_result = await loop.run_in_executor(
        None, lambda: providers[to_model](cross_prompt, cwd=cwd))

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
        topics: Comma-separated key topics (e.g. "karavan, DMK, fiyat")
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
async def get_recent_sessions(limit: int = 5) -> str:
    """List recent conversation sessions.

    Args:
        limit: Number of sessions to return
    """
    memory = _get_memory()
    sessions = memory.list_sessions(limit)
    if not sessions:
        return "No sessions yet."
    parts = []
    for s in sessions:
        sid = s["id"][:8]
        title = s.get("title", "Untitled")
        updated = s["updated_at"][:16]
        parts.append(f"{sid} | {title} | {updated}")
    return "\n".join(parts)


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
