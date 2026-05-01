"""Unit tests for SqliteOAuthProvider.

Each test gets its own tmp_path so the SQLite file is isolated.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from mcp.server.auth.provider import AuthorizationCode, AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from ip_mcp.auth.provider import (
    ACCESS_TOKEN_TTL_SECONDS,
    REFRESH_TOKEN_TTL_SECONDS,
    SqliteOAuthProvider,
)


def _make_client(client_id: str = "test-client") -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        redirect_uris=[AnyUrl("https://client.example.com/cb")],
        client_name="Test Client",
    )


def _make_params(
    redirect_uri: str = "https://client.example.com/cb",
    scopes: list[str] | None = None,
    state: str = "xyz",
    code_challenge: str = "a" * 43,
) -> AuthorizationParams:
    return AuthorizationParams(
        state=state,
        scopes=scopes,
        code_challenge=code_challenge,
        redirect_uri=AnyUrl(redirect_uri),
        redirect_uri_provided_explicitly=True,
        resource=None,
    )


@pytest.fixture
def provider(tmp_path: Path) -> SqliteOAuthProvider:
    return SqliteOAuthProvider(
        master_password="hunter2",
        consent_url="https://server.example.com/consent",
        db_path=tmp_path / "oauth.db",
    )


def test_db_file_created_with_parent_dir(tmp_path: Path) -> None:
    db = tmp_path / "nested" / "deep" / "oauth.db"
    SqliteOAuthProvider(
        master_password="x",
        consent_url="https://e/c",
        db_path=db,
    )
    assert db.exists()


def test_master_password_required(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="master_password"):
        SqliteOAuthProvider(
            master_password="",
            consent_url="https://e/c",
            db_path=tmp_path / "oauth.db",
        )


async def test_register_and_get_client(provider: SqliteOAuthProvider) -> None:
    client = _make_client("abc")
    await provider.register_client(client)

    loaded = await provider.get_client("abc")
    assert loaded is not None
    assert loaded.client_id == "abc"
    assert loaded.client_name == "Test Client"


async def test_get_unknown_client_returns_none(provider: SqliteOAuthProvider) -> None:
    assert await provider.get_client("does-not-exist") is None


async def test_register_client_without_id_rejected(provider: SqliteOAuthProvider) -> None:
    bad = OAuthClientInformationFull(
        client_id=None,
        redirect_uris=[AnyUrl("https://e/c")],
    )
    with pytest.raises(ValueError):
        await provider.register_client(bad)


async def test_persistence_across_instances(tmp_path: Path) -> None:
    """A new provider pointing at the same DB sees previously-registered clients."""
    db = tmp_path / "oauth.db"
    p1 = SqliteOAuthProvider(
        master_password="x", consent_url="https://e/c", db_path=db
    )
    await p1.register_client(_make_client("persisted"))

    p2 = SqliteOAuthProvider(
        master_password="x", consent_url="https://e/c", db_path=db
    )
    loaded = await p2.get_client("persisted")
    assert loaded is not None
    assert loaded.client_id == "persisted"


async def test_authorize_returns_consent_url(provider: SqliteOAuthProvider) -> None:
    client = _make_client()
    consent_url = await provider.authorize(client, _make_params())
    assert consent_url.startswith("https://server.example.com/consent?session_id=")


async def test_pending_consent_lookup(provider: SqliteOAuthProvider) -> None:
    client = _make_client()
    consent_url = await provider.authorize(client, _make_params())
    session_id = consent_url.split("session_id=")[1]

    pending = provider.get_pending_consent(session_id)
    assert pending is not None
    pending_client, _params = pending
    assert pending_client.client_id == client.client_id


async def test_full_auth_code_flow(provider: SqliteOAuthProvider) -> None:
    client = _make_client()
    await provider.register_client(client)

    consent_url = await provider.authorize(client, _make_params())
    session_id = consent_url.split("session_id=")[1]

    redirect = provider.approve_consent(session_id, "hunter2")
    assert redirect is not None
    code = redirect.split("code=")[1].split("&")[0]

    loaded = await provider.load_authorization_code(client, code)
    assert loaded is not None
    assert loaded.client_id == client.client_id

    token = await provider.exchange_authorization_code(client, loaded)
    assert token.access_token
    assert token.refresh_token
    assert token.expires_in == ACCESS_TOKEN_TTL_SECONDS

    # Code should be single-use
    assert await provider.load_authorization_code(client, code) is None


async def test_wrong_password_rejected(provider: SqliteOAuthProvider) -> None:
    client = _make_client()
    consent_url = await provider.authorize(client, _make_params())
    session_id = consent_url.split("session_id=")[1]

    assert provider.approve_consent(session_id, "wrong-password") is None
    # Session must NOT be consumed on wrong password — user can retry
    assert provider.get_pending_consent(session_id) is not None


async def test_unknown_session_rejected(provider: SqliteOAuthProvider) -> None:
    assert provider.approve_consent("never-existed", "hunter2") is None


async def test_expired_auth_code_returns_none(provider: SqliteOAuthProvider) -> None:
    """Manually insert an expired auth_code row and confirm it's purged on read."""
    import sqlite3

    client = _make_client()
    expired = AuthorizationCode(
        code="expired-code",
        scopes=[],
        expires_at=time.time() - 1,
        client_id=client.client_id or "",
        code_challenge="a" * 43,
        redirect_uri=AnyUrl("https://e/c"),
        redirect_uri_provided_explicitly=True,
    )
    with sqlite3.connect(provider._db_path) as conn:
        conn.execute(
            "INSERT INTO auth_codes (code, client_id, data_json, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (
                expired.code,
                expired.client_id,
                expired.model_dump_json(),
                expired.expires_at,
            ),
        )
        conn.commit()

    assert await provider.load_authorization_code(client, "expired-code") is None


