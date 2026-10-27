# -*- coding: utf-8 -*-
"""多镜像择优：开局测速、途中换慢镜像、换镜像后接着下。

本地测试服务器的 ``/mirror/<name>`` 路由用 query 区分镜像：路径相同即同一个文件
（``mirror_resume`` 按路径共用临时文件），``speed=slow`` 的镜像每 256 字节停顿一下。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from media_downloader import MediaDownloader, MediaItem, MediaType
from tests.media_server import MediaTestServer, start_server

BIG = bytes(range(256)) * 256  # 64 KiB
MEDIUM = bytes(range(256)) * 64  # 16 KiB


@pytest.fixture(scope="module")
def server():
    srv, _thread = start_server()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture(autouse=True)
def _reset_server(server: MediaTestServer):
    server.reset()
    yield


def make_downloader(tmp_path: Path, **overrides) -> MediaDownloader:
    params = dict(
        platform="bili",
        base_dir=tmp_path,
        max_retries=2,
        retry_base_delay=0.001,
        retry_max_delay=0.005,
        max_candidates=8,
        mirror_resume=True,
    )
    params.update(overrides)
    return MediaDownloader(**params)


def make_item(server: MediaTestServer, name: str, *mirrors: str, content_id: str = "BV1") -> MediaItem:
    urls = [server.url(f"/mirror/{name}?{query}") for query in mirrors]
    return MediaItem(
        url=urls[0],
        backup_urls=tuple(urls[1:]),
        media_type=MediaType.VIDEO,
        content_id=content_id,
        stem="video",
        extension=".m4s",
    )


def ranges(server: MediaTestServer) -> list[tuple[str, str]]:
    """每个请求的 (镜像 query, Range)"""
    return [(record.path.split("?", 1)[1], record.headers.get("range")) for record in server.requests]


@pytest.mark.asyncio
async def test_race_picks_fastest_mirror_and_keeps_its_bytes(server, tmp_path):
    server.set_content("race-big", BIG)
    downloader = make_downloader(tmp_path, race_candidates=2, race_seconds=0.5, race_max_bytes=1024)
    item = make_item(server, "race-big", "speed=slow", "speed=fast")

    result = await downloader.download(item)

    assert result is not None
    assert result.read_bytes() == BIG
    requested = ranges(server)
    # 两个镜像同时从 0 开始赛跑，之后只在赢家（fast）上从赛跑收到的字节处接着下
    assert sorted(requested[:2]) == [("speed=fast", "bytes=0-"), ("speed=slow", "bytes=0-")]
    (mirror, range_header), = requested[2:]
    assert mirror == "speed=fast"
    # 读到 race_max_bytes 时按数据块截止，可能略多于 1024 字节，但都已保留
    resumed_at = int(range_header.removeprefix("bytes=").rstrip("-"))
    assert 1024 <= resumed_at < len(BIG)


@pytest.mark.asyncio
async def test_race_measures_speed_from_first_byte_not_request_start(server, tmp_path):
    """回归：国内 upos 镜像首字节要 1-2 秒，测速若从发请求算起，会把延迟当成慢、
    选中首字节快但持续传输慢的镜像（2026-10-27 实测三个镜像都被测成 0-11 MB/min）"""
    server.set_content("latency", BIG)
    downloader = make_downloader(tmp_path, race_candidates=2, race_seconds=0.3, race_max_bytes=1024)
    item = make_item(server, "latency", "speed=slow&pause=0.05", "speed=fast&delay=0.6")

    result = await downloader.download(item)

    assert result is not None
    assert result.read_bytes() == BIG
    assert [mirror for mirror, _ in ranges(server)[2:]] == ["speed=fast&delay=0.6"]


@pytest.mark.asyncio
async def test_race_winner_that_finishes_the_file_is_used_directly(server, tmp_path):
    server.set_content("race-small", MEDIUM)
    downloader = make_downloader(tmp_path, race_candidates=2, race_seconds=0.5)
    item = make_item(server, "race-small", "speed=slow", "speed=fast")

    result = await downloader.download(item)

    assert result is not None
    assert result.read_bytes() == MEDIUM
    assert server.request_count == 2, "赛跑时就下完了，不应再发请求"


@pytest.mark.asyncio
async def test_slow_mirror_is_abandoned_and_next_mirror_resumes(server, tmp_path):
    server.set_content("switch", MEDIUM)
    downloader = make_downloader(tmp_path, slow_min_rate=100 * 1024, slow_window=0.2)
    item = make_item(server, "switch", "speed=slow", "speed=fast")

    result = await downloader.download(item)

    assert result is not None
    assert result.read_bytes() == MEDIUM
    (first, first_range), (second, second_range) = ranges(server)
    assert (first, first_range) == ("speed=slow", "bytes=0-")
    assert second == "speed=fast"
    resumed_at = int(second_range.removeprefix("bytes=").rstrip("-"))
    assert 0 < resumed_at < len(MEDIUM), "应从慢镜像已下到的位置接着下，而不是从头再来"


@pytest.mark.asyncio
async def test_last_mirror_is_never_abandoned_for_being_slow(server, tmp_path):
    server.set_content("only-slow", MEDIUM)
    downloader = make_downloader(tmp_path, slow_min_rate=100 * 1024, slow_window=0.2)
    item = make_item(server, "only-slow", "speed=slow&pause=0.01")

    result = await downloader.download(item)

    assert result is not None
    assert result.read_bytes() == MEDIUM
    assert server.request_count == 1


@pytest.mark.asyncio
async def test_mirror_with_different_total_restarts_instead_of_appending(server, tmp_path):
    """换到的镜像报告的总长不同（不是同一个文件），不能把它的内容接到旧片段后面"""
    server.set_content("mismatch", MEDIUM)
    downloader = make_downloader(tmp_path, slow_min_rate=100 * 1024, slow_window=0.2)
    item = make_item(server, "mismatch", "speed=slow", "speed=fast&extra=100")

    result = await downloader.download(item)

    assert result is not None
    assert result.read_bytes() == MEDIUM + b"x" * 100, "结果必须完整来自同一个镜像"
    assert ranges(server)[-1] == ("speed=fast&extra=100", "bytes=0-")


@pytest.mark.asyncio
async def test_recent_mirror_speeds_skip_the_next_race(server, tmp_path):
    server.set_content("memo-a", MEDIUM)
    server.set_content("memo-b", MEDIUM)
    downloader = make_downloader(tmp_path, race_candidates=2, race_seconds=0.5)

    assert await downloader.download(make_item(server, "memo-a", "speed=slow", "speed=fast")) is not None
    raced = server.request_count
    assert raced == 2

    second = make_item(server, "memo-b", "speed=slow", "speed=fast", content_id="BV2")
    assert await downloader.download(second) is not None
    # 两个参赛镜像的域名刚测过速，直接按已知速度挑，不再赛跑
    assert server.request_count - raced == 1


@pytest.mark.asyncio
async def test_defaults_keep_strict_order_without_racing(server, tmp_path):
    """择优默认关闭：其他平台仍按顺序只请求第一个可用地址"""
    server.set_content("plain", MEDIUM)
    downloader = MediaDownloader(platform="xhs", base_dir=tmp_path)
    item = make_item(server, "plain", "speed=slow&pause=0.001", "speed=fast")

    assert await downloader.download(item) is not None
    assert ranges(server) == [("speed=slow&pause=0.001", "bytes=0-")]
