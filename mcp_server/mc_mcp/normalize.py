"""Map each platform's upstream record fields onto one shared shape.

Field names come from MediaCrawler's store/*/__init__.py and model/*.py; the
first candidate present in a record wins.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

TEXT_LIMIT = 500

CONTENT_FIELDS: dict[str, tuple[str, ...]] = {
    "id": ("note_id", "aweme_id", "video_id", "content_id"),
    "type": ("type", "aweme_type", "video_type", "content_type"),
    "title": ("title",),
    "text": ("desc", "content_text", "content"),
    "author": ("nickname", "user_nickname"),
    "time": ("time", "create_time", "created_time", "publish_time"),
    "likes": ("liked_count", "voteup_count"),
    "comments": ("comment_count", "video_comment", "comments_count", "total_replay_num"),
    "collects": ("collected_count", "video_favorite_count"),
    "shares": ("share_count", "video_share_count", "shared_count"),
    "views": ("video_play_count", "viewd_count"),
    "url": ("note_url", "aweme_url", "video_url", "content_url"),
    "keyword": ("source_keyword",),
}

COMMENT_FIELDS: dict[str, tuple[str, ...]] = {
    "id": ("comment_id",),
    "parent_id": ("parent_comment_id",),
    "content_id": ("note_id", "aweme_id", "video_id", "content_id"),
    "text": ("content",),
    "author": ("nickname", "user_nickname"),
    "time": ("create_time", "publish_time"),
    "likes": ("like_count", "comment_like_count"),
    "replies": ("sub_comment_count",),
}

_FIELD_MAPS = {"contents": CONTENT_FIELDS, "comments": COMMENT_FIELDS}


def _format_time(value: Any) -> Any:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return value
    if number > 10**12:
        number //= 1000
    if number < 10**9:
        return value
    return datetime.fromtimestamp(number).strftime("%Y-%m-%d %H:%M")


def _format_number(value: Any) -> Any:
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return value


def _truncate(value: Any) -> Any:
    if isinstance(value, str) and len(value) > TEXT_LIMIT:
        return value[:TEXT_LIMIT] + "...[truncated]"
    return value


def normalize(record: dict[str, Any], record_type: str) -> dict[str, Any]:
    """Return the shared view of a record, or the record unchanged for other types."""
    fields = _FIELD_MAPS.get(record_type)
    if fields is None:
        return record
    result: dict[str, Any] = {}
    for name, candidates in fields.items():
        value = next(
            (record[key] for key in candidates if record.get(key) not in (None, "")),
            None,
        )
        if value is None:
            continue
        if name == "time":
            value = _format_time(value)
        elif name in {"likes", "comments", "collects", "shares", "views", "replies"}:
            value = _format_number(value)
        elif name in {"text", "title"}:
            value = _truncate(value)
        result[name] = value
    # Weibo and dy have no separate title; avoid repeating the text.
    if result.get("title") == result.get("text"):
        result.pop("title", None)
    return result
