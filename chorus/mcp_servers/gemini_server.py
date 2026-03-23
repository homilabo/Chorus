"""MCP server — Gemini CLI wrapper."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("chorus-gemini")


@mcp.tool()
async def ask_gemini(prompt: str, model: str = "gemini-2.5-pro", cwd: str = ".") -> str:
    """Ask Google Gemini a question. Gemini can search the internet and do research.

    Args:
        prompt: The question or instruction to send to Gemini
        model: Gemini model to use
        cwd: Working directory for file operations
    """
    from chorus.cli import call_gemini
    result = call_gemini(prompt, model, cwd=cwd)
    if result.error:
        return f"[Gemini ERROR] {result.error}"
    return result.text


if __name__ == "__main__":
    mcp.run(transport="stdio")
