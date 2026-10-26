# -*- coding: utf-8 -*-
"""
知乎回归测试:get_note_all_comments 必须遵守 CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES。

覆盖:
1. 每页 10 条、max_count=15 时只返回/回调 15 条一级评论,且不再请求多余页面。
2. max_count 大于可用评论数时,在 is_end 处停止。
3. 楼中楼只为保留下来的一级评论抓取。
"""

import pytest

import config
from media_platform.zhihu.client import ZhiHuClient
from media_platform.zhihu.help import ZhihuExtractor
from model.m_zhihu import ZhihuContent

PAGE_SIZE = 10


def _make_client(total_pages, child_count=0):
    client = object.__new__(ZhiHuClient)
    client._extractor = ZhihuExtractor()
    root_calls = []
    child_calls = []

    async def get_root_comments(content_id, content_type, offset="", limit=10, order_by="score"):
        page = len(root_calls)
        root_calls.append(offset)
        is_end = page >= total_pages - 1
        data = [
            {
                "type": "comment",
                "id": page * PAGE_SIZE + i,
                "reply_comment_id": "0",
                "content": f"c{page * PAGE_SIZE + i}",
                "created_time": 0,
                "child_comment_count": child_count,
                "author": None,
            }
            for i in range(PAGE_SIZE)
        ]
        next_url = f"https://www.zhihu.com/api/v4/comment_v5/answers/1/root_comment?limit=10&offset=off{page + 1}"
        return {"paging": {"is_end": is_end, "next": next_url}, "data": data}

    async def get_child_comments(root_comment_id, offset="", limit=10, order_by="sort"):
        child_calls.append(root_comment_id)
        return {
            "paging": {"is_end": True, "next": "https://www.zhihu.com/x?offset=end"},
            "data": [
                {
                    "type": "comment",
                    "id": f"sub-{root_comment_id}",
                    "reply_comment_id": root_comment_id,
                    "content": "sub",
                    "created_time": 0,
                    "child_comment_count": 0,
                    "author": None,
                }
            ],
        }

    client.get_root_comments = get_root_comments
    client.get_child_comments = get_child_comments
    return client, root_calls, child_calls


def _content():
    return ZhihuContent(content_id="1", content_type="answer")


@pytest.mark.asyncio
async def test_root_comments_capped_at_max_count_and_paging_stops(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_GET_SUB_COMMENTS", False)
    client, root_calls, _ = _make_client(total_pages=5)
    stored = []

    async def callback(comments):
        stored.extend(comments)

    result = await client.get_note_all_comments(
        content=_content(), crawl_interval=0, callback=callback, max_count=15
    )

    assert len(result) == 15
    assert len(stored) == 15
    assert [c.comment_id for c in stored] == [str(i) for i in range(15)]
    # 第 2 页已凑满 15 条,不能再请求第 3 页
    assert len(root_calls) == 2


@pytest.mark.asyncio
async def test_max_count_larger_than_available_stops_at_is_end(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_GET_SUB_COMMENTS", False)
    client, root_calls, _ = _make_client(total_pages=2)
    stored = []

    async def callback(comments):
        stored.extend(comments)

    result = await client.get_note_all_comments(
        content=_content(), crawl_interval=0, callback=callback, max_count=100
    )

    assert len(result) == 20
    assert len(stored) == 20
    assert len(root_calls) == 2


@pytest.mark.asyncio
async def test_sub_comments_only_fetched_for_kept_comments(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_GET_SUB_COMMENTS", True)
    client, root_calls, child_calls = _make_client(total_pages=5, child_count=1)
    stored = []

    async def callback(comments):
        stored.extend(comments)

    result = await client.get_note_all_comments(
        content=_content(), crawl_interval=0, callback=callback, max_count=15
    )

    assert len(result) == 15
    assert child_calls == [str(i) for i in range(15)]
    roots = [c for c in stored if not c.comment_id.startswith("sub-")]
    subs = [c for c in stored if c.comment_id.startswith("sub-")]
    assert len(roots) == 15
    assert len(subs) == 15
    assert len(root_calls) == 2
