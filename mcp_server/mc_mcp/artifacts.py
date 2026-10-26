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

# A post found by several keywords is stored once per keyword, and its comments are
# fetched again each time, so records are de-duplicated by these ids when read.
ID_KEYS = {
    "contents": ("note_id", "aweme_id", "video_id", "content_id"),
    "comments": ("comment_id",),
}


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


def _record_id(record: dict[str, Any], kind: str) -> str | None:
    for key in ID_KEYS.get(kind, ()):
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def unique_records(artifacts: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    """Records of one kind in file order, keeping the first copy of each id.

    Contents gain `source_keywords`: every keyword that found the post.
    """
    records: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for artifact in artifacts:
        if artifact["record_type"] != kind:
            continue
        for record in iter_records(Path(artifact["path"])):
            record_id = _record_id(record, kind)
            keyword = record.get("source_keyword")
            if record_id is not None and record_id in by_id:
                kept = by_id[record_id]
                if kind == "contents" and keyword and keyword not in kept["source_keywords"]:
                    kept["source_keywords"].append(keyword)
                continue
            if kind == "contents":
                record = {**record, "source_keywords": [keyword] if keyword else []}
            if record_id is not None:
                by_id[record_id] = record
            records.append(record)
    return records


def count_by_type(artifacts: list[dict[str, Any]]) -> dict[str, int]:
    """Record counts per kind, counting de-duplicated kinds by unique id."""
    counts: dict[str, int] = {}
    for artifact in artifacts:
        record_type = str(artifact["record_type"])
        if record_type in ID_KEYS:
            continue
        counts[record_type] = counts.get(record_type, 0) + int(artifact["records"])
    for kind in ID_KEYS:
        if any(artifact["record_type"] == kind for artifact in artifacts):
            counts[kind] = len(unique_records(artifacts, kind))
    return counts
