"""IP-MCP server entry point.

Run as a module (``python -m ip_mcp.server``) or via the script entry
``ip-mcp`` defined in pyproject.toml. Uses HTTP transports so clients on the
LAN can connect over MCP.
"""

from __future__ import annotations

import logging
import os
from typing import Literal, cast

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import BaseRoute

from .jpo.client import JpoClient, JpoConfig
from .tools_official import (
    applicant as tool_applicant,
)
from .tools_official import (
    citations as tool_citations,
)
from .tools_official import (
    convert as tool_convert,
)
from .tools_official import (
    documents as tool_documents,
)
from .tools_official import (
    fetch_full_record as tool_fetch_full_record,
)
from .tools_official import (
    jpp_url as tool_jpp_url,
)
from .tools_official import (
    opd as tool_opd,
)
from .tools_official import (
    progress as tool_progress,
)
from .tools_official import (
    registration as tool_registration,
)
from .tools_official import (
    relations as tool_relations,
)

log = logging.getLogger(__name__)
Transport = Literal["sse", "streamable-http", "both"]
FastMcpTransport = Literal["sse", "streamable-http"]
VALID_TRANSPORTS: set[Transport] = {"sse", "streamable-http", "both"}


def build_server() -> tuple[FastMCP, JpoClient]:
    config = JpoConfig.from_env()
    if not config.has_credentials:
        log.warning(
            "No JPO credentials in environment (JPO_USERNAME/JPO_PASSWORD or JPO_TOKEN). "
            "Tools will fail until credentials are provided."
        )

    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8765"))
    mount_path = os.getenv("MCP_MOUNT_PATH", "").rstrip("/")

    # ----- OAuth setup (optional) -----
    # When MCP_OAUTH_MASTER_PASSWORD + MCP_OAUTH_ISSUER_URL are both set,
    # the SDK auto-generates /authorize, /token, /register, /revoke, and the
    # well-known metadata endpoints. We add /consent on top via custom_route.
    master_password = os.getenv("MCP_OAUTH_MASTER_PASSWORD", "").strip()
    issuer_url = os.getenv("MCP_OAUTH_ISSUER_URL", "").strip()
    db_path = os.getenv("MCP_OAUTH_DB_PATH", "/app/data/oauth.db").strip()
    auth_provider = None
    auth_settings = None
    if master_password and issuer_url:
        from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions

        from .auth.provider import SqliteOAuthProvider

        consent_url = f"{issuer_url.rstrip('/')}/consent"
        auth_provider = SqliteOAuthProvider(
            master_password=master_password,
            consent_url=consent_url,
            db_path=db_path,
        )
        auth_settings = AuthSettings(
            issuer_url=issuer_url,
            resource_server_url=issuer_url,
            client_registration_options=ClientRegistrationOptions(enabled=True),
        )
        log.info("OAuth 2.1 enabled, issuer=%s", issuer_url)
    elif master_password or issuer_url:
        log.warning(
            "MCP_OAUTH_MASTER_PASSWORD and MCP_OAUTH_ISSUER_URL must BOTH be set "
            "to enable OAuth — running without authentication"
        )

    mcp_kwargs: dict[str, object] = {
        "host": host,
        "port": port,
        "mount_path": mount_path or "/",
    }
    if auth_provider is not None and auth_settings is not None:
        mcp_kwargs["auth_server_provider"] = auth_provider
        mcp_kwargs["auth"] = auth_settings

    mcp = FastMCP("ip-mcp", **mcp_kwargs)

    # Register the OAuth consent page (only when OAuth is enabled).
    if auth_provider is not None:
        from .auth.pages import make_consent_handlers

        consent_get, consent_post = make_consent_handlers(auth_provider)
        mcp.custom_route("/consent", methods=["GET"])(consent_get)
        mcp.custom_route("/consent", methods=["POST"])(consent_post)

    client = JpoClient(config=config)

    # Register Phase 1A tools — official JPO API only.
    # The two registration calls (tools_official.* and tools_external.*) are
    # kept deliberately separate so the boundary is visible in code review.
    tool_convert.register(mcp, client)
    tool_progress.register(mcp, client)
    tool_registration.register(mcp, client)
    tool_citations.register(mcp, client)
    tool_relations.register(mcp, client)
    tool_applicant.register(mcp, client)
    tool_documents.register(mcp, client)
    tool_jpp_url.register(mcp, client)
    tool_opd.register(mcp, client)              # auto-skips when JPO_ENABLE_OPD=0
    tool_fetch_full_record.register(mcp, client)

    # Register Phase 1B tools — external sources, independent module.
    # tools_external.register() takes NO JpoClient (intentional: zero coupling).
    from .tools_external import google_patents_search as tool_external_search
    tool_external_search.register(mcp)          # auto-skips when EXTERNAL_GOOGLE_PATENTS_ENABLED=0

    return mcp, client


def normalize_route_path(path: str) -> str:
    if path == "/":
        return path
    return path.rstrip("/")


def is_sse_transport_route(route: BaseRoute, transport_paths: set[str]) -> bool:
    route_path = getattr(route, "path", None)
    if not isinstance(route_path, str):
        return False
    return normalize_route_path(route_path) in transport_paths


def build_dual_transport_app(mcp: FastMCP) -> Starlette:
    """Build a single Starlette app that serves Streamable HTTP and SSE MCP.

    FastMCP exposes one transport per app. Some clients still probe `/sse`,
    while Codex direct HTTP MCP uses `/mcp`, so production needs both routes
    from the same OAuth issuer and token database.
    """

    streamable_app = mcp.streamable_http_app()
    sse_app = mcp.sse_app()
    sse_transport_paths = {
        normalize_route_path(mcp.settings.sse_path),
        normalize_route_path(mcp.settings.message_path),
    }
    routes = list(streamable_app.routes)
    routes.extend(
        route for route in sse_app.routes if is_sse_transport_route(route, sse_transport_paths)
    )
    return Starlette(
        debug=mcp.settings.debug,
        routes=routes,
        middleware=list(streamable_app.user_middleware),
        lifespan=lambda app: mcp.session_manager.run(),
    )


async def run_dual_transport_async(mcp: FastMCP) -> None:  # pragma: no cover
    import uvicorn

    app = build_dual_transport_app(mcp)
    config = uvicorn.Config(
        app,
        host=mcp.settings.host,
        port=mcp.settings.port,
        log_level=mcp.settings.log_level.lower(),
    )
    server = uvicorn.Server(config)
    await server.serve()


def read_transport_from_env() -> Transport:
    transport = os.getenv("MCP_TRANSPORT", "sse").strip() or "sse"
    if transport not in VALID_TRANSPORTS:
        raise ValueError("MCP_TRANSPORT must be sse, streamable-http, or both")
    return cast(Transport, transport)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    mcp, _client = build_server()
    transport = read_transport_from_env()
    log.info(
        "starting IP-MCP on %s:%s (transport=%s)",
        os.getenv("MCP_HOST", "0.0.0.0"),
        os.getenv("MCP_PORT", "8765"),
        transport,
    )
    if transport == "both":
        import anyio

        anyio.run(lambda: run_dual_transport_async(mcp))
    else:
        mcp.run(transport=cast(FastMcpTransport, transport))


if __name__ == "__main__":
    main()
