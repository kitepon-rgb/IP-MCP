"""Unit tests for JpoRawResponse — the dual binary/JSON wrapper used by
``app_doc_cont_*`` endpoints."""

from __future__ import annotations

import json

import pytest

from ip_mcp.jpo.client import JpoRawResponse
from ip_mcp.jpo.status_codes import JpoOutcome

# ---- is_binary detection ----------------------------------------------


def test_zip_content_type_is_binary() -> None:
    raw = JpoRawResponse(
        http_status=200,
        content_type="application/zip",
        content=b"PK\x03\x04anything",
    )
    assert raw.is_binary is True


def test_octet_stream_is_binary() -> None:
    raw = JpoRawResponse(
        http_status=200,
        content_type="application/octet-stream",
        content=b"some bytes",
    )
    assert raw.is_binary is True


def test_pdf_content_type_is_binary() -> None:
    raw = JpoRawResponse(
        http_status=200,
        content_type="application/pdf",
        content=b"%PDF-1.4...",
    )
    assert raw.is_binary is True


def test_zip_magic_without_content_type_is_binary() -> None:
    """Even when the server omits Content-Type, the PK magic is conclusive."""
    raw = JpoRawResponse(
        http_status=200,
        content_type="",
        content=b"PK\x03\x04rest of zip",
    )
    assert raw.is_binary is True


def test_json_response_is_not_binary() -> None:
    raw = JpoRawResponse(
        http_status=200,
        content_type="application/json; charset=utf-8",
        content=b'{"result":{"statusCode":"100"}}',
    )
    assert raw.is_binary is False


def test_text_response_with_no_magic_is_not_binary() -> None:
    raw = JpoRawResponse(
        http_status=200,
        content_type="text/plain",
        content=b"some plain text body",
    )
    assert raw.is_binary is False


# ---- envelope() parsing ----------------------------------------------


def test_envelope_parses_json_body() -> None:
    body = {
        "result": {
            "statusCode": "100",
            "errorMessage": "",
            "remainAccessCount": "42",
            "data": {"URL": "https://example.com/signed.zip"},
        }
    }
    raw = JpoRawResponse(
        http_status=200,
        content_type="application/json",
        content=json.dumps(body).encode("utf-8"),
    )
    env = raw.envelope()
    assert env.outcome is JpoOutcome.OK
    assert env.remain_access_count == "42"
    assert env.data == {"URL": "https://example.com/signed.zip"}


def test_envelope_handles_empty_body() -> None:
    raw = JpoRawResponse(http_status=200, content_type="application/json", content=b"")
    env = raw.envelope()
    assert env.outcome is JpoOutcome.UNKNOWN_ERROR


def test_envelope_raises_on_invalid_json() -> None:
    raw = JpoRawResponse(
        http_status=200, content_type="application/json", content=b"not json{"
    )
    with pytest.raises(json.JSONDecodeError):
        raw.envelope()


def test_envelope_propagates_status_code_210() -> None:
    body = {"result": {"statusCode": "210", "errorMessage": "invalid token"}}
    raw = JpoRawResponse(
        http_status=200,
        content_type="application/json",
        content=json.dumps(body).encode("utf-8"),
    )
    env = raw.envelope()
    assert env.outcome is JpoOutcome.INVALID_TOKEN
    assert env.status_code == "210"
