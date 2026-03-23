"""MCP server — OpenAI Codex CLI wrapper."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("chorus-codex")


@mcp.tool()
async def ask_codex(prompt: str, model: str = "gpt-5.4", cwd: str = ".") -> str:
    """Ask OpenAI Codex a question. Strong at code generation and analysis.

    Args:
        prompt: The question or instruction to send to Codex
        model: Codex model to use
        cwd: Working directory for file operations
    """
    from chorus.cli import call_codex
    result = call_codex(prompt, model, cwd=cwd)
    if result.error:
        return f"[Codex ERROR] {result.error}"
    return result.text


if __name__ == "__main__":
    mcp.run(transport="stdio")
