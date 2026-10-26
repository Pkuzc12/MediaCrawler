# -*- coding: utf-8 -*-
"""
回归测试(小红书 xhs):window.__INITIAL_STATE__ 解析失败不能击穿整次采集。

背景:
extract_note_detail_from_html 原先对整段 state 做 .replace("undefined", '""'),
笔记正文里出现单词 "undefined" 时会被改坏成非法 JSON,json.loads 抛 JSONDecodeError;
client 层的 tenacity 重试后变成 RetryError,而 core 层没有在 HTML 兜底分支捕获它,
异常穿过 asyncio.gather 直接中断整次 search,同页已抓到但未入库的笔记全部丢失。

覆盖:
1. state 归一化:字符串内 "undefined" 保持原样;值位置的 undefined/NaN/Infinity -> null;
   new Set([...]) / new Map([...]) 解包;state 之后的尾随 <script> 内容被忽略。
2. 无法解析的 state 返回 None 并记录 warning(含 note_id 与出错位置上下文),不抛异常。
3. 创作者主页信息解析仍正常。
4. get_note_detail_async_task 在 HTML 兜底抛 RetryError / 任意异常时返回 None。
"""

import asyncio
import json
import logging
from unittest.mock import AsyncMock

import pytest
from tenacity import RetryError, Future

from media_platform.xhs.core import XiaoHongShuCrawler
from media_platform.xhs.extractor import XiaoHongShuExtractor

NOTE_ID = "6600000000000000000000aa"


def make_note_html(note_body: str, trailing: str = "") -> str:
    """Build a minimal note detail page whose state embeds `note_body` as the note object."""
    state = (
        '{"global":{"appSettings":{"notificationInterval":30}},'
        '"note":{"currentNoteId":"' + NOTE_ID + '",'
        '"noteDetailMap":{"' + NOTE_ID + '":{"comments":{"list":[],"cursor":""},'
        '"currentTime":1700000000000,"note":' + note_body + "}}}}"
    )
    return (
        "<html><head></head><body>"
        "<script>window.__INITIAL_STATE__=" + state + "</script>" + trailing + "</body></html>"
    )


@pytest.fixture
def extractor():
    return XiaoHongShuExtractor()


def test_word_undefined_inside_note_text_is_preserved(extractor):
    # 旧实现会把字符串里的 undefined 换成 "" -> "title":"x is ""..." 非法 JSON
    body = (
        '{"noteId":"' + NOTE_ID + '","title":"why is it undefined?",'
        '"desc":"a:undefined,b \\"undefined\\" [undefined]","type":"normal"}'
    )
    note = extractor.extract_note_detail_from_html(NOTE_ID, make_note_html(body))

    assert note is not None
    assert note["note_id"] == NOTE_ID
    assert note["title"] == "why is it undefined?"
    assert note["desc"] == 'a:undefined,b "undefined" [undefined]'


def test_bare_undefined_values_become_none(extractor):
    body = (
        '{"noteId":"' + NOTE_ID + '","video":undefined,'
        '"tagList":[undefined, {"name":"t"},undefined],"ipLocation" : undefined }'
    )
    note = extractor.extract_note_detail_from_html(NOTE_ID, make_note_html(body))

    assert note["video"] is None
    assert note["tag_list"] == [None, {"name": "t"}, None]
    assert note["ip_location"] is None


def test_nan_and_infinity_become_none(extractor):
    body = (
        '{"noteId":"' + NOTE_ID + '","ratio":NaN,"maxVal":Infinity,'
        '"minVal":-Infinity,"list":[NaN,1,Infinity],"desc":"NaN Infinity stay"}'
    )
    note = extractor.extract_note_detail_from_html(NOTE_ID, make_note_html(body))

    assert note["ratio"] is None
    assert note["max_val"] is None
    assert note["min_val"] is None
    assert note["list"] == [None, 1, None]
    assert note["desc"] == "NaN Infinity stay"


def test_new_set_and_map_literals_are_unwrapped(extractor):
    body = (
        '{"noteId":"' + NOTE_ID + '","seen":new Set(["a","b"]),'
        '"empty":new Set(),"pairs":new Map([["k",1]]),'
        '"desc":"text with new Set([1]) and (parens)"}'
    )
    note = extractor.extract_note_detail_from_html(NOTE_ID, make_note_html(body))

    assert note["seen"] == ["a", "b"]
    assert note["empty"] == []
    assert note["pairs"] == [["k", 1]]
    assert note["desc"] == "text with new Set([1]) and (parens)"


