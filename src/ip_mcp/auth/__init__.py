"""OAuth 2.1 server for IP-MCP — single-user, in-memory.

The MCP SDK auto-generates /authorize, /token, /register, /revoke, and the
well-known metadata endpoints. This package implements the user-consent flow
on top, plus the in-memory provider that backs them all.
"""
