from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# Upstream file names look like search_contents_2026-10-25.jsonl.
_UPSTREAM_ARTIFACT_NAME = re.compile(
    r"^(search|detail|creator)_(contents|comments|creators|contacts|dynamics)(?:_|$)"
)
RECORD_TYPES = ("contents", "comments", "creators", "contacts", "dynamics")


def classify(path: Path) -> str:
    match = _UPSTREAM_ARTIFACT_NAME.match(path.stem)
    return match.group(2) if match else "unknown"


def iter_records(path: Path) -> Iterator[dict[str, Any]]:
    """Yield complete JSON objects, skipping a partially written final line."""
    with path.open("rb") as handle:
        for raw in handle:
            if not raw.endswith(b"\n"):
                continue
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                yield value


def _count_lines(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for raw in handle if raw.endswith(b"\n") and raw.strip())


def discover(data_dir: Path) -> list[dict[str, Any]]:
    if not data_dir.is_dir():
        return []
    artifacts: list[dict[str, Any]] = []
    for path in sorted(data_dir.rglob("*.jsonl")):
        try:
            records = _count_lines(path)
        except OSError:
            continue
        artifacts.append(
            {
                "record_type": classify(path),
                "path": str(path.resolve()),
                "records": records,
            }
        )
    return artifacts


def count_by_type(artifacts: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for artifact in artifacts:
        record_type = str(artifact["record_type"])
        counts[record_type] = counts.get(record_type, 0) + int(artifact["records"])
    return counts