def test_trailing_script_after_state_is_ignored(extractor):
    # 贪婪正则会把同一行后面的 <script>...{...}</script> 一并捕获
    body = '{"noteId":"' + NOTE_ID + '","title":"ok"}'
    trailing = '<script>window.__SSR__={"a":1};(function(){var x={b:2}})()</script>'
    note = extractor.extract_note_detail_from_html(NOTE_ID, make_note_html(body, trailing))

    assert note["title"] == "ok"


def test_malformed_state_returns_none_and_logs_context(extractor, caplog):
    body = '{"noteId":"' + NOTE_ID + '","title":"ok","broken":@@BAD@@}'
    with caplog.at_level(logging.WARNING, logger="MediaCrawler"):
        note = extractor.extract_note_detail_from_html(NOTE_ID, make_note_html(body))

    assert note is None
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert NOTE_ID in messages
    assert "pos:" in messages
    assert "@@BAD@@" in messages


def test_missing_note_in_detail_map_returns_none(extractor):
    html = make_note_html('{"noteId":"' + NOTE_ID + '"}')
    assert extractor.extract_note_detail_from_html("other-note-id", html) is None


def test_no_note_detail_map_returns_none(extractor):
    assert extractor.extract_note_detail_from_html(NOTE_ID, "<html>captcha</html>") is None


def test_creator_info_extraction(extractor):
    state = {
        "user": {
            "userPageData": {
                "basicInfo": {"nickname": "undefined fan", "desc": "NaN (Infinity)"},
                "interactions": [{"type": "fans", "count": "10"}],
            }
        }
    }
    raw = json.dumps(state, ensure_ascii=False)
    # 注入 JS 字面量和尾随脚本,模拟真实页面
    raw = raw.replace('"user": {', '"seen": new Set(["x"]), "gone": undefined, "user": {', 1)
    html = (
        "<html><script>window.__INITIAL_STATE__=" + raw + "</script>"
        '<script>var y={"z":1}</script></html>'
    )

    info = extractor.extract_creator_info_from_html(html)

    assert info == state["user"]["userPageData"]


def test_creator_info_malformed_returns_none(extractor):
    html = '<html><script>window.__INITIAL_STATE__={"user":@@}</script></html>'
    assert extractor.extract_creator_info_from_html(html) is None


# --------------------------------------------------------------------------- core


def make_crawler(xhs_client):
    crawler = XiaoHongShuCrawler.__new__(XiaoHongShuCrawler)
    crawler.xhs_client = xhs_client
    return crawler


def make_retry_error(exc: BaseException) -> RetryError:
    attempt = Future(attempt_number=3)
    attempt.set_exception(exc)
    return RetryError(attempt)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        make_retry_error(json.JSONDecodeError("Expecting value", "{x}", 1)),
        RuntimeError("unexpected"),
    ],
)
async def test_note_detail_task_skips_html_fallback_failure(error):
    xhs_client = AsyncMock()
    xhs_client.get_note_by_id.return_value = None
    xhs_client.get_note_by_id_from_html.side_effect = error

    result = await make_crawler(xhs_client).get_note_detail_async_task(
        note_id="n1",
        xsec_source="pc_search",
        xsec_token="token",
        semaphore=asyncio.Semaphore(1),
    )

    assert result is None
    xhs_client.get_note_by_id_from_html.assert_awaited_once()


@pytest.mark.asyncio
async def test_gather_survives_single_unparsable_note(monkeypatch):
    """一条笔记 HTML 兜底解析失败不能让同批次其他笔记的结果一起丢掉。"""
    monkeypatch.setattr("config.CRAWLER_MAX_SLEEP_SEC", 0, raising=False)
    xhs_client = AsyncMock()

    async def get_note_by_id(note_id, xsec_source, xsec_token):
        return None if note_id == "bad" else {"note_id": note_id}

    xhs_client.get_note_by_id.side_effect = get_note_by_id
    xhs_client.get_note_by_id_from_html.side_effect = make_retry_error(
        json.JSONDecodeError("Expecting value", "{x}", 1)
    )
    crawler = make_crawler(xhs_client)

    semaphore = asyncio.Semaphore(2)
    results = await asyncio.gather(
        *[
            crawler.get_note_detail_async_task(
                note_id=note_id,
                xsec_source="pc_search",
                xsec_token="token",
                semaphore=semaphore,
            )
            for note_id in ("ok1", "bad", "ok2")
        ]
    )

    assert [r.get("note_id") if r else None for r in results] == ["ok1", None, "ok2"]


@pytest.mark.asyncio
async def test_note_detail_task_propagates_cancellation():
    xhs_client = AsyncMock()
    xhs_client.get_note_by_id.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await make_crawler(xhs_client).get_note_detail_async_task(
            note_id="n1",
            xsec_source="pc_search",
            xsec_token="token",
            semaphore=asyncio.Semaphore(1),
        )
