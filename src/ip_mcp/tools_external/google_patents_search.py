"""MCP tool: external_search_patents_by_keyword.

Source: Google Patents (非公式 XHR エンドポイント、参考用)

⚠ Phase 1B — completely independent of tools_official/*.
   - Does NOT import from ip_mcp.jpo or ip_mcp.tools_official.
   - On failure, returns a structured error. Callers (LLM) decide whether
     to retry or to consult official tools — never automatic fallback.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

log = logging.getLogger(__name__)

GOOGLE_PATENTS_XHR_URL = "https://patents.google.com/xhr/query"
_MIN_INTERVAL_SECONDS = 3.0
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_state_lock = asyncio.Lock()
_last_request_at: float = 0.0


class SearchUnavailableError(Exception):
    """503 / connection failure from the non-official endpoint."""


def enabled() -> bool:
    return os.getenv("EXTERNAL_GOOGLE_PATENTS_ENABLED", "1") not in {
        "0", "false", "no", ""
    }


async def _wait_rate_limit() -> None:
    global _last_request_at
    async with _state_lock:
        elapsed = time.monotonic() - _last_request_at
        wait = _MIN_INTERVAL_SECONDS - elapsed
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_at = time.monotonic()


def _build_query_string(query: str, *, assignee: str, ipc: str) -> str:
    parts = [query]
    if assignee:
        parts.append(f"assignee:({assignee})")
    if ipc:
        parts.append(f"cpc=({ipc})")
    return " ".join(parts).strip()


def _build_params(
    query: str,
    *,
    num: int,
    page: int,
    sort: str,
    before: str,
    after: str,
    assignee: str,
    ipc: str,
) -> dict[str, str]:
    p: dict[str, str] = {
        "q": _build_query_string(query, assignee=assignee, ipc=ipc),
        "country": "JP",
        "language": "JAPANESE",
        "type": "PATENT",
        "num": str(num),
    }
    if page:
        p["page"] = str(page)
    if before:
        p["before"] = f"priority:{before}"
    if after:
        p["after"] = f"priority:{after}"
    if sort in ("new", "old"):
        p["sort"] = sort
    return p


async def _do_search(params: dict[str, str]) -> dict[str, Any]:
    """Issue a single XHR call with up to 3 backoff retries on 503.

    All retries stay against the SAME endpoint — never falls over to anything else.
    """
    encoded = "&".join(f"{k}={v}" for k, v in params.items())
    backoffs = (2.0, 4.0, 8.0)

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
        "Accept-Language": "ja,en;q=0.9",
        "Referer": "https://patents.google.com/",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        last_status = 0
        for attempt, backoff in enumerate([0.0, *backoffs], start=0):
            if backoff > 0:
                await asyncio.sleep(backoff)
            await _wait_rate_limit()
            try:
                response = await client.get(
                    GOOGLE_PATENTS_XHR_URL,
                    params={"url": encoded, "exp": ""},
                    headers=headers,
                )
            except httpx.RequestError as exc:
                raise SearchUnavailableError(
                    f"Google Patents transport error: {type(exc).__name__}: {exc}"
                ) from exc

            last_status = response.status_code
            if response.status_code == 503:
                log.warning(
                    "Google Patents 503 (attempt %d), backoff next", attempt + 1
                )
                continue
            response.raise_for_status()
            return response.json()

    raise SearchUnavailableError(
        f"Google Patents returned 503 after retries (last status {last_status}). "
        "The non-official endpoint is rate-limiting or blocking us."
    )


def register(mcp: FastMCP) -> None:
    if not enabled():
        log.info(
            "EXTERNAL_GOOGLE_PATENTS_ENABLED disabled — external_search_* tools will not be exposed"
        )
        return

    @mcp.tool(
        name="external_search_patents_by_keyword",
        description=(
            "Source: Google Patents (非公式 XHR エンドポイント、参考用). "
            "⚠ Non-official data source. Always verify discovered patents by "
            "calling jpo_convert_patent_number → jpo_fetch_full_record afterwards. "
            "Free-text keyword search of Japanese patents. "
            "Optional filters: assignee (substring OK here), ipc (e.g. 'B65G'), "
            "before / after (priority date, YYYYMMDD or YYYY-MM-DD), "
            "sort ('relevance' | 'new' | 'old'). "
            "Rate-limited internally (3s spacing, 3-attempt exponential backoff on 503). "
            "Returns Google Patents' raw XHR JSON; on failure returns a structured "
            "{ok:false, source:'google_patents_unofficial', kind:'search_unavailable'} — "
            "the official JPO API has NO keyword-search capability so do NOT silently "
            "retry against it."
        ),
    )
    async def external_search_patents_by_keyword(
        query: str,
        num: int = 10,
        page: int = 0,
        sort: str = "relevance",
        before: str = "",
        after: str = "",
        assignee: str = "",
        ipc: str = "",
    ) -> dict[str, Any]:
        if not query or not query.strip():
            return {
                "ok": False,
                "source": "google_patents_unofficial",
                "kind": "bad_input",
                "message": "query is empty",
            }
        params = _build_params(
            query.strip(),
            num=max(1, min(num, 100)),
            page=max(0, page),
            sort=sort,
            before=before,
            after=after,
            assignee=assignee,
            ipc=ipc,
        )
        try:
            data = await _do_search(params)
        except SearchUnavailableError as exc:
            return {
                "ok": False,
                "source": "google_patents_unofficial",
                "kind": "search_unavailable",
                "message": str(exc),
                "hint": (
                    "Try a direct web search from your client (WebSearch / browser). "
                    "Do NOT silently retry this query against jpo_* — the official "
                    "JPO API does not support keyword search."
                ),
            }
        except httpx.HTTPStatusError as exc:
            return {
                "ok": False,
                "source": "google_patents_unofficial",
                "kind": "http_error",
                "status_code": exc.response.status_code,
                "message": str(exc),
            }
        return {
            "ok": True,
            "source": "google_patents_unofficial",
            "input": {
                "query": query,
                "num": params["num"],
                "page": int(params.get("page", "0")),
                "filters": {
                    k: params.get(k, "")
                    for k in ("before", "after", "sort")
                    if params.get(k)
                } | ({"assignee": assignee} if assignee else {})
                  | ({"ipc": ipc} if ipc else {}),
            },
            "data": data,
        }
