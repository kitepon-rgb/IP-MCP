"""Async HTTP client for the JPO 特許情報取得API.

- OAuth2 Resource Owner Password Grant (token TTL 1h, refresh TTL 8h)
- Auto token refresh on 401 / statusCode 210 (one retry, same source)
- Exponential backoff on statusCode 303 (server busy, same source)
- NO automatic fallback to other data sources — see CLAUDE.md
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass

import httpx

from ..access_log import log_call
from .rate_limiter import RateLimiter, domestic_limiter, opd_limiter
from .status_codes import JpoOutcome, JpoResultEnvelope, parse_envelope

log = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://ip-data.jpo.go.jp"
DEFAULT_AUTH_URL = "https://ip-data.jpo.go.jp/auth/token"

_BUSY_RETRY_DELAYS = (1.0, 3.0, 9.0)  # seconds, total ≈ 13s


@dataclass(frozen=True)
class JpoRawResponse:
    """Raw HTTP response for endpoints that may return binary OR JSON.

    Used for ``app_doc_cont_*`` endpoints which embed small documents inline
    as ZIP bytes but return a JSON envelope (with a signed download URL) for
    larger ones.
    """

    http_status: int
    content_type: str
    content: bytes

    @property
    def is_binary(self) -> bool:
        ct = self.content_type.lower()
        if "json" in ct:
            return False
        if "zip" in ct or "octet-stream" in ct or "pdf" in ct:
            return True
        # Magic-byte fallback: ZIP starts with PK\x03\x04, PDF with %PDF
        head = self.content[:4]
        return head.startswith(b"PK") or head == b"%PDF"

    def envelope(self) -> JpoResultEnvelope:
        """Parse as JSON envelope. Caller must verify ``is_binary == False`` first."""
        return parse_envelope(json.loads(self.content.decode("utf-8")) if self.content else {})


@dataclass
class JpoConfig:
    username: str = ""
    password: str = ""
    pre_issued_token: str = ""
    api_base: str = DEFAULT_API_BASE
    auth_url: str = DEFAULT_AUTH_URL
    request_timeout: float = 30.0

    @classmethod
    def from_env(cls) -> "JpoConfig":
        return cls(
            username=os.getenv("JPO_USERNAME", "").strip(),
            password=os.getenv("JPO_PASSWORD", "").strip(),
            pre_issued_token=os.getenv("JPO_TOKEN", "").strip(),
            api_base=os.getenv("JPO_API_BASE", DEFAULT_API_BASE).rstrip("/"),
            auth_url=os.getenv("JPO_AUTH_TOKEN_URL", DEFAULT_AUTH_URL),
        )

    @property
    def has_credentials(self) -> bool:
        return bool(self.pre_issued_token or (self.username and self.password))


class JpoClient:
    """Reusable async client. One instance per process is enough."""

    def __init__(
        self,
        config: JpoConfig | None = None,
        *,
        domestic: RateLimiter | None = None,
        opd: RateLimiter | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config or JpoConfig.from_env()
        self._domestic_limiter = domestic or domestic_limiter()
        self._opd_limiter = opd or opd_limiter()
        self._http = http_client or httpx.AsyncClient(timeout=self.config.request_timeout)
        self._token: str = self.config.pre_issued_token
        self._token_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "JpoClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # ---- Auth ----------------------------------------------------------

    async def _refresh_token(self, *, force: bool = False) -> str:
        async with self._token_lock:
            if self._token and not force:
                return self._token
            if not self.config.username or not self.config.password:
                if self._token:
                    return self._token
                raise RuntimeError(
                    "JPO_USERNAME/JPO_PASSWORD or JPO_TOKEN must be set in environment"
                )
            log.info("requesting new JPO access token (password grant)")
            response = await self._http.post(
                self.config.auth_url,
                data={
                    "grant_type": "password",
                    "username": self.config.username,
                    "password": self.config.password,
                },
            )
            response.raise_for_status()
            payload = response.json()
            token = str(payload.get("access_token", "")).strip()
            if not token:
                raise RuntimeError(f"JPO auth returned no access_token: {payload!r}")
            self._token = token
            return token

    # ---- GET (JSON) ----------------------------------------------------

    async def get_json(self, path: str, *, opd: bool = False) -> JpoResultEnvelope:
        """GET ``path`` (relative to api_base) and return a parsed envelope.

        Retries inside the same source only:
          - statusCode 210 (invalid token) → re-acquire token, one retry
          - statusCode 303 (server busy)   → exponential backoff, up to 3 tries
        Other failures bubble up as JpoResultEnvelope (caller decides).

        Every terminal call (success or final failure) writes one line to the
        access log so daily quota consumption can be summarized externally.
        """
        started = time.perf_counter()
        try:
            envelope = await self._get_json_inner(path, opd=opd)
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            log_call(
                source="jpo_official",
                endpoint=path,
                elapsed_ms=elapsed_ms,
                outcome="exception",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        log_call(
            source="jpo_official",
            endpoint=path,
            elapsed_ms=elapsed_ms,
            outcome=envelope.outcome.value,
            status_code=envelope.status_code or None,
            remain_today=envelope.remain_access_count,
        )
        return envelope

    async def _get_json_inner(self, path: str, *, opd: bool) -> JpoResultEnvelope:
        limiter = self._opd_limiter if opd else self._domestic_limiter
        url = self._build_url(path)

        for attempt, delay in enumerate(_BUSY_RETRY_DELAYS, start=1):
            await limiter.acquire()
            envelope = await self._do_get(url, refresh_on_invalid_token=True)
            if envelope.outcome is not JpoOutcome.SERVER_BUSY:
                return envelope
            log.warning(
                "JPO server busy (303), backing off %.1fs (attempt %d/%d) url=%s",
                delay, attempt, len(_BUSY_RETRY_DELAYS), url,
            )
            await asyncio.sleep(delay)

        await limiter.acquire()
        return await self._do_get(url, refresh_on_invalid_token=True)

    # ---- GET (raw bytes, for binary-or-JSON endpoints) ----------------

    async def get_raw(self, path: str, *, opd: bool = False) -> JpoRawResponse:
        """Like :meth:`get_json`, but returns raw bytes + content-type so the
        caller can handle endpoints that may embed binary documents (ZIP/PDF)
        inline OR return a JSON envelope with a signed download URL.

        Same retry semantics: 401 → token refresh, JSON ``statusCode 303`` →
        exponential backoff. Binary responses pass through immediately.
        """
        started = time.perf_counter()
        try:
            raw = await self._get_raw_inner(path, opd=opd)
        except Exception as exc:
            log_call(
                source="jpo_official",
                endpoint=path,
                elapsed_ms=(time.perf_counter() - started) * 1000,
                outcome="exception",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        # Best-effort outcome derivation for the access log
        outcome = "ok"
        status_code: str | None = None
        remain: str | None = None
        if not raw.is_binary:
            try:
                env = raw.envelope()
                outcome = env.outcome.value
                status_code = env.status_code or None
                remain = env.remain_access_count
            except Exception:
                outcome = "non_envelope_text"
        log_call(
            source="jpo_official",
            endpoint=path,
            elapsed_ms=elapsed_ms,
            outcome=outcome,
            status_code=status_code,
            remain_today=remain,
        )
        return raw

    async def _get_raw_inner(self, path: str, *, opd: bool) -> JpoRawResponse:
        limiter = self._opd_limiter if opd else self._domestic_limiter
        url = self._build_url(path)

        for attempt, delay in enumerate(_BUSY_RETRY_DELAYS, start=1):
            await limiter.acquire()
            raw = await self._do_get_raw(url, refresh_on_invalid_token=True)
            # Only JSON responses can carry the SERVER_BUSY status code.
            if raw.is_binary:
                return raw
            try:
                env = raw.envelope()
            except Exception:
                # Non-JSON, non-binary (e.g. plain text error) — return as-is
                return raw
            if env.outcome is not JpoOutcome.SERVER_BUSY:
                return raw
            log.warning(
                "JPO server busy (303), backing off %.1fs (attempt %d/%d) url=%s",
                delay, attempt, len(_BUSY_RETRY_DELAYS), url,
            )
            await asyncio.sleep(delay)

        await limiter.acquire()
        return await self._do_get_raw(url, refresh_on_invalid_token=True)

    async def _do_get_raw(
        self, url: str, *, refresh_on_invalid_token: bool
    ) -> JpoRawResponse:
        token = await self._refresh_token()
        response = await self._http.get(url, headers={"Authorization": f"Bearer {token}"})

        if response.status_code == 401 and refresh_on_invalid_token:
            log.info("JPO returned HTTP 401, refreshing token and retrying once")
            await self._refresh_token(force=True)
            return await self._do_get_raw(url, refresh_on_invalid_token=False)

        ctype = response.headers.get("content-type", "")
        raw = JpoRawResponse(
            http_status=response.status_code,
            content_type=ctype,
            content=response.content,
        )

        # JSON-layer 210 (invalid token) → refresh + retry once
        if not raw.is_binary:
            try:
                env = raw.envelope()
                if env.outcome is JpoOutcome.INVALID_TOKEN and refresh_on_invalid_token:
                    log.info("JPO statusCode 210, refreshing token and retrying once")
                    await self._refresh_token(force=True)
                    return await self._do_get_raw(url, refresh_on_invalid_token=False)
            except Exception:
                pass

        return raw

    async def _do_get(self, url: str, *, refresh_on_invalid_token: bool) -> JpoResultEnvelope:
        token = await self._refresh_token()
        response = await self._http.get(url, headers={"Authorization": f"Bearer {token}"})

        # Token rejected at HTTP layer
        if response.status_code == 401 and refresh_on_invalid_token:
            log.info("JPO returned HTTP 401, refreshing token and retrying once")
            await self._refresh_token(force=True)
            return await self._do_get(url, refresh_on_invalid_token=False)

        envelope = parse_envelope(response.json() if response.content else {})

        # Token rejected at envelope layer (statusCode 210)
        if envelope.outcome is JpoOutcome.INVALID_TOKEN and refresh_on_invalid_token:
            log.info("JPO returned statusCode 210, refreshing token and retrying once")
            await self._refresh_token(force=True)
            return await self._do_get(url, refresh_on_invalid_token=False)

        return envelope

    # ---- helpers -------------------------------------------------------

    def _build_url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.config.api_base}{path}"
