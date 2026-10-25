from __future__ import annotations

import json
import os
import re
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import ToolError
from .redaction import redact_log_message, redact_value

# mc_<date>_<time>_<milliseconds>_<random>; sorting by name sorts by start time.
_RUN_ID = re.compile(r"^mc_[0-9]{8}_[0-9]{6}_[0-9]{3}_[a-f0-9]{4}$")


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_run_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    return f"mc_{stamp}_{uuid.uuid4().hex[:4]}"


class RunStore:
    """One directory per run: manifest.json, logs.jsonl and the crawler's data/."""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir.expanduser().resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def run_dir(self, run_id: str) -> Path:
        if not _RUN_ID.fullmatch(run_id):
            raise ToolError("RUN_NOT_FOUND", f"Run not found: {run_id!r}.")
        return self.state_dir / run_id

    def manifest_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "manifest.json"

    def data_dir(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "data"

    def log_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "logs.jsonl"

    def create(self, manifest: dict[str, Any]) -> None:
        run_dir = self.run_dir(manifest["run_id"])
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "data").mkdir()
        self.save(manifest)

    def save(self, manifest: dict[str, Any]) -> None:
        target = self.manifest_path(str(manifest["run_id"]))
        temp = target.with_suffix(f".json.tmp-{os.getpid()}-{uuid.uuid4().hex}")
        temp.write_text(
            json.dumps(redact_value(manifest), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp.replace(target)

    def load(self, run_id: str) -> dict[str, Any]:
        path = self.manifest_path(run_id)
        if not path.is_file():
            raise ToolError("RUN_NOT_FOUND", f"Run not found: {run_id!r}.")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ToolError("OUTPUT_INVALID", f"Run manifest is unreadable: {run_id!r}.") from exc

    def update(self, run_id: str, **changes: Any) -> dict[str, Any]:
        manifest = self.load(run_id)
        manifest.update(changes)
        manifest["updated_at"] = now()
        self.save(manifest)
        return manifest

    def all_manifests(self) -> list[dict[str, Any]]:
        manifests: list[dict[str, Any]] = []
        for path in sorted(self.state_dir.glob("mc_*/manifest.json"), reverse=True):
            try:
                manifests.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return manifests

    def append_log(self, run_id: str, message: str) -> None:
        entry = {"time": now(), "message": redact_log_message(message.rstrip("\r\n"))}
        with self.log_path(run_id).open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def tail_logs(self, run_id: str, limit: int = 100) -> list[dict[str, Any]]:
        path = self.log_path(run_id)
        if not path.is_file():
            return []
        entries: deque[dict[str, Any]] = deque(maxlen=limit)
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return list(entries)

    def has_upstream_errors(self, run_id: str) -> bool:
        path = self.log_path(run_id)
        if not path.is_file():
            return False
        markers = (
            "MEDIACRAWLER ERROR",
            "RUNNER ERROR",
            "TRACEBACK",
            "FAILED BY QRCODE",
            "HAVE NOT FOUND QRCODE",
            "LOGIN QRCODE NOT FOUND",
            "LOGIN FAILED PLEASE CONFIRM",
        )
        detected = False
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    message = str(json.loads(line).get("message", "")).upper()
                except json.JSONDecodeError:
                    continue
                if "LOGIN SUCCESSFUL" in message:
                    detected = False
                    continue
                if any(marker in message for marker in markers):
                    detected = True
        return detected
