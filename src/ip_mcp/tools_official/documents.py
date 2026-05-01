"""MCP tool: jpo_get_patent_documents — 申請書類 / 拒絶理由通知書 / 発送書類 のメタ情報.

Source: 特許庁 特許情報取得API (公式)
Endpoints:
  - /api/patent/v1/app_doc_cont_opinion_amendment/{出願番号}    (意見書・補正書)
  - /api/patent/v1/app_doc_cont_refusal_reason/{出願番号}       (拒絶理由通知書)
  - /api/patent/v1/app_doc_cont_refusal_reason_decision/{出願番号} (拒絶査定/特許査定/補正却下)

Note: when the document is small enough the API embeds it; when ≥10MB it returns
a signed download URL instead. Phase 1 returns the raw envelope; the caller
decides whether to fetch the URL.
"""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from ..jpo.client import JpoClient
from ..jpo.normalize import normalize_application_number, parse_identifier
from ._shared import bad_input, envelope_error

DocKind = Literal["opinion_amendment", "refusal_reason", "refusal_reason_decision"]

_KIND_TO_PATH: dict[str, str] = {
    "opinion_amendment": "app_doc_cont_opinion_amendment",
    "refusal_reason": "app_doc_cont_refusal_reason",
    "refusal_reason_decision": "app_doc_cont_refusal_reason_decision",
}


def register(mcp: FastMCP, client: JpoClient) -> None:
    @mcp.tool(
        name="jpo_get_patent_documents",
        description=(
            "Source: 特許庁 特許情報取得API (公式). "
            "Fetch document metadata (and small documents inline) for a Japanese patent. "
            "kind is one of: "
            "'opinion_amendment' (意見書・手続補正書), "
            "'refusal_reason' (拒絶理由通知書), "
            "'refusal_reason_decision' (特許査定/拒絶査定/補正却下決定). "
            "For documents ≥10MB the API returns a signed URL instead of the bytes. "
            "Data freshness: daily. Coverage: documents from 2019-01 onward."
        ),
    )
    async def jpo_get_patent_documents(
        application_number: str, kind: str = "refusal_reason"
    ) -> dict[str, Any]:
        path = _KIND_TO_PATH.get(kind)
        if path is None:
            return bad_input(f"kind must be one of {sorted(_KIND_TO_PATH)}")
        try:
            k, number = parse_identifier(application_number)
            if k != "application":
                return bad_input(f"expected application number; got {k}={number}")
            number = normalize_application_number(number)
        except ValueError as exc:
            return bad_input(str(exc))

        endpoint = f"/api/patent/v1/{path}/{number}"
        env = await client.get_json(endpoint)
        if env.is_ok:
            return {
                "ok": True,
                "source": "jpo_official",
                "input": {"application_number": number, "kind": kind},
                "data": env.data,
                "remaining_today": env.remain_access_count,
            }
        return envelope_error(env, endpoint)
