"""MCP tool: jpo_get_patent_documents — 申請書類 / 拒絶理由通知書 / 発送書類 のメタ情報.

Source: 特許庁 特許情報取得API (公式)
Endpoints:
  - /api/patent/v1/app_doc_cont_opinion_amendment/{出願番号}    (意見書・補正書)
  - /api/patent/v1/app_doc_cont_refusal_reason/{出願番号}       (拒絶理由通知書)
  - /api/patent/v1/app_doc_cont_refusal_reason_decision/{出願番号} (拒絶査定/特許査定/補正却下)

The JPO API returns documents in TWO different shapes for these endpoints:

  1. Small documents → raw ZIP bytes inline (Content-Type contains "zip" or
     body starts with the PK\\x03\\x04 magic).
  2. Large documents (≥10MB) → JSON envelope with a signed download URL in
     ``result.data.URL``.

Phase 1 returns:
  - result_kind="inline_zip" with base64-encoded content (capped at 8MB to
    keep LLM context manageable) when bytes are returned inline
  - result_kind="signed_url" with the URL when the API returns a signed link
  - result_kind="envelope" with raw envelope data otherwise (other 200/JSON
    shapes — preserved verbatim for the caller)
  - structured error otherwise (mapped from JpoOutcome)

The MCP layer never falls back to a different data source on error.
"""

from __future__ import annotations

import base64
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

_INLINE_BYTE_LIMIT = 8 * 1024 * 1024


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
            "For small documents the response.result_kind == 'inline_zip' and "
            "response.content_base64 holds the ZIP bytes. "
            "For large documents the response.result_kind == 'signed_url' and "
            "response.signed_url is a one-shot download link. "
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
        raw = await client.get_raw(endpoint)

        # Path 1: binary inline (ZIP/PDF)
        if raw.is_binary:
            size = len(raw.content)
            response: dict[str, Any] = {
                "ok": True,
                "source": "jpo_official",
                "input": {"application_number": number, "kind": kind},
                "result_kind": "inline_zip",
                "content_type": raw.content_type or "application/zip",
                "size_bytes": size,
            }
            if size <= _INLINE_BYTE_LIMIT:
                response["content_base64"] = base64.b64encode(raw.content).decode("ascii")
            else:
                response["note"] = (
                    f"document is {size} bytes; exceeds inline limit "
                    f"({_INLINE_BYTE_LIMIT}) - fetch out of band"
                )
            return response

        # Path 2: JSON envelope
        env = raw.envelope()
        if env.is_ok:
            data = env.data or {}
            signed_url = data.get("URL") if isinstance(data, dict) else None
            return {
                "ok": True,
                "source": "jpo_official",
                "input": {"application_number": number, "kind": kind},
                "result_kind": "signed_url" if signed_url else "envelope",
                "signed_url": signed_url,
                "data": data,
                "remaining_today": env.remain_access_count,
            }
        return envelope_error(env, endpoint)
