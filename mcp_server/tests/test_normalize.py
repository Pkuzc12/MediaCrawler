from __future__ import annotations

from mc_mcp.normalize import TEXT_LIMIT, normalize


def test_xhs_content() -> None:
    record = {
        "note_id": "n1",
        "type": "normal",
        "title": "标题",
        "desc": "正文",
        "time": 1780000000000,
        "nickname": "小***",
        "liked_count": "1177",
        "collected_count": "756",
        "comment_count": "244",
        "share_count": "10",
        "note_url": "https://www.xiaohongshu.com/explore/n1?xsec_token=tok",
        "xsec_token": "tok",
    }
    result = normalize(record, "contents")
    assert result["id"] == "n1"
    assert result["title"] == "标题"
    assert result["text"] == "正文"
    assert result["likes"] == 1177
    assert result["collects"] == 756
    assert result["url"].endswith("xsec_token=tok")
    assert result["time"].startswith("2026-")
    assert "xsec_token" not in result


def test_platform_specific_names() -> None:
    bili = normalize(
        {
            "video_id": "1",
            "title": "t",
            "desc": "d",
            "create_time": 1780000000,
            "liked_count": "3",
            "video_comment": "4",
            "video_favorite_count": "5",
            "video_share_count": "6",
            "video_play_count": "7",
            "video_url": "https://www.bilibili.com/video/av1",
        },
        "contents",
    )
    assert (bili["comments"], bili["collects"], bili["shares"], bili["views"]) == (4, 5, 6, 7)

    weibo = normalize(
        {"note_id": "w", "content": "微博正文", "comments_count": "2", "shared_count": "1"},
        "contents",
    )
    assert weibo["text"] == "微博正文"
    assert weibo["comments"] == 2
    assert weibo["shares"] == 1

    tieba = normalize(
        {"note_id": "t", "title": "帖", "user_nickname": "u", "publish_time": "2026-10-01 10:00", "total_replay_num": 9},
        "contents",
    )
    assert tieba["author"] == "u"
    assert tieba["time"] == "2026-10-01 10:00"
    assert tieba["comments"] == 9


def test_douyin_title_equal_to_text_is_dropped() -> None:
    result = normalize({"aweme_id": "a", "title": "同一段", "desc": "同一段"}, "contents")
    assert "title" not in result
    assert result["text"] == "同一段"


def test_comment_and_truncation() -> None:
    result = normalize(
        {"comment_id": "c", "note_id": "n", "content": "x" * (TEXT_LIMIT + 10), "like_count": "7"},
        "comments",
    )
    assert result["content_id"] == "n"
    assert result["likes"] == 7
    assert result["text"].endswith("...[truncated]")


def test_other_kinds_pass_through() -> None:
    record = {"user_id": "u", "fans": 3}
    assert normalize(record, "creators") is record