async def test_auth_code_wrong_client_rejected(provider: SqliteOAuthProvider) -> None:
    """An auth code issued to client A must not load for client B."""
    client_a = _make_client("client-a")
    client_b = _make_client("client-b")

    consent_url = await provider.authorize(client_a, _make_params())
    session_id = consent_url.split("session_id=")[1]
    redirect = provider.approve_consent(session_id, "hunter2")
    assert redirect is not None
    code = redirect.split("code=")[1].split("&")[0]

    assert await provider.load_authorization_code(client_b, code) is None
    # Still loadable by the original client
    assert await provider.load_authorization_code(client_a, code) is not None


async def test_refresh_token_rotation(provider: SqliteOAuthProvider) -> None:
    """exchange_refresh_token rotates: old refresh becomes invalid, new one works."""
    client = _make_client()
    await provider.register_client(client)

    consent_url = await provider.authorize(client, _make_params())
    session_id = consent_url.split("session_id=")[1]
    redirect = provider.approve_consent(session_id, "hunter2")
    assert redirect is not None
    code = redirect.split("code=")[1].split("&")[0]

    auth_code = await provider.load_authorization_code(client, code)
    assert auth_code is not None
    token1 = await provider.exchange_authorization_code(client, auth_code)
    assert token1.refresh_token is not None

    refresh1 = await provider.load_refresh_token(client, token1.refresh_token)
    assert refresh1 is not None

    token2 = await provider.exchange_refresh_token(client, refresh1, scopes=[])
    assert token2.access_token != token1.access_token
    assert token2.refresh_token != token1.refresh_token

    # Old refresh token must be invalid
    assert await provider.load_refresh_token(client, token1.refresh_token) is None
    # New refresh token works
    assert await provider.load_refresh_token(client, token2.refresh_token) is not None


async def test_load_access_token(provider: SqliteOAuthProvider) -> None:
    client = _make_client()
    await provider.register_client(client)

    consent_url = await provider.authorize(client, _make_params())
    session_id = consent_url.split("session_id=")[1]
    redirect = provider.approve_consent(session_id, "hunter2")
    assert redirect is not None
    code = redirect.split("code=")[1].split("&")[0]
    auth_code = await provider.load_authorization_code(client, code)
    assert auth_code is not None
    token = await provider.exchange_authorization_code(client, auth_code)

    loaded = await provider.load_access_token(token.access_token)
    assert loaded is not None
    assert loaded.client_id == client.client_id
    assert loaded.expires_at is not None
    assert loaded.expires_at > time.time()


async def test_revoke_clears_token(provider: SqliteOAuthProvider) -> None:
    client = _make_client()
    await provider.register_client(client)

    consent_url = await provider.authorize(client, _make_params())
    session_id = consent_url.split("session_id=")[1]
    redirect = provider.approve_consent(session_id, "hunter2")
    assert redirect is not None
    code = redirect.split("code=")[1].split("&")[0]
    auth_code = await provider.load_authorization_code(client, code)
    assert auth_code is not None
    token = await provider.exchange_authorization_code(client, auth_code)

    access = await provider.load_access_token(token.access_token)
    assert access is not None

    await provider.revoke_token(access)
    assert await provider.load_access_token(token.access_token) is None


async def test_token_persistence_across_restart(tmp_path: Path) -> None:
    """A token issued by p1 must still be loadable by p2 after restart."""
    db = tmp_path / "oauth.db"

    p1 = SqliteOAuthProvider(
        master_password="hunter2",
        consent_url="https://e/c",
        db_path=db,
    )
    client = _make_client()
    await p1.register_client(client)

    consent_url = await p1.authorize(client, _make_params())
    session_id = consent_url.split("session_id=")[1]
    redirect = p1.approve_consent(session_id, "hunter2")
    assert redirect is not None
    code = redirect.split("code=")[1].split("&")[0]
    auth_code = await p1.load_authorization_code(client, code)
    assert auth_code is not None
    token = await p1.exchange_authorization_code(client, auth_code)

    # Simulate container restart
    p2 = SqliteOAuthProvider(
        master_password="hunter2",
        consent_url="https://e/c",
        db_path=db,
    )

    # Client survives
    assert await p2.get_client(client.client_id or "") is not None
    # Access token survives
    loaded_access = await p2.load_access_token(token.access_token)
    assert loaded_access is not None
    # Refresh token survives
    assert token.refresh_token is not None
    loaded_refresh = await p2.load_refresh_token(client, token.refresh_token)
    assert loaded_refresh is not None
    assert loaded_refresh.expires_at is not None
    assert loaded_refresh.expires_at > time.time() + REFRESH_TOKEN_TTL_SECONDS - 60
