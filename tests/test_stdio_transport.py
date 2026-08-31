"""Regression coverage for the real stdio MCP transport."""
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test_mission_create_returns_over_stdio(tmp_path):
    repo = str(tmp_path)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "hamgoose"],
        # model_preflight off: this test covers the TRANSPORT, not the
        # capability smoke (which spawns a live goose leaf, HG-07).
        env=dict(os.environ, HAMGOOSE_REPO=repo,
                 HAMGOOSE_CONFIG=json.dumps({"execution": {"model_preflight": False}})),
        cwd=repo,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=5)
            result = await asyncio.wait_for(
                session.call_tool("mission_create", {"goal": "stdio transport regression", "repo": repo}),
                timeout=5,
            )

    assert not result.isError
    assert "Mission created:" in result.content[0].text
