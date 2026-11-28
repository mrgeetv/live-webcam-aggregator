"""Only `store` may import sqlite3: it owns every connection, pragma and transaction.

A module opening its own connection would skip the store's pragmas, its write lock
and its failure handling, so a failing disk could then stop a refresh.
"""

from __future__ import annotations

import ast
from pathlib import Path

import webcam_aggregator

_SRC = Path(webcam_aggregator.__file__).parent


def _imports_sqlite3(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names = [node.module or ""]
        else:
            continue
        if any(n.partition(".")[0] in {"sqlite3", "_sqlite3"} for n in names):
            return True
    return False


def test_only_the_store_imports_sqlite3() -> None:
    owners = sorted(
        str(path.relative_to(_SRC))
        for path in _SRC.rglob("*.py")
        if _imports_sqlite3(ast.parse(path.read_text(), str(path)))
    )
    assert owners == ["store.py"]
