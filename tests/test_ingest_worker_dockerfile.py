"""The ingest-worker image copies scripts one by one: every local module a copied script imports
must be copied too, or the script fails in the image (tests run with scripts/ on the path and
can't see that). 2026-11-06: ingest-folder.py imported virus_scan_cache, which wasn't copied."""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def copied_scripts() -> set[str]:
    text = (ROOT / "ingest-worker" / "Dockerfile").read_text()
    return set(re.findall(r"scripts/([\w\-]+\.py)", text))


def local_imports(path: Path) -> set[str]:
    """Top-level names of scripts/ modules this file imports (anywhere, incl. inside functions)."""
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module.split(".")[0]]
        out |= {n for n in names if (SCRIPTS / f"{n}.py").is_file()}
    return out


def test_every_local_import_of_a_copied_script_is_copied() -> None:
    copied = copied_scripts()
    missing = {}
    seen, todo = set(), sorted(copied)
    while todo:
        name = todo.pop()
        if name in seen or not (SCRIPTS / name).is_file():
            continue
        seen.add(name)
        for mod in local_imports(SCRIPTS / name):
            if f"{mod}.py" not in copied:
                missing.setdefault(f"{mod}.py", []).append(name)
            todo.append(f"{mod}.py")
    assert not missing, f"add to the COPY list in ingest-worker/Dockerfile: {missing}"
