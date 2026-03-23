"""MCP server — Chorus memory system (SQLite + FTS5 full-text search)."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("chorus-memory")


def _get_memory():
    """Lazy-init memory connection (singleton)."""
    from chorus.memory import Memory
    if not hasattr(_get_memory, "_instance"):
        _get_memory._instance = Memory()
    return _get_memory._instance


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
