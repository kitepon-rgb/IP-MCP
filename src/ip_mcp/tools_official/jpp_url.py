"""MCP tool: jpo_get_jpp_url — J-PlatPat 固定アドレス取得.

Source: 特許庁 特許情報取得API (公式)
Endpoint: /api/patent/v1/jpp_fixed_address/{出願番号}
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..jpo.client import JpoClient
from ..jpo.normalize import normalize_application_number, parse_identifier
from ._shared import bad_input, envelope_error


def register(mcp: FastMCP, client: JpoClient) -> None:
    @mcp.tool(
        name="jpo_get_jpp_url",
        description=(
            "Source: 特許庁 特許情報取得API (公式). "
            "Get the canonical J-PlatPat URL for a Japanese patent application. "
            "Use this when you want to give a human reader a link to view the patent "
            "in the official J-PlatPat web UI. Pass the application number."
        ),
    )
    async def jpo_get_jpp_url(application_number: str) -> dict[str, Any]:
        try:
            kind, number = parse_identifier(application_number)
            if kind != "application":
                return bad_input(f"expected application number; got {kind}={number}")
            number = normalize_application_number(number)
        except ValueError as exc:
            return bad_input(str(exc))

        endpoint = f"/api/patent/v1/jpp_fixed_address/{number}"
        env = await client.get_json(endpoint)
        if env.is_ok:
            return {
                "ok": True,
                "source": "jpo_official",
                "input": {"application_number": number},
                "data": env.data,
                "remaining_today": env.remain_access_count,
            }
        return envelope_error(env, endpoint)
