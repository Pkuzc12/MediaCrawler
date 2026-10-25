from __future__ import annotations

import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, stdio_client


async def test_server_lists_tools_over_real_stdio(
    fake_mediacrawler_root: Path, tmp_path: Path, listening_port: int
) -> None:
    env = os.environ.copy()
    env["MEDIACRAWLER_ROOT"] = str(fake_mediacrawler_root)
    env["MEDIACRAWLER_PYTHON"] = sys.executable
    env["MC_MCP_STATE_DIR"] = str(tmp_path / "stdio runs")
    env["MC_MCP_CDP_PORT"] = str(listening_port)
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "mc_mcp"], cwd=tmp_path, env=env
    )

    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        await session.initialize()
        tools = await session.list_tools()
        result = await session.call_tool("check", {})

    assert {tool.name for tool in tools.tools} == {
        "check",
        "crawl",
        "status",
        "stop",
        "results",
        "runs",
    }
    assert result.structured_content["ready"] is True
