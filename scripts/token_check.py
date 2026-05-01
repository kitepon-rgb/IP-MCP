"""Standalone smoke test: can we obtain a JPO access token from the env?

Usage (from the project root, after `uv sync`):
    uv run python scripts/token_check.py

Exits 0 on success, 1 on failure. Does NOT print the token.
"""

from __future__ import annotations

import asyncio
import os
import sys

from ip_mcp.jpo.client import JpoClient, JpoConfig


async def _main() -> int:
    config = JpoConfig.from_env()
    if not config.has_credentials:
        print("ERROR: set JPO_USERNAME/JPO_PASSWORD (or JPO_TOKEN) in .env first.", file=sys.stderr)
        return 1

    async with JpoClient(config=config) as client:
        try:
            token = await client._refresh_token(force=not config.pre_issued_token)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL: token request raised {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

    if not token:
        print("FAIL: empty token returned", file=sys.stderr)
        return 1

    print(f"OK: obtained access token ({len(token)} chars). Auth flow works.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
