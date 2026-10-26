from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import replace

import psutil
import pytest

from mc_mcp.errors import ToolError
from mc_mcp.models import CrawlRequest
from mc_mcp.service import CrawlerService


def search(keyword: str = "海优", **overrides: object) -> CrawlRequest:
    values: dict[str, object] = {"platform": "xhs", "mode": "search", "keywords": [keyword]}
    values.update(overrides)
    return CrawlRequest.create(**values)


def fake_config(service: CrawlerService, run_id: str) -> dict[str, object]:
    for entry in service.store.tail_logs(run_id, 1000):
        message = str(entry["message"])
        if message.startswith("fake config "):
            return json.loads(message.removeprefix("fake config "))
    raise AssertionError("fake crawler did not report its config")


async def wait_for_state(service: CrawlerService, run_id: str, expected: str = "completed") -> dict:
    deadline = asyncio.get_running_loop().time() + 20
    while True:
        result = await service.status(run_id)
        if result["state"] == expected:
            return result
        if asyncio.get_running_loop().time() >= deadline:
            pytest.fail(f"run {run_id} did not reach {expected}: {result}")
        await asyncio.sleep(0.05)


async def wait_for_attention(service: CrawlerService, run_id: str) -> dict:
    deadline = asyncio.get_running_loop().time() + 20
    while True:
        result = await service.status(run_id)
        if result["attention"]:
            return result
        if asyncio.get_running_loop().time() >= deadline:
            pytest.fail(f"run {run_id} never asked for attention: {result}")
        await asyncio.sleep(0.05)


async def test_check_reports_ready(service: CrawlerService) -> None:
    result = await service.check()
    assert result["ready"] is True, result["issues"]
    assert result["chrome_debug_port_open"] is True


async def test_check_reports_closed_debug_port(service: CrawlerService) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    service.settings = replace(service.settings, cdp_port=free_port)
    result = await service.check()
    assert result["ready"] is False
    assert any("debug port" in issue for issue in result["issues"])


async def test_crawl_waits_and_returns_completed_status(service: CrawlerService) -> None:
    result = await service.crawl(search(sort="latest", comments=True), wait_seconds=20)
    assert result["state"] == "completed"
    assert result["outcome"] == "data_available"
    assert result["record_counts"] == {"contents": 3, "comments": 1}
    assert result["next"] == "Call results with this run_id."

    seen = fake_config(service, result["run_id"])
    assert seen["KEYWORDS"] == "海优"
    assert seen["SORT_TYPE"] == "time_descending"
    assert seen["ENABLE_CDP_MODE"] is True
    assert seen["CDP_CONNECT_EXISTING"] is True
    assert seen["CDP_DEBUG_PORT"] == service.settings.cdp_port
    assert seen["BROWSER_LAUNCH_TIMEOUT"] == service.settings.cdp_timeout


async def test_crawl_refuses_when_debug_port_is_closed(service: CrawlerService) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    service.settings = replace(service.settings, cdp_port=free_port)
    with pytest.raises(ToolError) as error:
        await service.crawl(search(), wait_seconds=0)
    assert error.value.code == "CHROME_NOT_REACHABLE"
    assert service.store.all_manifests() == []


async def test_bundled_browser_fallback_is_blocked_and_explained(service: CrawlerService) -> None:
    result = await service.crawl(search("fallback"), wait_seconds=20)
    assert result["outcome"] == "failed"
    assert "Could not connect to Chrome" in result["error"]
    messages = " ".join(str(e["message"]) for e in service.store.tail_logs(result["run_id"], 1000))
    assert "refuses to launch Playwright's bundled browser" in messages


async def test_results_and_counts_are_deduplicated(service: CrawlerService) -> None:
    result = await service.crawl(search("dupes"), wait_seconds=20)
    assert result["record_counts"] == {"contents": 2, "comments": 2}

    contents = await service.results(result["run_id"])
    assert contents["total"] == 2
    assert contents["duplicates_removed"] == 1
    assert [record["id"] for record in contents["records"]] == ["n1", "n2"]
    assert contents["records"][0]["keywords"] == ["海优", "优青"]

    raw = await service.results(result["run_id"], raw=True)
    assert raw["records"][0]["source_keywords"] == ["海优", "优青"]

    comments = await service.results(result["run_id"], kind="comments")
    assert [record["id"] for record in comments["records"]] == ["c1", "c2"]
    assert comments["duplicates_removed"] == 1


