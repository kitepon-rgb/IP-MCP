"""Structured per-call access log (JSONL).

Every JPO official-API call and every external (Google Patents) call writes
one line here. The format is intentionally machine-readable so that
``scripts/summarize_logs.py`` (or any external dashboard) can aggregate it
into daily quota reports without having to parse human-language log lines.

This is a separate logging channel from the stdlib ``logging`` calls scattered
through the code — those stay free-form for humans tailing the container.
The two layers do not conflict.

Fields (all optional except ts/source/endpoint/elapsed_ms/outcome):

    ts            : ISO 8601 UTC, microsecond precision (e.g. "2026-05-01T16:30:00.123456+00:00")
    source        : "jpo_official" | "google_patents_unofficial"
    endpoint      : URL path (jpo) or tool name (external)
    elapsed_ms    : float, wall-clock duration of the call
    outcome       : envelope outcome string ("ok", "not_found", "rate_limited_daily", ...)
                    or "exception" / "search_unavailable" for non-envelope failures
    status_code   : JPO statusCode (only for jpo_official)
    remain_today  : JPO remainAccessCount (only for jpo_official, may be absent)
    error         : truncated exception message (only when outcome indicates failure)

The file is opened in append-only mode with line buffering. Concurrent writes
from a single async event loop are safe (line-buffered + atomic short writes
on POSIX). Single-process is the only supported deployment.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any, TextIO

log = logging.getLogger(__name__)

DEFAULT_PATH = "/app/logs/access.jsonl"

_lock = Lock()
_handle: TextIO | None = None
_handle_path: str | None = None


def _get_handle() -> TextIO | None:
    """Lazily open the log file. Returns None if opening failed (logging-only fallback)."""
    global _handle, _handle_path
    target_path = os.getenv("ACCESS_LOG_PATH", DEFAULT_PATH).strip() or DEFAULT_PATH
    if _handle is not None and _handle_path == target_path:
        return _handle
    # Path changed (e.g. tests) — close old handle
    if _handle is not None:
        try:
            _handle.close()
        except Exception:
            pass
        _handle = None
        _handle_path = None
    try:
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        _handle = open(target_path, "a", encoding="utf-8", buffering=1)  # line-buffered
        _handle_path = target_path
        log.info("access log: writing to %s", target_path)
        return _handle
    except OSError as exc:
        log.warning("access log disabled (cannot open %s: %s)", target_path, exc)
        return None


def reset_for_tests() -> None:
    """Close the cached handle. Tests call this after changing ACCESS_LOG_PATH."""
    global _handle, _handle_path
    with _lock:
        if _handle is not None:
            try:
                _handle.close()
            except Exception:
                pass
        _handle = None
        _handle_path = None


def log_call(
    *,
    source: str,
    endpoint: str,
    elapsed_ms: float,
    outcome: str,
    status_code: str | None = None,
    remain_today: str | None = None,
    error: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Append one JSONL record. Never raises — logging failure must not break tools."""
    record: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "source": source,
        "endpoint": endpoint,
        "elapsed_ms": round(elapsed_ms, 1),
        "outcome": outcome,
    }
    if status_code:
        record["status_code"] = status_code
    if remain_today is not None and remain_today != "":
        record["remain_today"] = remain_today
    if error:
        # Cap at 200 chars so a runaway exception message can't blow up the log
        record["error"] = error[:200]
    if extra:
        record.update(extra)

    line = json.dumps(record, ensure_ascii=False) + "\n"
    with _lock:
        handle = _get_handle()
        if handle is None:
            return
        try:
            handle.write(line)
        except OSError as exc:
            log.warning("access log write failed: %s", exc)
