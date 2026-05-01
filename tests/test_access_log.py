"""Tests for the JSONL access log."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from ip_mcp import access_log


@pytest.fixture(autouse=True)
def _isolated_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log_path = tmp_path / "access.jsonl"
    monkeypatch.setenv("ACCESS_LOG_PATH", str(log_path))
    access_log.reset_for_tests()
    yield log_path
    access_log.reset_for_tests()


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_log_call_writes_minimum_fields(_isolated_log: Path) -> None:
    access_log.log_call(
        source="jpo_official",
        endpoint="/api/patent/v1/case_number_reference/application/2017204947",
        elapsed_ms=234.5,
        outcome="ok",
    )
    records = _read(_isolated_log)
    assert len(records) == 1
    rec = records[0]
    assert rec["source"] == "jpo_official"
    assert rec["endpoint"].startswith("/api/patent/v1/")
    assert rec["elapsed_ms"] == 234.5
    assert rec["outcome"] == "ok"
    datetime.fromisoformat(rec["ts"])


def test_log_call_optional_fields(_isolated_log: Path) -> None:
    access_log.log_call(
        source="jpo_official",
        endpoint="/api/patent/v1/foo",
        elapsed_ms=10.0,
        outcome="ok",
        status_code="100",
        remain_today="998",
    )
    rec = _read(_isolated_log)[0]
    assert rec["status_code"] == "100"
    assert rec["remain_today"] == "998"


def test_log_call_omits_empty_optional_fields(_isolated_log: Path) -> None:
    access_log.log_call(
        source="google_patents_unofficial",
        endpoint="external_search_patents_by_keyword",
        elapsed_ms=1234.5,
        outcome="ok",
    )
    rec = _read(_isolated_log)[0]
    assert "status_code" not in rec
    assert "remain_today" not in rec
    assert "error" not in rec


def test_log_call_truncates_long_error(_isolated_log: Path) -> None:
    long_error = "x" * 500
    access_log.log_call(
        source="jpo_official",
        endpoint="/api/patent/v1/foo",
        elapsed_ms=10.0,
        outcome="exception",
        error=long_error,
    )
    rec = _read(_isolated_log)[0]
    assert len(rec["error"]) == 200


def test_log_call_appends_multiple_lines(_isolated_log: Path) -> None:
    for i in range(5):
        access_log.log_call(
            source="jpo_official",
            endpoint=f"/api/patent/v1/x/{i}",
            elapsed_ms=float(i),
            outcome="ok",
        )
    records = _read(_isolated_log)
    assert len(records) == 5
    assert [r["endpoint"] for r in records] == [f"/api/patent/v1/x/{i}" for i in range(5)]


def test_log_call_does_not_raise_on_unwriteable_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the log file cannot be opened, log_call must swallow the error
    so that a logging failure never breaks a tool call."""
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    bad_path = blocker / "child" / "access.jsonl"
    monkeypatch.setenv("ACCESS_LOG_PATH", str(bad_path))
    access_log.reset_for_tests()

    access_log.log_call(
        source="jpo_official",
        endpoint="/x",
        elapsed_ms=0.0,
        outcome="ok",
    )


def test_log_call_records_japanese_endpoint_correctly(_isolated_log: Path) -> None:
    access_log.log_call(
        source="jpo_official",
        endpoint="/api/patent/v1/applicant_attorney/トヨタ自動車",
        elapsed_ms=15.0,
        outcome="ok",
    )
    rec = _read(_isolated_log)[0]
    assert rec["endpoint"] == "/api/patent/v1/applicant_attorney/トヨタ自動車"


def test_extra_fields_are_merged(_isolated_log: Path) -> None:
    access_log.log_call(
        source="jpo_official",
        endpoint="/api/patent/v1/foo",
        elapsed_ms=10.0,
        outcome="ok",
        extra={"client_id": "test-client", "trace_id": "abc123"},
    )
    rec = _read(_isolated_log)[0]
    assert rec["client_id"] == "test-client"
    assert rec["trace_id"] == "abc123"
