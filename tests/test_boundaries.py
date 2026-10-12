"""Core modules never import the integrations, so core works without them."""

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).parents[1] / "src" / "sbe_qa_processing"
# cli.py puts every command together
NOT_CORE = {"cli.py"}


def imported_modules(path: Path) -> set[str]:
    modules = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules |= {f"{node.module}.{alias.name}" for alias in node.names}
    return modules


@pytest.mark.parametrize(
    "path",
    [p for p in sorted(PACKAGE.glob("*.py")) if p.name not in NOT_CORE],
    ids=lambda p: p.name,
)
def test_core_doesnt_import_integrations(path):
    integrations = {m for m in imported_modules(path) if "sbe_qa_processing.integrations" in m}
    assert not integrations, f"{path.name} imports {sorted(integrations)}"
