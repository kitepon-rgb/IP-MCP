"""MCP tools: jpo_get_divisional_apps, jpo_get_priority_apps.

Source: 特許庁 特許情報取得API (公式)
Endpoints:
  - /api/patent/v1/divisional_app_info/{出願番号}
  - /api/patent/v1/priority_right_app_info/{出願番号}
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..jpo.client import JpoClient
from ..jpo.normalize import normalize_application_number, parse_identifier
from ._shared import bad_input, envelope_error


def _ensure_app_number(value: str) -> str:
    kind, number = parse_identifier(value)
    if kind != "application":
        raise ValueError(f"expected application number; got {kind}={number}")
    return normalize_application_number(number)


def register(mcp: FastMCP, client: JpoClient) -> None:
    @mcp.tool(
        name="jpo_get_divisional_apps",
        description=(
            "Source: 特許庁 特許情報取得API (公式). "
            "List divisional (分割出願) information for a Japanese patent application. "
            "Pass the application number. Data freshness: daily."
        ),
    )
    async def jpo_get_divisional_apps(application_number: str) -> dict[str, Any]:
        try:
            number = _ensure_app_number(application_number)
        except ValueError as exc:
            return bad_input(str(exc))
        endpoint = f"/api/patent/v1/divisional_app_info/{number}"
        env = await client.get_json(endpoint)
        if env.is_ok:
            return {
                "ok": True, "source": "jpo_official",
                "input": {"application_number": number},
                "data": env.data, "remaining_today": env.remain_access_count,
            }
        return envelope_error(env, endpoint)

    @mcp.tool(
        name="jpo_get_priority_apps",
        description=(
            "Source: 特許庁 特許情報取得API (公式). "
            "List priority-basis (優先基礎出願) information for a Japanese patent application. "
            "Pass the application number. Data freshness: daily."
        ),
    )
    async def jpo_get_priority_apps(application_number: str) -> dict[str, Any]:
        try:
            number = _ensure_app_number(application_number)
        except ValueError as exc:
            return bad_input(str(exc))
        endpoint = f"/api/patent/v1/priority_right_app_info/{number}"
        env = await client.get_json(endpoint)
        if env.is_ok:
            return {
                "ok": True, "source": "jpo_official",
                "input": {"application_number": number},
                "data": env.data, "remaining_today": env.remain_access_count,
            }
        return envelope_error(env, endpoint)
