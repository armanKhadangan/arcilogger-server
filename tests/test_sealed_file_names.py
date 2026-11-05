"""The sealed-file names are built in one place (Wave 5 contract H3).

Days sealed before W5-H are ``<source>.log.gz`` and ``audit.jsonl.gz``; later days are ``.xz``,
and both are read forever. A name built by hand elsewhere would find one format and miss the
other, so every string literal of the product code (``*/src``) is checked: only
``arcilogger_core/sealedfile.py`` may spell a sealed name. Docstrings and comments are text, not
names, and are not checked.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted(path for path in ROOT.glob("*/src/**/*.py"))
OWNER = ROOT / "core" / "src" / "arcilogger_core" / "sealedfile.py"

# Built from parts, so this file holds no sealed name either.
_SEALED = re.compile(r"(\.log|audit\.jsonl)\." + "(" + "|".join(("gz", "xz")) + r")\b")


def _docstrings(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def _sealed_literals(path: Path, root: Path = ROOT) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    skip = _docstrings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            skip.update(id(part) for part in node.values)  # checked as the whole f-string
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            # f"{source}.log.gz": the constant parts with a marker where a value goes.
            text = "".join(
                str(part.value) if isinstance(part, ast.Constant) else "{}" for part in node.values
            )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in skip:
                continue
            text = node.value
        else:
            continue
        if _SEALED.search(text):
            found.append(f"{path.relative_to(root).as_posix()}:{node.lineno}: {text!r}")
    return found


def test_the_guard_recognises_sealed_names(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    names = [part + "." + kind for part in (".log", "audit.jsonl") for kind in ("gz", "xz")]
    sample.write_text(
        '"""A docstring may name '
        + names[0]
        + '."""\n'
        + "".join(f'A{index} = "x{name}"\n' for index, name in enumerate(names))
        + 'B = f"{source}'
        + names[1]
        + '"\n'
        + 'C = "fortigate.log"\n'
        + 'D = ".gz"\n',
        encoding="utf-8",
    )
    found = [line.split(": ", 1)[1] for line in _sealed_literals(sample, tmp_path)]
    assert found == [*(repr(f"x{name}") for name in names), repr("{}" + names[1])]


def test_no_sealed_name_is_built_outside_sealedfile() -> None:
    assert OWNER in SOURCES
    found = [hit for path in SOURCES if path != OWNER for hit in _sealed_literals(path)]
    assert found == [], (
        "A sealed file name is written by hand. Days are sealed as .gz (before W5-H) or .xz, so "
        "use arcilogger_core.sealedfile (log_name, audit_name, find_log, find_audit):\n"
        + "\n".join(found)
    )
