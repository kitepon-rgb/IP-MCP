"""In-memory OAuth 2.1 provider for IP-MCP.

Single-user, single-process. Clients/codes/tokens live in dicts and are lost on
restart. This is intentional for personal-use deployments — swap in a
SQLite-backed store later if multi-user or restart-stable sessions are needed.

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
import time

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


class InMemoryOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """Personal-use, in-memory OAuth 2.1 Authorization Server."""

    def __init__(self, *, master_password: str, consent_url: str) -> None:
        if not master_password:
            raise ValueError("master_password must be non-empty")
        self._master_password = master_password
        self._consent_url = consent_url
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._codes: dict[str, AuthorizationCode] = {}
        self._access_tokens: dict[str, AccessToken] = {}
        self._refresh_tokens: dict[str, RefreshToken] = {}
        self._pending_consents: dict[
            str, tuple[OAuthClientInformationFull, AuthorizationParams, float]
        ] = {}

    # ---------------- DCR ----------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            raise ValueError("SDK should have assigned client_id")
        self._clients[client_info.client_id] = client_info
        log.info(
            "DCR: registered client_id=%s name=%r",
            client_info.client_id,
            client_info.client_name,
        )

    # ---------------- /authorize ----------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        self._gc()
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
        self._gc()
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
        """
        if not secrets.compare_digest(password.encode(), self._master_password.encode()):
            log.warning("consent rejected: wrong password (session=%s...)", session_id[:8])
            return None
        consent = self._pending_consents.pop(session_id, None)
        if consent is None:
            log.warning("consent rejected: unknown session %s...", session_id[:8])
            return None
        client, params, _ = consent
        code = secrets.token_urlsafe(32)
        self._codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=time.time() + AUTH_CODE_TTL_SECONDS,
            client_id=client.client_id or "",
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        log.info("consent approved: client_id=%s code=%s...", client.client_id, code[:8])
        return construct_redirect_uri(
            str(params.redirect_uri), code=code, state=params.state
        )

    # ---------------- /token: code exchange ----------------

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = self._codes.get(authorization_code)
        if code is None or code.client_id != client.client_id:
            return None
        if code.expires_at < time.time():
            self._codes.pop(authorization_code, None)
            return None
        return code

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        # Single-use: drop the code so it cannot be replayed
        self._codes.pop(authorization_code.code, None)
        token_str = secrets.token_urlsafe(48)
        refresh_str = secrets.token_urlsafe(48)
        now = int(time.time())
        self._access_tokens[token_str] = AccessToken(
            token=token_str,
            client_id=client.client_id or "",
            scopes=authorization_code.scopes,
            expires_at=now + ACCESS_TOKEN_TTL_SECONDS,
            resource=authorization_code.resource,
        )
        self._refresh_tokens[refresh_str] = RefreshToken(
            token=refresh_str,
            client_id=client.client_id or "",
            scopes=authorization_code.scopes,
            expires_at=now + REFRESH_TOKEN_TTL_SECONDS,
        )
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
        rt = self._refresh_tokens.get(refresh_token)
        if rt is None or rt.client_id != client.client_id:
            return None
        if rt.expires_at is not None and rt.expires_at < time.time():
            self._refresh_tokens.pop(refresh_token, None)
            return None
        return rt

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # Rotate: invalidate old refresh, issue fresh pair
        self._refresh_tokens.pop(refresh_token.token, None)
        token_str = secrets.token_urlsafe(48)
        new_refresh = secrets.token_urlsafe(48)
        now = int(time.time())
        actual_scopes = scopes or refresh_token.scopes
        self._access_tokens[token_str] = AccessToken(
            token=token_str,
            client_id=client.client_id or "",
            scopes=actual_scopes,
            expires_at=now + ACCESS_TOKEN_TTL_SECONDS,
        )
        self._refresh_tokens[new_refresh] = RefreshToken(
            token=new_refresh,
            client_id=client.client_id or "",
            scopes=actual_scopes,
            expires_at=now + REFRESH_TOKEN_TTL_SECONDS,
        )
        return OAuthToken(
            access_token=token_str,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            refresh_token=new_refresh,
            scope=" ".join(actual_scopes) if actual_scopes else None,
        )

    # ---------------- token verification (per-request) ----------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        at = self._access_tokens.get(token)
        if at is None:
            return None
        if at.expires_at is not None and at.expires_at < time.time():
            self._access_tokens.pop(token, None)
            return None
        return at

    # ---------------- /revoke ----------------

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self._access_tokens.pop(token.token, None)
        self._refresh_tokens.pop(token.token, None)

    # ---------------- housekeeping ----------------

    def _gc(self) -> None:
        now = time.time()
        for sid, (_, _, exp) in list(self._pending_consents.items()):
            if exp < now:
                self._pending_consents.pop(sid, None)
        for code_str, code in list(self._codes.items()):
            if code.expires_at < now:
                self._codes.pop(code_str, None)
