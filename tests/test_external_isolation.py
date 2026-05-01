"""Boundary test: tools_external must NOT import from tools_official or jpo.

If this test fails, the architectural rule "no automatic fallback" is at risk —
because once an external tool can call into the official client directly,
nothing prevents an accidental fallback path.
"""

import ast
from pathlib import Path

import pytest


EXTERNAL_PKG = Path(__file__).resolve().parent.parent / "src" / "ip_mcp" / "tools_external"


def _imports_in(file: Path) -> set[str]:
    tree = ast.parse(file.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            # Resolve relative imports against the package
            level = node.level
            if level:
                mod = ".".join([".." * level, mod]).strip(".") if mod else "." * level
            found.add(mod)
    return found


@pytest.mark.parametrize("py_file", list(EXTERNAL_PKG.glob("*.py")))
def test_external_does_not_import_official_or_jpo_client(py_file: Path) -> None:
    forbidden_substrings = ("ip_mcp.jpo", "ip_mcp.tools_official", "tools_official", "..jpo", "..tools_official")
    for module in _imports_in(py_file):
        for forbidden in forbidden_substrings:
            assert forbidden not in module, (
                f"{py_file.name} imports '{module}' which crosses the "
                f"tools_external/tools_official boundary "
                f"(matched forbidden substring: {forbidden!r})"
            )


def test_google_patents_search_module_is_present() -> None:
    assert (EXTERNAL_PKG / "google_patents_search.py").exists()
