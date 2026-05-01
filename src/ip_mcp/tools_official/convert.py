"""MCP tool: jpo_convert_patent_number — 出願⇄公開⇄登録 番号変換.

Source: 特許庁 特許情報取得API (公式)
Endpoint: GET /api/patent/v1/case_number_reference/{種別}/{番号}
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..jpo.client import JpoClient
from ..jpo.normalize import (
    normalize_application_number,
    normalize_publication_number,
    normalize_registration_number,
    parse_identifier,
)
from ..jpo.status_codes import JpoOutcome

_VALID_TYPES = {"application", "publication", "registration"}


def register(mcp: FastMCP, client: JpoClient) -> None:
    @mcp.tool(
        name="jpo_convert_patent_number",
        description=(
            "Source: 特許庁 特許情報取得API (公式). "
            "Convert between application / publication / registration numbers. "
            "Pass the number in any common Japanese format "
            "(特開2010-228687 / 特願2017-204947 / JP-2025-173545 / bare 10 digits). "
            "Returns the normalized 10-digit number plus its counterparts. "
            "Data freshness: daily. Coverage: patents filed 2003-07 onward."
        ),
    )
    async def jpo_convert_patent_number(
        value: str, source_type: str = "auto"
    ) -> dict[str, Any]:
        try:
            if source_type == "auto":
                kind, number = parse_identifier(value)
            elif source_type in _VALID_TYPES:
                kind = source_type
                number = {
                    "application": normalize_application_number,
                    "publication": normalize_publication_number,
                    "registration": normalize_registration_number,
                }[kind](value)
            else:
                return _err(
                    "bad_input",
                    f"source_type must be one of {sorted(_VALID_TYPES)} or 'auto'",
                )
        except ValueError as exc:
            return _err("bad_input", str(exc))

        endpoint = f"/api/patent/v1/case_number_reference/{kind}/{number}"
        envelope = await client.get_json(endpoint)

        if envelope.is_ok:
            return {
                "ok": True,
                "source": "jpo_official",
                "input": {"raw": value, "type": kind, "normalized": number},
                "data": envelope.data,
                "remaining_today": envelope.remain_access_count,
            }
        return _envelope_error(envelope, endpoint)


def _err(kind: str, message: str) -> dict[str, Any]:
    return {"ok": False, "source": "jpo_official", "kind": kind, "message": message}


def _envelope_error(envelope: Any, endpoint: str) -> dict[str, Any]:
    kind_map = {
        JpoOutcome.NOT_FOUND: "not_found",
        JpoOutcome.DAILY_QUOTA_EXCEEDED: "rate_limited_daily",
        JpoOutcome.BAD_PARAMETER: "bad_parameter",
        JpoOutcome.INVALID_TOKEN: "auth_failed",
        JpoOutcome.SERVER_BUSY: "transient",
        JpoOutcome.TIMEOUT: "transient",
        JpoOutcome.UNKNOWN_ERROR: "unknown_error",
    }
    return {
        "ok": False,
        "source": "jpo_official",
        "kind": kind_map.get(envelope.outcome, "unknown_error"),
        "status_code": envelope.status_code,
        "message": envelope.error_message or "(no message)",
        "endpoint": endpoint,
        "remaining_today": envelope.remain_access_count,
    }
