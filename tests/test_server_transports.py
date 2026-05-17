import pytest

from ip_mcp.server import build_dual_transport_app, build_server, read_transport_from_env


@pytest.fixture(autouse=True)
def clear_server_env(monkeypatch):
    for name in (
        "MCP_OAUTH_MASTER_PASSWORD",
        "MCP_OAUTH_ISSUER_URL",
        "JPO_USERNAME",
        "JPO_PASSWORD",
        "JPO_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)


def test_dual_transport_app_exposes_mcp_and_sse_routes():
    mcp, _client = build_server()

    app = build_dual_transport_app(mcp)

    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/mcp" in paths
    assert "/sse" in paths
    assert "/messages" in paths


def test_transport_env_accepts_both(monkeypatch):
    monkeypatch.setenv("MCP_TRANSPORT", "both")

    assert read_transport_from_env() == "both"


def test_transport_env_rejects_unknown_value(monkeypatch):
    monkeypatch.setenv("MCP_TRANSPORT", "websocket")

    with pytest.raises(ValueError, match="sse, streamable-http, or both"):
        read_transport_from_env()
