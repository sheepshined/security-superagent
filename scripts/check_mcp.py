"""Verify the local Streamable HTTP MCP gateway without exposing sensitive content."""

import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def _ensure_local_proxy_bypass(url: str) -> None:
    """Keep loopback MCP diagnostics away from machine-wide HTTP proxies."""
    if not url.startswith(("http://127.0.0.1", "http://localhost")):
        return
    for name in ("NO_PROXY", "no_proxy"):
        entries = [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]
        for host in ("127.0.0.1", "localhost"):
            if host not in entries:
                entries.append(host)
        os.environ[name] = ",".join(entries)


async def check(url: str) -> None:
    _ensure_local_proxy_bypass(url)
    async with (
        streamable_http_client(url) as (read_stream, write_stream, _),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        names = sorted(tool.name for tool in tools.tools)
        alert = await session.call_tool(
            "alert_context",
            arguments={"external_id": "A-SUP-001"},
        )
        print(
            json.dumps(
                {
                    "url": url,
                    "tools": names,
                    "alert_call_error": alert.isError,
                    "content_blocks": len(alert.content),
                },
                ensure_ascii=False,
            )
        )
        expected = {"knowledge_search", "alert_context", "trace_summary"}
        if set(names) != expected or alert.isError:
            raise SystemExit(1)


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8100/mcp"
    asyncio.run(check(url))


if __name__ == "__main__":
    main()
