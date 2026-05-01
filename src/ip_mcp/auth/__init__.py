"""OAuth 2.1 server for IP-MCP — single-user, SQLite-backed.

The MCP SDK auto-generates /authorize, /token, /register, /revoke, and the
well-known metadata endpoints. This package implements the user-consent flow
on top, plus the SQLite-backed provider (``SqliteOAuthProvider``) that backs
them all so DCR clients and issued tokens survive container restarts.
"""
