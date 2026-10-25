from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

from . import artifacts as artifact_files
from .errors import ToolError
from .lock import InterProcessLease
from .models import PLATFORMS, CrawlRequest
from .normalize import normalize
from .redaction import redact_text, truncate_value
from .settings import Settings
from .store import RunStore, new_run_id, now

ACTIVE_STATES = {"starting", "running", "stopping"}
REQUIRED_CLI_OPTIONS = (
    "--platform",
    "--lt",
    "--type",
    "--start",
    "--get_comment",
    "--get_sub_comment",
    "--get_media",
    "--headless",
    "--save_data_option",
    "--save_data_path",
    "--crawler_max_notes_count",
    "--max_comments_count_singlenotes",
    "--max_concurrency_num",
    "--enable_ip_proxy",
)
# (log marker, phase, what the user has to do)
ATTENTION_RULES = (
    (
        "waiting for scan code login",
        "awaiting_user_login",
        "The platform is not logged in. Scan the QR code shown in Chrome.",
    ),
    (
        "by qrcode",
        "awaiting_user_login",
        "The platform is not logged in. Scan the QR code shown in Chrome.",
    ),
    (
        "confirmation dialog and accept it",
        "awaiting_browser_confirmation",
        "Click Allow in Chrome's remote-debugging confirmation dialog.",
    ),
    (
        "please enable remote debugging",
        "awaiting_browser_setup",
        "Turn on remote debugging at chrome://inspect/#remote-debugging.",
    ),
)
ATTENTION_CLEAR_MARKERS = (
    "successfully connected to existing browser",
    "fallback to standard mode",
    "login successful",
    "failed by qrcode",
    "have not found qrcode",
    "login qrcode not found",
    "login failed please confirm",
    "use cache login state",
    "crawler finished",
)
RECENT_LOG_LINES = 10
RECENT_LOG_CHARS = 300
MAX_LOG_LINE_CHARS = 2_000
MAX_LOG_LINE_BYTES = 1024 * 1024


def _bool_arg(value: bool) -> str:
    return "true" if value else "false"


def _seconds_since(timestamp: str | None, until: str | None = None) -> int | None:
    if not timestamp:
        return None
    try:
        start = datetime.fromisoformat(timestamp)
        end = datetime.fromisoformat(until) if until else datetime.now().astimezone()
    except ValueError:
        return None
    return max(0, int((end - start).total_seconds()))


