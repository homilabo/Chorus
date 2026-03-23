"""MCP server — Claude CLI wrapper (used when Gemini is the conductor)."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("chorus-claude")


@mcp.tool()
async def ask_claude(prompt: str, model: str = "sonnet", cwd: str = ".") -> str:
    """Ask Claude a question. Strong at reasoning, analysis, and coding.

    Args:
        prompt: The question or instruction to send to Claude
        model: Claude model to use (sonnet, opus, haiku)
        cwd: Working directory for file operations
    """
    from chorus.cli import call_claude
    result = call_claude(prompt, model, cwd=cwd)
    if result.error:
        return f"[Claude ERROR] {result.error}"
    return result.text


if __name__ == "__main__":
    mcp.run(transport="stdio")
