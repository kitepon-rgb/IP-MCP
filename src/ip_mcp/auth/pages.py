"""HTTP handlers for the OAuth consent page (GET + POST /consent).

The page is intentionally minimal: just a master-password input. We escape
the client name before rendering it because it comes from the (untrusted)
DCR registration payload.
"""

from __future__ import annotations

import html
import logging

from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from .provider import SqliteOAuthProvider

log = logging.getLogger(__name__)


_CONSENT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>IP-MCP authorization</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      max-width: 22em; margin: 4em auto; padding: 0 1em; color: #222;
    }}
    h2 {{ margin: 0 0 0.4em; font-size: 1.2em; }}
    p  {{ color: #555; line-height: 1.5; font-size: 0.95em; }}
    code {{ background: #f0f0f0; padding: 0.1em 0.3em; border-radius: 3px; }}
    input[type=password] {{
      width: 100%; padding: 0.6em; font-size: 1em;
      border: 1px solid #bbb; border-radius: 4px; box-sizing: border-box;
    }}
    button {{
      margin-top: 1em; padding: 0.7em 1.5em; font-size: 1em;
      background: #2a6; color: white; border: 0; border-radius: 4px;
      cursor: pointer; width: 100%;
    }}
    button:hover {{ background: #195; }}
    .err {{ color: #c33; margin: 0.6em 0; font-size: 0.9em; }}
  </style>
</head>
<body>
  <h2>IP-MCP authorization</h2>
  <p>Client <code>{client_name}</code> is requesting access. Enter the master
  password to approve.</p>
  {error_html}
  <form method="post" action="/consent">
    <input type="hidden" name="session_id" value="{session_id}">
    <input type="password" name="password" placeholder="master password"
           autocomplete="off" autofocus required>
    <button type="submit">Approve</button>
  </form>
</body>
</html>"""


def _render(session_id: str, client_name: str, error: str = "") -> str:
    error_html = (
        f'<p class="err">{html.escape(error)}</p>' if error else ""
    )
    return _CONSENT_HTML.format(
        session_id=html.escape(session_id),
        client_name=html.escape(client_name),
        error_html=error_html,
    )


def make_consent_handlers(provider: SqliteOAuthProvider):
    """Return ``(get_handler, post_handler)`` for /consent."""

    async def consent_get(request: Request):
        session_id = request.query_params.get("session_id", "").strip()
        if not session_id:
            return PlainTextResponse("missing session_id", status_code=400)
        pending = provider.get_pending_consent(session_id)
        if pending is None:
            return PlainTextResponse(
                "this consent session is invalid or has expired",
                status_code=400,
            )
        client, _ = pending
        client_name = client.client_name or client.client_id or "(unnamed)"
        return HTMLResponse(_render(session_id, client_name))

    async def consent_post(request: Request):
        form = await request.form()
        session_id = str(form.get("session_id", "")).strip()
        password = str(form.get("password", ""))

        if not session_id or not password:
            return PlainTextResponse("missing fields", status_code=400)

        pending = provider.get_pending_consent(session_id)
        if pending is None:
            return PlainTextResponse(
                "this consent session is invalid or has expired",
                status_code=400,
            )

        redirect_url = provider.approve_consent(session_id, password)
        if redirect_url is None:
            client, _ = pending
            client_name = client.client_name or client.client_id or "(unnamed)"
            return HTMLResponse(
                _render(session_id, client_name, error="incorrect password"),
                status_code=401,
            )
        return RedirectResponse(redirect_url, status_code=302)

    return consent_get, consent_post
