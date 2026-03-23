"""MCP server — GitHub Copilot CLI wrapper."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("chorus-copilot")


@mcp.tool()
async def ask_copilot(prompt: str, model: str = "gpt-5-mini", cwd: str = ".") -> str:
    """Ask GitHub Copilot a question. Good for code-related tasks.

    Args:
        prompt: The question or instruction to send to Copilot
        model: Copilot model to use
        cwd: Working directory for file operations
    """
    from chorus.cli import call_copilot
    result = call_copilot(prompt, model, cwd=cwd)
    if result.error:
        return f"[Copilot ERROR] {result.error}"
    return result.text


if __name__ == "__main__":
    mcp.run(transport="stdio")