class CrawlerService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self.store = RunStore(self.settings.state_dir)
        self._lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._leases: dict[str, InterProcessLease] = {}

    # ---- readiness -------------------------------------------------------

    def _readiness_issues(self) -> list[str]:
        issues: list[str] = []
        root = self.settings.mediacrawler_root
        if not root.is_dir():
            return [f"MediaCrawler root does not exist: {root}"]
        for relative in ("main.py", "cmd_arg/arg.py", "config/__init__.py"):
            if not (root / relative).is_file():
                issues.append(f"Required upstream file is missing: {relative}")
        if not self.settings.python_executable.is_file():
            issues.append(
                f"MediaCrawler's Python is missing: {self.settings.python_executable}. "
                "Run `uv sync` in the MediaCrawler checkout."
            )
        return issues

    def _cdp_port_open(self) -> bool:
        # A bare TCP connect; it does not open a DevTools session in Chrome.
        try:
            with socket.create_connection(("127.0.0.1", self.settings.cdp_port), timeout=1):
                return True
        except OSError:
            return False

    async def check(self) -> dict[str, Any]:
        issues = self._readiness_issues()
        missing_options: list[str] = []
        arg_file = self.settings.mediacrawler_root / "cmd_arg" / "arg.py"
        if arg_file.is_file():
            source = await asyncio.to_thread(arg_file.read_text, encoding="utf-8", errors="replace")
            missing_options = [option for option in REQUIRED_CLI_OPTIONS if option not in source]
            if missing_options:
                issues.append(
                    "MediaCrawler's CLI changed; missing options: " + ", ".join(missing_options)
                )
        cdp_open = await asyncio.to_thread(self._cdp_port_open)
        if not cdp_open:
            issues.append(
                f"Nothing is listening on Chrome's debug port {self.settings.cdp_port}. "
                "Open Chrome and turn on chrome://inspect/#remote-debugging."
            )
        active = await asyncio.to_thread(self._active_manifest)
        return {
            "ok": True,
            "ready": not issues,
            "issues": issues,
            "mediacrawler_root": str(self.settings.mediacrawler_root),
            "python": str(self.settings.python_executable),
            "runs_dir": str(self.store.state_dir),
            "chrome_debug_port": self.settings.cdp_port,
            "chrome_debug_port_open": cdp_open,
            "active_run_id": active["run_id"] if active else None,
            "platforms": list(PLATFORMS),
        }

    # ---- starting runs ---------------------------------------------------

    def _lease_path(self) -> Path:
        identity = str(self.settings.mediacrawler_root.resolve())
        if os.name == "nt":
            identity = identity.casefold()
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        return Path(tempfile.gettempdir()) / "mediacrawler-mcp-locks" / f"{digest}.lock"

    def _active_manifest(self) -> dict[str, Any] | None:
        for manifest in self.store.all_manifests():
            if manifest.get("state") not in ACTIVE_STATES:
                continue
            run_id = manifest["run_id"]
            task = self._tasks.get(run_id)
            if task is not None and not task.done():
                return manifest
            pid = manifest.get("pid")
            if pid and self._pid_matches(
                int(pid), self.store.data_dir(run_id), manifest.get("process_create_time")
            ):
                return manifest
            self.store.update(
                run_id,
                state="completed",
                outcome="orphaned",
                finished_at=now(),
                error="The MCP server restarted or the crawler disappeared before completion.",
            )
        return None

    async def crawl(self, request: CrawlRequest, wait_seconds: int = 60) -> dict[str, Any]:
        if not 0 <= wait_seconds <= 300:
            raise ToolError("INVALID_REQUEST", "wait_seconds must be between 0 and 300.")
        async with self._lock:
            issues = self._readiness_issues()
            if issues:
                raise ToolError(
                    "NOT_READY",
                    "MediaCrawler is not ready: " + " ".join(issues),
                    remediation="Call check for details.",
                )
            lease = InterProcessLease(self._lease_path())
            if not lease.acquire():
                active = self._active_manifest()
                raise ToolError(
                    "BUSY",
                    "Another MCP server process is running MediaCrawler.",
                    retryable=True,
                    run_id=active.get("run_id") if active else None,
                    remediation="Wait for that run or stop it.",
                )
            active = self._active_manifest()
            if active:
                lease.release()
                raise ToolError(
                    "BUSY",
                    "Another MediaCrawler run is active.",
                    retryable=True,
                    run_id=active["run_id"],
                    remediation="Call status on it, or stop it before starting another.",
                )

            run_id = new_run_id()
            timestamp = now()
            manifest: dict[str, Any] = {
                "run_id": run_id,
                "request": request.public_dict(),
                "state": "starting",
                "outcome": None,
                "pid": None,
                "process_create_time": None,
                "exit_code": None,
                "created_at": timestamp,
                "updated_at": timestamp,
                "started_at": None,
                "finished_at": None,
                "cancel_requested": False,
                "error": None,
                "warnings": request.warnings(),
            }
            try:
                self.store.create(manifest)
                self._leases[run_id] = lease
                task = asyncio.create_task(self._run(run_id, request), name=f"mediacrawler-{run_id}")
                self._tasks[run_id] = task
            except BaseException:
                self._leases.pop(run_id, None)
                lease.release()
                raise

        if wait_seconds:
            await asyncio.wait({task}, timeout=wait_seconds)
        return await self.status(run_id)

    def _build_command(self, request: CrawlRequest, data_dir: Path) -> tuple[str, ...]:
        runner = Path(__file__).with_name("runner.py")
        return (
            str(self.settings.python_executable),
            str(runner),
            "--platform",
            request.platform,
            "--lt",
            "qrcode",
            "--type",
            request.mode,
            "--start",
            str(request.start_page),
            "--get_comment",
            _bool_arg(request.comments),
            "--get_sub_comment",
            _bool_arg(request.sub_comments),
            "--get_media",
            _bool_arg(request.media),
            "--headless",
            "false",
            "--save_data_option",
            "jsonl",
            "--crawler_max_notes_count",
            str(request.max_items),
            "--max_comments_count_singlenotes",
            str(request.max_comments),
            "--max_concurrency_num",
            "1",
            "--save_data_path",
            str(data_dir),
            "--enable_ip_proxy",
            "false",
        )

    def _launch_spec(self, request: CrawlRequest) -> bytes:
        overrides = request.config_overrides()
        # Always drive the user's own, already logged-in Chrome over CDP.
        overrides.update(
            ENABLE_CDP_MODE=True,
            CDP_CONNECT_EXISTING=True,
            CDP_DEBUG_PORT=self.settings.cdp_port,
        )
        spec = {"root": str(self.settings.mediacrawler_root), "config": overrides}
        return json.dumps(spec, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"

    def _child_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        return env

    async def _run(self, run_id: str, request: CrawlRequest) -> None:
        process: asyncio.subprocess.Process | None = None
        process_create_time: float | None = None
        log_task: asyncio.Task[None] | None = None
        data_dir = self.store.data_dir(run_id)
        try:
            command = self._build_command(request, data_dir)
            kwargs: dict[str, Any] = {}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            async with self._lock:
                if self.store.load(run_id).get("cancel_requested"):
                    self.store.update(run_id, state="completed", outcome="cancelled", finished_at=now())
                    return
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=str(self.settings.mediacrawler_root),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=self._child_env(),
                    **kwargs,
                )
                self._processes[run_id] = process
                process_create_time = psutil.Process(process.pid).create_time()
                if process.stdin is None:
                    raise RuntimeError("Crawler stdin pipe is unavailable")
                process.stdin.write(self._launch_spec(request))
                await process.stdin.drain()
                process.stdin.close()
                self.store.update(
                    run_id,
                    state="running",
                    pid=process.pid,
                    process_create_time=process_create_time,
                    started_at=now(),
                )
            self.store.append_log(
                run_id, f"MediaCrawler started for platform={request.platform} mode={request.mode}."
            )
            log_task = asyncio.create_task(self._capture_logs(run_id, process))

            timed_out = False
            try:
                await asyncio.wait_for(process.wait(), timeout=request.timeout_minutes * 60)
            except TimeoutError:
                timed_out = True
                self.store.append_log(run_id, "Run timed out; stopping process tree.")
                await asyncio.to_thread(self._terminate_pid, process.pid, data_dir, process_create_time)
                await process.wait()

            await log_task
            current = self.store.load(run_id)
            found = await asyncio.to_thread(artifact_files.discover, data_dir)
            record_count = sum(int(item["records"]) for item in found)
            upstream_errors = await asyncio.to_thread(self.store.has_upstream_errors, run_id)
            warnings = list(current.get("warnings", []))
            if record_count and upstream_errors:
                warnings.append(
                    "MediaCrawler wrote records but also logged errors; the result may be incomplete."
                )
            if timed_out:
                outcome, error = "timed_out", "The run exceeded timeout_minutes and was stopped."
            elif current.get("cancel_requested"):
                outcome, error = "cancelled", None
            elif process.returncode != 0:
                outcome = "failed"
                error = f"MediaCrawler exited with code {process.returncode}. See recent_logs."
            elif record_count:
                outcome, error = "data_available", None
            elif upstream_errors:
                outcome = "failed"
                error = "MediaCrawler exited without records after logging errors. See recent_logs."
            else:
                outcome, error = "no_data", None
            self.store.update(
                run_id,
                state="completed",
                outcome=outcome,
                exit_code=process.returncode,
                finished_at=now(),
                error=error,
                warnings=warnings,
                record_count=record_count,
            )
        except asyncio.CancelledError:
            if process and process.returncode is None:
                await asyncio.to_thread(self._terminate_pid, process.pid, data_dir, process_create_time)
                await process.wait()
            cancelled = bool(self.store.load(run_id).get("cancel_requested"))
            self.store.update(
                run_id,
                state="completed",
                outcome="cancelled" if cancelled else "orphaned",
                exit_code=process.returncode if process else None,
                finished_at=now(),
                error=None if cancelled else "The MCP server stopped before the run completed.",
            )
            raise
        except Exception as exc:  # noqa: BLE001 - supervision must always clean up the child
            if process and process.returncode is None:
                await asyncio.to_thread(self._terminate_pid, process.pid, data_dir, process_create_time)
                await process.wait()
            self.store.append_log(run_id, f"Runner error: {redact_text(str(exc))}")
            cancelled = bool(self.store.load(run_id).get("cancel_requested"))
            self.store.update(
                run_id,
                state="completed",
                outcome="cancelled" if cancelled else "failed",
                exit_code=process.returncode if process else None,
                finished_at=now(),
                error=None if cancelled else "Could not start or supervise MediaCrawler. See recent_logs.",
            )
        finally:
            if process and process.returncode is None:
                await asyncio.to_thread(self._terminate_pid, process.pid, data_dir, process_create_time)
                await process.wait()
            if log_task and not log_task.done():
                log_task.cancel()
                await asyncio.gather(log_task, return_exceptions=True)
            self._processes.pop(run_id, None)
            self._tasks.pop(run_id, None)
            lease = self._leases.pop(run_id, None)
            if lease:
                lease.release()

    async def _capture_logs(self, run_id: str, process: asyncio.subprocess.Process) -> None:
        # MediaCrawler logs whole posts on one line, which can exceed StreamReader's
        # 64 KiB readline limit, so split lines ourselves and cap what is stored.
        if process.stdout is None:
            return
        pending = b""
        while True:
            chunk = await process.stdout.read(65_536)
            if not chunk:
                break
            pending += chunk
            *lines, pending = pending.split(b"\n")
            for line in lines:
                self._store_log_line(run_id, line)
            if len(pending) > MAX_LOG_LINE_BYTES:
                self._store_log_line(run_id, pending)
                pending = b""
        if pending:
            self._store_log_line(run_id, pending)

    def _store_log_line(self, run_id: str, raw: bytes) -> None:
        message = raw[:MAX_LOG_LINE_BYTES].decode("utf-8", errors="replace").rstrip("\r")
        if len(message) > MAX_LOG_LINE_CHARS:
            message = message[:MAX_LOG_LINE_CHARS] + "...[truncated]"
        self.store.append_log(run_id, message)

    # ---- process handling ------------------------------------------------

    def _pid_matches(
        self, pid: int, data_dir: Path, expected_create_time: float | None = None
    ) -> bool:
        try:
            return self._process_matches(psutil.Process(pid), data_dir, expected_create_time)
        except (psutil.Error, OSError):
            return False

    @staticmethod
    def _process_matches(
        process: psutil.Process, data_dir: Path | None, expected_create_time: float | None
    ) -> bool:
        if expected_create_time is not None and abs(process.create_time() - expected_create_time) > 0.001:
            return False
        if data_dir is None:
            return True
        command = " ".join(process.cmdline()).lower()
        return "runner.py" in command and str(data_dir).lower() in command

    @classmethod
    def _terminate_pid(
        cls, pid: int, data_dir: Path | None = None, expected_create_time: float | None = None
    ) -> bool:
        try:
            parent = psutil.Process(pid)
            if not cls._process_matches(parent, data_dir, expected_create_time):
                return False
        except (psutil.Error, OSError):
            return False
        processes = parent.children(recursive=True)
        for process in reversed(processes):
            try:
                process.terminate()
            except psutil.Error:
                pass
        try:
            parent.terminate()
        except psutil.Error:
            pass
        _, alive = psutil.wait_procs([*processes, parent], timeout=5)
        for process in alive:
            try:
                process.kill()
            except psutil.Error:
                pass
        return True

    # ---- inspecting runs -------------------------------------------------

    @staticmethod
    def _detect_attention(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
        attention = None
        for entry in entries:
            lowered = str(entry.get("message", "")).lower()
            if any(marker in lowered for marker in ATTENTION_CLEAR_MARKERS):
                attention = None
                continue
            for marker, phase, instruction in ATTENTION_RULES:
                if marker in lowered:
                    attention = {"phase": phase, "message": instruction, "since": entry.get("time")}
        return attention

    def _latest_run_id(self) -> str:
        manifests = self.store.all_manifests()
        if not manifests:
            raise ToolError("RUN_NOT_FOUND", "No runs yet.")
        return str(manifests[0]["run_id"])

    async def status(self, run_id: str | None = None) -> dict[str, Any]:
        run_id = run_id or await asyncio.to_thread(self._latest_run_id)
        manifest = await asyncio.to_thread(self.store.load, run_id)
        if manifest.get("state") in ACTIVE_STATES:
            await asyncio.to_thread(self._active_manifest)
            manifest = await asyncio.to_thread(self.store.load, run_id)
        data_dir = self.store.data_dir(run_id)
        found = await asyncio.to_thread(artifact_files.discover, data_dir)
        active = manifest["state"] in ACTIVE_STATES
        tail = await asyncio.to_thread(self.store.tail_logs, run_id, 100)
        attention = self._detect_attention(tail) if active else None
        recent_logs = [
            str(entry.get("message", ""))[:RECENT_LOG_CHARS] for entry in tail[-RECENT_LOG_LINES:]
        ]
        result: dict[str, Any] = {
            "ok": True,
            "run_id": run_id,
            "request": manifest.get("request"),
            "state": manifest["state"],
            "outcome": manifest.get("outcome"),
            "attention": attention,
            "error": manifest.get("error"),
            "warnings": manifest.get("warnings", []),
            "elapsed_seconds": _seconds_since(manifest.get("created_at"), manifest.get("finished_at")),
            "record_counts": artifact_files.count_by_type(found),
            "data_dir": str(data_dir.resolve()),
            "recent_logs": recent_logs,
        }
        if active:
            result["next"] = "Call status again in a few seconds" + (
                "; the user must act first (see attention)." if attention else "."
            )
        elif manifest.get("outcome") == "data_available":
            result["next"] = "Call results with this run_id."
        return result

    async def stop(self, run_id: str) -> dict[str, Any]:
        async with self._lock:
            manifest = self.store.load(run_id)
            if manifest.get("state") == "completed":
                result = await self.status(run_id)
                result["already_stopped"] = True
                return result
            self.store.update(run_id, state="stopping", cancel_requested=True)
            process = self._processes.get(run_id)
            pid = process.pid if process else manifest.get("pid")
            task = self._tasks.get(run_id)

        data_dir = self.store.data_dir(run_id)
        if pid and (
            process is not None
            or self._pid_matches(int(pid), data_dir, manifest.get("process_create_time"))
        ):
            await asyncio.to_thread(
                self._terminate_pid, int(pid), data_dir, manifest.get("process_create_time")
            )
        if task:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=15)
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        else:
            self.store.update(run_id, state="completed", outcome="cancelled", finished_at=now(), error=None)
        result = await self.status(run_id)
        result["already_stopped"] = False
        return result

    async def results(
        self,
        run_id: str,
        kind: str = "contents",
        offset: int = 0,
        limit: int = 20,
        raw: bool = False,
    ) -> dict[str, Any]:
        kind = kind.strip().lower()
        if kind not in artifact_files.RECORD_TYPES:
            raise ToolError(
                "INVALID_REQUEST", f"kind must be one of: {', '.join(artifact_files.RECORD_TYPES)}."
            )
        if offset < 0:
            raise ToolError("INVALID_REQUEST", "offset must be zero or greater.")
        if not 1 <= limit <= 100:
            raise ToolError("INVALID_REQUEST", "limit must be between 1 and 100.")
        manifest = await asyncio.to_thread(self.store.load, run_id)
        found = await asyncio.to_thread(artifact_files.discover, self.store.data_dir(run_id))
        files = [item for item in found if item["record_type"] == kind]

        def collect() -> tuple[list[dict[str, Any]], int]:
            records: list[dict[str, Any]] = []
            index = 0
            for item in files:
                for record in artifact_files.iter_records(Path(item["path"])):
                    if offset <= index < offset + limit:
                        records.append(truncate_value(record) if raw else normalize(record, kind))
                    index += 1
            return records, index

        records, total = await asyncio.to_thread(collect)
        return {
            "ok": True,
            "run_id": run_id,
            "platform": manifest.get("request", {}).get("platform"),
            "state": manifest.get("state"),
            "kind": kind,
            "total": total,
            "offset": offset,
            "returned": len(records),
            "next_offset": offset + len(records) if offset + len(records) < total else None,
            "records": records,
            "files": [item["path"] for item in files],
            "available": artifact_files.count_by_type(found),
        }

    async def runs(self, limit: int = 20) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise ToolError("INVALID_REQUEST", "limit must be between 1 and 100.")
        manifests = await asyncio.to_thread(self.store.all_manifests)
        summaries = []
        for manifest in manifests[:limit]:
            request = manifest.get("request", {})
            summaries.append(
                {
                    "run_id": manifest.get("run_id"),
                    "platform": request.get("platform"),
                    "mode": request.get("mode"),
                    "keywords": request.get("keywords", []),
                    "targets": request.get("targets", []),
                    "state": manifest.get("state"),
                    "outcome": manifest.get("outcome"),
                    "record_count": manifest.get("record_count"),
                    "created_at": manifest.get("created_at"),
                }
            )
        return {"ok": True, "runs": summaries, "returned": len(summaries)}

    async def shutdown(self) -> None:
        for run_id in set(self._tasks) | set(self._leases):
            try:
                if self.store.load(run_id).get("state") in ACTIVE_STATES:
                    await self.stop(run_id)
            except ToolError:
                continue
        remaining = [task for task in self._tasks.values() if not task.done()]
        for task in remaining:
            task.cancel()
        if remaining:
            await asyncio.gather(*remaining, return_exceptions=True)
