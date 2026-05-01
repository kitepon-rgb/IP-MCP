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

    # Register Phase 1A tools — official JPO API only.
    # tools_external/* will be wired in S6 (separate registration to keep
    # the import boundary visible).
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
