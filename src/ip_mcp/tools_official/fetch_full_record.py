"""MCP tool: jpo_fetch_full_record — 1 番号から書誌+経過+登録+引用を一括取得.

Source: 特許庁 特許情報取得API (公式)
Composite of: case_number_reference + app_progress + registration_info + cite_doc_info
All calls stay within the official API; never falls back to external sources.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..jpo.client import JpoClient
from ..jpo.normalize import (
    normalize_application_number,
    parse_identifier,
)
from ._shared import bad_input


async def _fetch_one(client: JpoClient, endpoint: str) -> dict[str, Any]:
    env = await client.get_json(endpoint)
    return {
        "ok": env.is_ok,
        "status_code": env.status_code,
        "kind": env.outcome.value,
        "data": env.data if env.is_ok else None,
        "error": env.error_message if not env.is_ok else None,
        "remaining_today": env.remain_access_count,
    }


def register(mcp: FastMCP, client: JpoClient) -> None:
    @mcp.tool(
        name="jpo_fetch_full_record",
        description=(
            "Source: 特許庁 特許情報取得API (公式). "
            "Composite tool: takes ANY identifier (application/publication/registration "
            "number, in any common Japanese format) and fetches in one call: "
            "(1) number cross-reference, (2) full examination progress, "
            "(3) registration / right status, (4) cited documents. "
            "Each sub-result reports its own status, so partial success is visible. "
            "All four calls run concurrently against the official JPO API. "
            "Quota cost: 1 call from each of 4 separate daily quotas "
            "(case_number_reference + app_progress + registration_info + cite_doc_info); "
            "the bottleneck is whichever quota is lowest. "
            "Data freshness: daily."
        ),
    )
    async def jpo_fetch_full_record(value: str) -> dict[str, Any]:
        # Step 1: figure out the identifier kind and resolve to application number
        try:
            kind, number = parse_identifier(value)
        except ValueError as exc:
            return bad_input(str(exc))

        # Number cross-reference call. This also gives us app/pub/reg numbers
        # to attach to the response even if downstream calls fail.
        ref_endpoint = f"/api/patent/v1/case_number_reference/{kind}/{number}"
        ref = await _fetch_one(client, ref_endpoint)

        # Resolve application number for the remaining calls
        app_no: str | None = None
        if ref["ok"] and ref["data"]:
            app_no = (
                str(ref["data"].get("applicationNumber") or "").strip() or None
            )
        if app_no is None and kind == "application":
            try:
                app_no = normalize_application_number(number)
            except ValueError:
                app_no = None

        if app_no is None:
            return {
                "ok": False,
                "source": "jpo_official",
                "kind": "unresolved_application_number",
                "input": {"raw": value, "type": kind, "normalized": number},
                "case_number_reference": ref,
                "message": (
                    "Could not resolve to an application number. "
                    "If you passed a registration number, the patent may not exist."
                ),
            }

        # Step 2: 3 calls in parallel — official API only, no fallback
        progress_ep = f"/api/patent/v1/app_progress/{app_no}"
        registration_ep = f"/api/patent/v1/registration_info/{app_no}"
        citations_ep = f"/api/patent/v1/cite_doc_info/{app_no}"

        progress_r, registration_r, citations_r = await asyncio.gather(
            _fetch_one(client, progress_ep),
            _fetch_one(client, registration_ep),
            _fetch_one(client, citations_ep),
        )

        any_ok = ref["ok"] or progress_r["ok"] or registration_r["ok"] or citations_r["ok"]
        return {
            "ok": any_ok,
            "source": "jpo_official",
            "input": {"raw": value, "type": kind, "normalized": number},
            "application_number": app_no,
            "case_number_reference": ref,
            "progress": progress_r,
            "registration": registration_r,
            "citations": citations_r,
        }