async def test_results_are_normalized_and_paged(service: CrawlerService) -> None:
    started = await service.crawl(search(comments=True), wait_seconds=20)
    run_id = started["run_id"]

    page = await service.results(run_id, limit=2)
    assert page["total"] == 3
    assert page["returned"] == 2
    assert page["next_offset"] == 2
    assert page["records"][0]["id"] == "n1"
    assert page["records"][0]["likes"] == 1177
    assert page["records"][0]["url"].endswith("xsec_token=tok")
    assert len(page["files"]) == 1

    last = await service.results(run_id, offset=2, limit=2)
    assert last["returned"] == 1
    assert last["next_offset"] is None

    comments = await service.results(run_id, kind="comments")
    assert comments["records"][0]["text"] == "评论"

    raw = await service.results(run_id, raw=True, limit=1)
    assert raw["records"][0]["note_id"] == "n1"


async def test_creator_targets_reach_zhihu_list(service: CrawlerService) -> None:
    request = CrawlRequest.create(
        platform="zhihu", mode="creator", targets=["https://www.zhihu.com/people/x"]
    )
    result = await service.crawl(request, wait_seconds=20)
    assert result["outcome"] == "data_available"
    assert fake_config(service, result["run_id"])["ZHIHU_CREATOR_URL_LIST"] == [
        "https://www.zhihu.com/people/x"
    ]
    page = await service.results(result["run_id"])
    assert page["records"][0]["likes"] == 42


async def test_logs_are_redacted_and_long_lines_capped(service: CrawlerService) -> None:
    result = await service.crawl(search(), wait_seconds=20)
    assert result["outcome"] == "data_available"
    entries = service.store.tail_logs(result["run_id"], 1000)
    messages = " ".join(str(entry["message"]) for entry in entries)
    assert "session-cookie" not in messages
    assert "log-token" not in messages
    long_line = next(str(e["message"]) for e in entries if "xhs note" in str(e["message"]))
    assert long_line.endswith("...[truncated]")
    assert len(long_line) < 2_100


@pytest.mark.parametrize(
    ("keyword", "outcome"),
    [("fail", "failed"), ("no-data", "no_data"), ("soft-error", "failed")],
)
async def test_unsuccessful_outcomes(service: CrawlerService, keyword: str, outcome: str) -> None:
    result = await service.crawl(search(keyword), wait_seconds=20)
    assert result["state"] == "completed"
    assert result["outcome"] == outcome


async def test_qr_attention_busy_and_stop(service: CrawlerService) -> None:
    started = await service.crawl(search("hang-qr"), wait_seconds=0)
    run_id = started["run_id"]

    waiting = await wait_for_attention(service, run_id)
    assert waiting["attention"]["phase"] == "awaiting_user_login"
    assert waiting["state"] == "running"

    with pytest.raises(ToolError) as busy:
        await service.crawl(search(), wait_seconds=0)
    assert busy.value.code == "BUSY"
    assert busy.value.run_id == run_id

    pid = service.store.load(run_id)["pid"]
    stopped = await service.stop(run_id)
    assert stopped["state"] == "completed"
    assert stopped["outcome"] == "cancelled"
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE

    again = await service.stop(run_id)
    assert again["already_stopped"] is True


async def test_timeout_stops_run(service: CrawlerService) -> None:
    request = search("hang")
    # timeout_minutes is validated as >= 1; shrink it after validation for the test.
    object.__setattr__(request, "timeout_minutes", 0.02)
    result = await service.crawl(request, wait_seconds=20)
    assert result["outcome"] == "timed_out"


async def test_status_defaults_to_latest_and_runs_lists(service: CrawlerService) -> None:
    first = await service.crawl(search("no-data"), wait_seconds=20)
    second = await service.crawl(search(), wait_seconds=20)
    latest = await service.status()
    assert latest["run_id"] == second["run_id"]
    listed = await service.runs()
    assert [item["run_id"] for item in listed["runs"]] == [second["run_id"], first["run_id"]]
    assert listed["runs"][0]["keywords"] == ["海优"]


async def test_unknown_run_is_reported(service: CrawlerService) -> None:
    with pytest.raises(ToolError) as error:
        await service.status("mc_20260101_000000_000_abcd")
    assert error.value.code == "RUN_NOT_FOUND"
