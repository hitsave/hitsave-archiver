"""Read and rewrite BagIt manifest lines (`<digest> <path>`) with one separator per file.

bagit-python writes payload manifests (manifest-*.txt) with two spaces between digest and path,
and tag manifests (tagmanifest-*.txt) with one. Both are valid BagIt, and GNU and uutils
`sha256sum -c` read either, but not both in one file. Every rewrite here uses the separator of
the file's first line for every line, so a file that was mixed becomes uniform.
"""
from __future__ import annotations

import re

_FIRST = re.compile(r"^\S+( +)")


def separator(text: str) -> str:
    """The separator the file's first line uses: one space or two (two for an empty file)."""
    for line in text.splitlines():
        if line.strip():
            m = _FIRST.match(line)
            return "  " if m is None or len(m.group(1)) >= 2 else " "
    return "  "


def entries(text: str) -> list[tuple[str, str]]:
    """[(digest, path)] in file order; blank lines skipped."""
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise ValueError(f"Unexpected manifest line: {line!r}")
        out.append((parts[0], parts[1].strip()))
    return out


def render(rows: list[tuple[str, str]], sep: str) -> str:
    return "".join(f"{digest}{sep}{path}\n" for digest, path in rows)


def upsert(text: str, digests: dict[str, str]) -> str:
    """Replace the digest of listed paths in place; append new paths, sorted, at the end."""
    remaining = dict(digests)
    rows = [(remaining.pop(path, digest), path) for digest, path in entries(text)]
    rows.extend((digest, path) for path, digest in sorted(remaining.items()))
    return render(rows, separator(text))


def add(text: str, path: str, digest: str) -> str:
    """Append one path that must not be listed yet."""
    if any(p == path for _, p in entries(text)):
        raise ValueError(f"{path} is already in the manifest")
    return upsert(text, {path: digest})


def replace(text: str, path: str, digest: str) -> str:
    """Change the digest of one path that must be listed."""
    if not any(p == path for _, p in entries(text)):
        raise ValueError(f"Path {path!r} not in manifest")
    return upsert(text, {path: digest})


def rename(text: str, renames: dict[str, str]) -> str:
    rows = [(digest, renames.get(path, path)) for digest, path in entries(text)]
    return render(rows, separator(text))
