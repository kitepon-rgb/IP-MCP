"""MCP tool: jpo_lookup_applicant — 出願人/代理人 名⇄コード ルックアップ.

Source: 特許庁 特許情報取得API (公式)
Endpoints:
  - /api/patent/v1/applicant_attorney_cd/{申請人コード}  (code → name)
  - /api/patent/v1/applicant_attorney/{申請人氏名・名称}  (EXACT-MATCH ONLY → code)
"""

from __future__ import annotations

import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..jpo.client import JpoClient
from ._shared import bad_input, envelope_error


def register(mcp: FastMCP, client: JpoClient) -> None:
    @mcp.tool(
        name="jpo_lookup_applicant",
        description=(
            "Source: 特許庁 特許情報取得API (公式). "
            "Translate between an applicant/attorney code (numeric, e.g. 511073075) "
            "and the registered name. "
            "⚠ Name lookup is EXACT-MATCH ONLY — whitespace, full/half-width, "
            "and case all matter. Fuzzy / partial search is NOT supported. "
            "Pass either a numeric code (returns name) or the exact name (returns code). "
            "Data freshness: daily."
        ),
    )
    async def jpo_lookup_applicant(value: str) -> dict[str, Any]:
        text = (value or "").strip()
        if not text:
            return bad_input("value is empty")

        is_code = bool(re.fullmatch(r"\d+", text))
        if is_code:
            endpoint = f"/api/patent/v1/applicant_attorney_cd/{text}"
            direction = "code_to_name"
        else:
            endpoint = f"/api/patent/v1/applicant_attorney/{text}"
            direction = "name_to_code"

        env = await client.get_json(endpoint)
        if env.is_ok:
            return {
                "ok": True,
                "source": "jpo_official",
                "input": {"value": text, "direction": direction},
                "data": env.data,
                "remaining_today": env.remain_access_count,
            }
        return envelope_error(env, endpoint)
