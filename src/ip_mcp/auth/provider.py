"""SQLite-backed OAuth 2.1 provider for IP-MCP.

Single-user, single-process. Clients, authorization codes, access tokens, and
refresh tokens persist to a SQLite file so container restarts do not invalidate
already-issued tokens or registered DCR clients.

Schema is created via the stdlib ``sqlite3`` module at construction time
(synchronous, runs once). All async operations use ``aiosqlite`` so the event
loop is not blocked.

Pending consent sessions (10-minute TTL) stay in memory: persisting them would
only matter for "restart-mid-consent" recovery, which is not worth the
complexity — the user can simply re-click "authorize" in the client.

Consent flow (called by the SDK's auto-generated /authorize handler):

  1. SDK calls :meth:`authorize` with ``params`` (PKCE challenge, redirect_uri,
     state, scopes). We mint a one-shot ``session_id``, stash the
     (client, params) tuple, and return the URL of our consent page.
  2. The user's browser hits ``/consent?session_id=...`` (a custom_route on
     the MCP server). The page asks for the master password.
  3. POST returns to :meth:`approve_consent`, which mints an authorization
     code and returns the redirect URL (with ``code`` + ``state``) to send
     the browser back to the client's ``redirect_uri``.
  4. The MCP client exchanges code -> token via the SDK's /token handler,
     which calls :meth:`load_authorization_code` and
     :meth:`exchange_authorization_code`. PKCE verification happens inside
     the SDK using the ``code_challenge`` we stored on the AuthorizationCode.
"""

from __future__ import annotations

import logging
import secrets
import sqlite3
import time
from pathlib import Path

import aiosqlite
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

log = logging.getLogger(__name__)

ACCESS_TOKEN_TTL_SECONDS = 3600              # 1 hour
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 3600   # 30 days
AUTH_CODE_TTL_SECONDS = 600                  # 10 minutes
CONSENT_TTL_SECONDS = 600                    # 10 minutes

_SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    client_id  TEXT PRIMARY KEY,
    data_json  TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_codes (
    code       TEXT PRIMARY KEY,
    client_id  TEXT NOT NULL,
    data_json  TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_codes_expires ON auth_codes(expires_at);

CREATE TABLE IF NOT EXISTS access_tokens (
    token      TEXT PRIMARY KEY,
    client_id  TEXT NOT NULL,
    data_json  TEXT NOT NULL,
    expires_at REAL
);
CREATE INDEX IF NOT EXISTS idx_access_tokens_client ON access_tokens(client_id);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    token      TEXT PRIMARY KEY,
    client_id  TEXT NOT NULL,
    data_json  TEXT NOT NULL,
    expires_at REAL
);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_client ON refresh_tokens(client_id);
"""


class SqliteOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """Personal-use OAuth 2.1 Authorization Server backed by a SQLite file."""

    def __init__(
        self,
        *,
        master_password: str,
        consent_url: str,
        db_path: str | Path,
    ) -> None:
        if not master_password:
            raise ValueError("master_password must be non-empty")
        self._master_password = master_password
        self._consent_url = consent_url
        self._db_path = str(db_path)

        self._pending_consents: dict[
            str, tuple[OAuthClientInformationFull, AuthorizationParams, float]
        ] = {}

        self._init_schema()

    def _init_schema(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.executescript(_SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        log.info("OAuth store: SQLite at %s", self._db_path)

    # ---------------- DCR ----------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                "SELECT data_json FROM clients WHERE client_id = ?",
                (client_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            return OAuthClientInformationFull.model_validate_json(row[0])

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            raise ValueError("SDK should have assigned client_id")
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO clients (client_id, data_json, created_at) "
                "VALUES (?, ?, ?)",
                (
                    client_info.client_id,
                    client_info.model_dump_json(),
                    time.time(),
                ),
            )
            await conn.commit()
        log.info(
            "DCR: registered client_id=%s name=%r",
            client_info.client_id,
            client_info.client_name,
        )

    # ---------------- /authorize ----------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        self._gc_consents()
        session_id = secrets.token_urlsafe(32)
        self._pending_consents[session_id] = (
            client,
            params,
            time.time() + CONSENT_TTL_SECONDS,
        )
        return f"{self._consent_url}?session_id={session_id}"

    # ---------------- consent helpers (called from custom HTTP handlers) ----------------

    def get_pending_consent(
        self, session_id: str
    ) -> tuple[OAuthClientInformationFull, AuthorizationParams] | None:
        self._gc_consents()
        entry = self._pending_consents.get(session_id)
        if entry is None:
            return None
        client, params, expires_at = entry
        if expires_at < time.time():
            self._pending_consents.pop(session_id, None)
            return None
        return client, params

    def approve_consent(self, session_id: str, password: str) -> str | None:
        """Verify master password, mint auth code, return redirect URL.

        Returns ``None`` if the password is wrong or the session expired.

        Synchronous: called from a Starlette form handler that does not await
        it. The DB write here is a once-per-grant blocking call (microseconds),
        so we use stdlib ``sqlite3`` rather than dragging in async machinery.
        """
        if not secrets.compare_digest(
            password.encode(), self._master_password.encode()
        ):
            log.warning("consent rejected: wrong password (session=%s...)", session_id[:8])
            return None
        consent = self._pending_consents.pop(session_id, None)
        if consent is None:
            log.warning("consent rejected: unknown session %s...", session_id[:8])
            return None
        client, params, _ = consent
        code = secrets.token_urlsafe(32)
        auth_code = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=time.time() + AUTH_CODE_TTL_SECONDS,
            client_id=client.client_id or "",
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO auth_codes (code, client_id, data_json, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    code,
                    client.client_id or "",
                    auth_code.model_dump_json(),
                    auth_code.expires_at,
                ),
            )
            conn.commit()
        log.info("consent approved: client_id=%s code=%s...", client.client_id, code[:8])
        return construct_redirect_uri(
            str(params.redirect_uri), code=code, state=params.state
        )

    # ---------------- /token: code exchange ----------------

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                "SELECT data_json, expires_at, client_id FROM auth_codes WHERE code = ?",
                (authorization_code,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            data_json, expires_at, code_client_id = row
            if code_client_id != client.client_id:
                return None
            if expires_at < time.time():
                await conn.execute(
                    "DELETE FROM auth_codes WHERE code = ?", (authorization_code,)
                )
                await conn.commit()
                return None
            return AuthorizationCode.model_validate_json(data_json)

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        token_str = secrets.token_urlsafe(48)
        refresh_str = secrets.token_urlsafe(48)
        now = int(time.time())
        access = AccessToken(
            token=token_str,
            client_id=client.client_id or "",
            scopes=authorization_code.scopes,
            expires_at=now + ACCESS_TOKEN_TTL_SECONDS,
            resource=authorization_code.resource,
        )
        refresh = RefreshToken(
            token=refresh_str,
            client_id=client.client_id or "",
            scopes=authorization_code.scopes,
            expires_at=now + REFRESH_TOKEN_TTL_SECONDS,
        )
        async with aiosqlite.connect(self._db_path) as conn:
            # Single-use: drop the code so it cannot be replayed
            await conn.execute(
                "DELETE FROM auth_codes WHERE code = ?", (authorization_code.code,)
            )
            await conn.execute(
                "INSERT INTO access_tokens (token, client_id, data_json, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (token_str, access.client_id, access.model_dump_json(), access.expires_at),
            )
            await conn.execute(
                "INSERT INTO refresh_tokens (token, client_id, data_json, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    refresh_str,
                    refresh.client_id,
                    refresh.model_dump_json(),
                    refresh.expires_at,
                ),
            )
            await conn.commit()
        return OAuthToken(
            access_token=token_str,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            refresh_token=refresh_str,
            scope=" ".join(authorization_code.scopes) if authorization_code.scopes else None,
        )

    # ---------------- /token: refresh ----------------

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                "SELECT data_json, expires_at, client_id FROM refresh_tokens WHERE token = ?",
                (refresh_token,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            data_json, expires_at, rt_client_id = row
            if rt_client_id != client.client_id:
                return None
            if expires_at is not None and expires_at < time.time():
                await conn.execute(
                    "DELETE FROM refresh_tokens WHERE token = ?", (refresh_token,)
                )
                await conn.commit()
                return None
            return RefreshToken.model_validate_json(data_json)

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        token_str = secrets.token_urlsafe(48)
        new_refresh = secrets.token_urlsafe(48)
        now = int(time.time())
        actual_scopes = scopes or refresh_token.scopes
        access = AccessToken(
            token=token_str,
            client_id=client.client_id or "",
            scopes=actual_scopes,
            expires_at=now + ACCESS_TOKEN_TTL_SECONDS,
        )
        new_rt = RefreshToken(
            token=new_refresh,
            client_id=client.client_id or "",
            scopes=actual_scopes,
            expires_at=now + REFRESH_TOKEN_TTL_SECONDS,
        )
        async with aiosqlite.connect(self._db_path) as conn:
            # Rotate: invalidate old refresh, issue fresh pair
            await conn.execute(
                "DELETE FROM refresh_tokens WHERE token = ?", (refresh_token.token,)
            )
            await conn.execute(
                "INSERT INTO access_tokens (token, client_id, data_json, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (token_str, access.client_id, access.model_dump_json(), access.expires_at),
            )
            await conn.execute(
                "INSERT INTO refresh_tokens (token, client_id, data_json, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (new_refresh, new_rt.client_id, new_rt.model_dump_json(), new_rt.expires_at),
            )
            await conn.commit()
        return OAuthToken(
            access_token=token_str,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            refresh_token=new_refresh,
            scope=" ".join(actual_scopes) if actual_scopes else None,
        )

    # ---------------- token verification (per-request) ----------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                "SELECT data_json, expires_at FROM access_tokens WHERE token = ?",
                (token,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            data_json, expires_at = row
            if expires_at is not None and expires_at < time.time():
                await conn.execute(
                    "DELETE FROM access_tokens WHERE token = ?", (token,)
                )
                await conn.commit()
                return None
            return AccessToken.model_validate_json(data_json)

    # ---------------- /revoke ----------------

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "DELETE FROM access_tokens WHERE token = ?", (token.token,)
            )
            await conn.execute(
                "DELETE FROM refresh_tokens WHERE token = ?", (token.token,)
            )
            await conn.commit()

    # ---------------- housekeeping ----------------

    def _gc_consents(self) -> None:
        now = time.time()
        for sid, (_, _, exp) in list(self._pending_consents.items()):
            if exp < now:
                self._pending_consents.pop(sid, None)
