"""IP-MCP server entry point.

Run as a module (``python -m ip_mcp.server``) or via the script entry
``ip-mcp`` defined in pyproject.toml. Uses Server-Sent Events transport so
clients on the LAN can connect over HTTP.
"""

from __future__ import annotations

import logging
import os

from mcp.server.fastmcp import FastMCP

from .jpo.client import JpoClient, JpoConfig
from .tools_official import convert as tool_convert
from .tools_official import progress as tool_progress

log = logging.getLogger(__name__)


def build_server() -> tuple[FastMCP, JpoClient]:
    config = JpoConfig.from_env()
    if not config.has_credentials:
        log.warning(
            "No JPO credentials in environment (JPO_USERNAME/JPO_PASSWORD or JPO_TOKEN). "
            "Tools will fail until credentials are provided."
        )

    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8765"))

    # FastMCP exposes host/port via settings — passed at construction time.
    mcp = FastMCP("ip-mcp", host=host, port=port)

    client = JpoClient(config=config)

    # Register tools — official only for Phase 1A.
    # tools_external/* will be wired in S6 (separate registration to keep
    # the import boundary visible).
    tool_convert.register(mcp, client)
    tool_progress.register(mcp, client)

    return mcp, client


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    mcp, _client = build_server()
    log.info(
        "starting IP-MCP on %s:%s (transport=sse)",
        os.getenv("MCP_HOST", "0.0.0.0"),
        os.getenv("MCP_PORT", "8765"),
    )
    mcp.run(transport="sse")


if __name__ == "__main__":
    main()
