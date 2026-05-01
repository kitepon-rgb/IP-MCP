"""JPO 特許情報取得API uses HTTP 200 for everything; the real outcome is in
``result.statusCode`` of the JSON body. This module decodes those codes into
domain-level outcomes the MCP tools can act on.

Reference: https://ip-data.jpo.go.jp/api_guide/api_reference.html
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class JpoOutcome(str, Enum):
    OK = "ok"
    NOT_FOUND = "not_found"
    DAILY_QUOTA_EXCEEDED = "daily_quota_exceeded"
    BAD_PARAMETER = "bad_parameter"
    INVALID_TOKEN = "invalid_token"
    SERVER_BUSY = "server_busy"
    TIMEOUT = "timeout"
    UNKNOWN_ERROR = "unknown_error"


# Documented codes from the official spec.
# Codes not in this map fall through to UNKNOWN_ERROR.
_CODE_MAP: dict[str, JpoOutcome] = {
    "100": JpoOutcome.OK,
    "107": JpoOutcome.NOT_FOUND,
    "203": JpoOutcome.DAILY_QUOTA_EXCEEDED,
    "204": JpoOutcome.BAD_PARAMETER,
    "208": JpoOutcome.BAD_PARAMETER,
    "210": JpoOutcome.INVALID_TOKEN,
    "302": JpoOutcome.TIMEOUT,
    "303": JpoOutcome.SERVER_BUSY,
    "999": JpoOutcome.UNKNOWN_ERROR,
}


@dataclass(frozen=True)
class JpoResultEnvelope:
    outcome: JpoOutcome
    status_code: str
    error_message: str
    remain_access_count: str | None
    data: dict[str, Any]
    raw: dict[str, Any]

    @property
    def is_ok(self) -> bool:
        return self.outcome is JpoOutcome.OK

    @property
    def is_retryable(self) -> bool:
        """True if the same call may succeed shortly (within the same source)."""
        return self.outcome in {JpoOutcome.SERVER_BUSY, JpoOutcome.TIMEOUT}


def parse_envelope(payload: dict[str, Any]) -> JpoResultEnvelope:
    """Decode JPO ``{"result": {"statusCode": ..., "data": ...}}`` envelope."""
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        return JpoResultEnvelope(
            outcome=JpoOutcome.UNKNOWN_ERROR,
            status_code="",
            error_message="response did not contain a 'result' object",
            remain_access_count=None,
            data={},
            raw=payload if isinstance(payload, dict) else {},
        )

    status_code = str(result.get("statusCode", "")).strip()
    error_message = str(result.get("errorMessage", "")).strip()
    remain_raw = result.get("remainAccessCount")
    remain = str(remain_raw).strip() if remain_raw not in (None, "") else None
    data = result.get("data", {}) if isinstance(result.get("data"), dict) else {}

    outcome = _CODE_MAP.get(status_code, JpoOutcome.UNKNOWN_ERROR)
    return JpoResultEnvelope(
        outcome=outcome,
        status_code=status_code,
        error_message=error_message,
        remain_access_count=remain,
        data=data,
        raw=payload,
    )


class JpoApiError(Exception):
    """Raised when the JPO API returns a non-recoverable failure.

    The MCP tool layer catches this and converts it to a structured
    ``{"ok": false, "source": "jpo_official", "kind": ...}`` response —
    it never falls back to an external source automatically.
    """

    def __init__(self, envelope: JpoResultEnvelope, *, endpoint: str = "") -> None:
        super().__init__(
            f"JPO API error ({envelope.outcome.value}, code={envelope.status_code}, "
            f"endpoint={endpoint or '?'}): {envelope.error_message}"
        )
        self.envelope = envelope
        self.endpoint = endpoint
