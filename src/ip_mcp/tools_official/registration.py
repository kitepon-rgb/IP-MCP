"""MCP tool: jpo_get_patent_registration — 登録情報・権利状態.

Source: 特許庁 特許情報取得API (公式)
Endpoint: /api/patent/v1/registration_info/{出願番号}
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..jpo.client import JpoClient
from ..jpo.normalize import normalize_application_number, parse_identifier
from ._shared import bad_input, envelope_error


def register(mcp: FastMCP, client: JpoClient) -> None:
    @mcp.tool(
        name="jpo_get_patent_registration",
        description=(
            "Source: 特許庁 特許情報取得API (公式). "
            "Fetch registration info and right status for a Japanese patent. "
            "Pass the application number (10-digit, or 特願YYYY-NNNNNN). "
            "Use jpo_convert_patent_number first if you only have publication/registration number. "
            "Returns: registration number, status (権利存続中等), expiry, holder. "
            "Data freshness: daily."
        ),
    )
    async def jpo_get_patent_registration(application_number: str) -> dict[str, Any]:
        try:
            kind, number = parse_identifier(application_number)
            if kind != "application":
                return bad_input(
                    f"expected an application number; got {kind}={number}. "
                    "Call jpo_convert_patent_number first."
                )
            number = normalize_application_number(number)
        except ValueError as exc:
            return bad_input(str(exc))

        endpoint = f"/api/patent/v1/registration_info/{number}"
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
