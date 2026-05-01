"""Shared helpers for tools_official/*. Do NOT import from tools_external/."""

from __future__ import annotations

from typing import Any

from ..jpo.status_codes import JpoOutcome, JpoResultEnvelope

_KIND_MAP: dict[JpoOutcome, str] = {
    JpoOutcome.NOT_FOUND: "not_found",
    JpoOutcome.DAILY_QUOTA_EXCEEDED: "rate_limited_daily",
    JpoOutcome.BAD_PARAMETER: "bad_parameter",
    JpoOutcome.INVALID_TOKEN: "auth_failed",
    JpoOutcome.SERVER_BUSY: "transient",
    JpoOutcome.TIMEOUT: "transient",
    JpoOutcome.UNKNOWN_ERROR: "unknown_error",
}


def envelope_error(envelope: JpoResultEnvelope, endpoint: str) -> dict[str, Any]:
    """Convert a non-OK envelope to a structured MCP error payload.

    Always tagged ``source: "jpo_official"``. Never falls back to another source.
    """
    return {
        "ok": False,
        "source": "jpo_official",
        "kind": _KIND_MAP.get(envelope.outcome, "unknown_error"),
        "status_code": envelope.status_code,
        "message": envelope.error_message or "(no message)",
        "endpoint": endpoint,
        "remaining_today": envelope.remain_access_count,
    }


def bad_input(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "source": "jpo_official",
        "kind": "bad_input",
        "message": message,
    }
