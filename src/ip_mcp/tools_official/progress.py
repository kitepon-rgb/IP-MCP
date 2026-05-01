"""MCP tool: jpo_get_patent_progress — 経過情報.

Source: 特許庁 特許情報取得API (公式)
Endpoint:
  - /api/patent/v1/app_progress/{出願番号}        (full)
  - /api/patent/v1/app_progress_simple/{出願番号} (simple, omits priority/divisional)
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..jpo.client import JpoClient
from ..jpo.normalize import normalize_application_number, parse_identifier
from ._shared import bad_input, envelope_error


def register(mcp: FastMCP, client: JpoClient) -> None:
    @mcp.tool(
        name="jpo_get_patent_progress",
        description=(
            "Source: 特許庁 特許情報取得API (公式). "
            "Fetch examination/registration progress for a Japanese patent application. "
            "Pass the application number (10-digit, or 特願YYYY-NNNNNN). "
            "If you only have a publication number, call jpo_convert_patent_number first. "
            "Set simple=true for a smaller payload that omits priority/divisional info. "
            "Data freshness: daily. Coverage: patents filed 2003-07 onward."
        ),
    )
    async def jpo_get_patent_progress(
        application_number: str, simple: bool = False
    ) -> dict[str, Any]:
        try:
            kind, number = parse_identifier(application_number)
            if kind != "application":
                # Best effort: if user passed pub/reg, hint they should convert first
                return bad_input(
                    f"expected an application number; got {kind}={number}. "
                    "Call jpo_convert_patent_number first to translate."
                )
            number = normalize_application_number(number)
        except ValueError as exc:
            return bad_input(str(exc))

        path = "app_progress_simple" if simple else "app_progress"
        endpoint = f"/api/patent/v1/{path}/{number}"
        envelope = await client.get_json(endpoint)

        if envelope.is_ok:
            return {
                "ok": True,
                "source": "jpo_official",
                "input": {"application_number": number, "simple": simple},
                "data": envelope.data,
                "remaining_today": envelope.remain_access_count,
            }
        return envelope_error(envelope, endpoint)
