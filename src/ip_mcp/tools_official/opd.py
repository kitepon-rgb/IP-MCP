"""MCP tools: jpo_get_opd_family, jpo_get_opd_doc_list — OPD (One Portal Dossier).

Source: 特許庁 特許情報取得API (公式)、OPD は五庁 (JPO/USPTO/EPO/CNIPA/KIPO) 横断
Base: /opdapi/patent/v1/...
Rate limit: 5 requests/min (different from the 10/min domestic limit)
Daily quota: shared OPD quota — separate from /api/patent/v1/* quotas
"""

from __future__ import annotations

import os
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


def opd_enabled() -> bool:
    return os.getenv("JPO_ENABLE_OPD", "1").lower() not in {"0", "false", "no", ""}


def register(mcp: FastMCP, client: JpoClient) -> None:
    if not opd_enabled():
        # OPD tools are hidden when JPO_ENABLE_OPD=0 (no contract / quota
        # exhausted). Calling code should treat their absence as "no OPD here".
        return

    @mcp.tool(
        name="jpo_get_opd_family",
        description=(
            "Source: 特許庁 特許情報取得API (公式 OPD). "
            "Retrieve the patent family across JPO / USPTO / EPO / CNIPA / KIPO. "
            "Pass the JP application number. Returns DOCDB-format family records. "
            "OPD has its own daily quota and a 5 req/min rate limit (smaller than "
            "the 10 req/min domestic limit). Data freshness: daily."
        ),
    )
    async def jpo_get_opd_family(application_number: str) -> dict[str, Any]:
        try:
            number = _ensure_app_number(application_number)
        except ValueError as exc:
            return bad_input(str(exc))
        # OPD wants DOCDB-format — JP{appno}, type=A
        endpoint = f"/opdapi/patent/v1/family/A/JP{number}"
        env = await client.get_json(endpoint, opd=True)
        if env.is_ok:
            return {
                "ok": True, "source": "jpo_official",
                "input": {"application_number": number},
                "data": env.data, "remaining_today": env.remain_access_count,
            }
        return envelope_error(env, endpoint)

    @mcp.tool(
        name="jpo_get_opd_doc_list",
        description=(
            "Source: 特許庁 特許情報取得API (公式 OPD). "
            "List downloadable office-action / publication documents for a JP "
            "application via the OPD endpoint. Use this to find the document IDs "
            "before fetching individual documents. "
            "OPD: 5 req/min, separate daily quota. Data freshness: daily."
        ),
    )
    async def jpo_get_opd_doc_list(application_number: str) -> dict[str, Any]:
        try:
            number = _ensure_app_number(application_number)
        except ValueError as exc:
            return bad_input(str(exc))
        endpoint = f"/opdapi/patent/v1/global_doc_list/JP{number}"
        env = await client.get_json(endpoint, opd=True)
        if env.is_ok:
            return {
                "ok": True, "source": "jpo_official",
                "input": {"application_number": number},
                "data": env.data, "remaining_today": env.remain_access_count,
            }
        return envelope_error(env, endpoint)
