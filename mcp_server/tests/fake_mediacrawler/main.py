"""Fake MediaCrawler entry point for the MCP server tests.

The first keyword or target selects a behaviour (hang, fail, no-data, ...);
anything else writes a few records shaped like the real platform's output.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import config

CONFIG_NAMES = (
    "KEYWORDS",
    "SORT_TYPE",
    "WEIBO_SEARCH_TYPE",
    "PUBLISH_TIME_TYPE",
    "BILI_SEARCH_MODE",
    "START_DAY",
    "END_DAY",
    "MAX_NOTES_PER_DAY",
    "ENABLE_CDP_MODE",
    "CDP_CONNECT_EXISTING",
    "CDP_DEBUG_PORT",
    "XHS_SPECIFIED_NOTE_URL_LIST",
    "TIEBA_SPECIFIED_ID_LIST",
    "TIEBA_CREATOR_URL_LIST",
    "ZHIHU_CREATOR_URL_LIST",
)

CONTENTS = {
    "xhs": {
        "note_id": "n1",
        "type": "normal",
        "title": "海优答疑",
        "desc": "正文",
        "time": 1780000000000,
        "nickname": "小***",
        "liked_count": "1177",
        "collected_count": "756",
        "comment_count": "244",
        "share_count": "10",
        "note_url": "https://www.xiaohongshu.com/explore/n1?xsec_token=tok",
        "source_keyword": "海优",
    },
    "dy": {
        "aweme_id": "a1",
        "aweme_type": "0",
        "title": "抖音视频",
        "desc": "抖音视频",
        "create_time": 1780000000,
        "nickname": "d***",
        "liked_count": "5",
        "collected_count": "2",
        "comment_count": "1",
        "share_count": "0",
        "aweme_url": "https://www.douyin.com/video/a1",
    },
    "zhihu": {
        "content_id": "z1",
        "content_type": "answer",
        "title": "问题",
        "content_text": "回答",
        "content_url": "https://www.zhihu.com/answer/z1",
        "created_time": 1780000000,
        "voteup_count": 42,
        "comment_count": 3,
        "user_nickname": "z***",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fake MediaCrawler CLI")
    for name in (
        "--platform",
        "--lt",
        "--type",
        "--start",
        "--get_comment",
        "--get_sub_comment",
        "--get_media",
        "--headless",
        "--save_data_option",
        "--crawler_max_notes_count",
        "--max_comments_count_singlenotes",
        "--max_concurrency_num",
        "--save_data_path",
        "--enable_ip_proxy",
    ):
        parser.add_argument(name, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    seen = {name: getattr(config, name) for name in CONFIG_NAMES}
    print("fake config " + json.dumps(seen, ensure_ascii=False), flush=True)
    print(f"fake args max={args.crawler_max_notes_count} comments={args.get_comment}", flush=True)
    print("cookie=session-cookie token=log-token", flush=True)
    # Real MediaCrawler logs whole posts on one line; keep one longer than 64 KiB.
    print("MediaCrawler INFO - xhs note: " + "长" * 100_000, flush=True)

    targets = (
        config.XHS_SPECIFIED_NOTE_URL_LIST
        + config.TIEBA_SPECIFIED_ID_LIST
        + config.TIEBA_CREATOR_URL_LIST
        + config.ZHIHU_CREATOR_URL_LIST
    )
    control = config.KEYWORDS.split(",")[0] if config.KEYWORDS else (targets[0] if targets else "")

    if control.startswith("hang-qr"):
        print("MediaCrawler INFO - Waiting for scan code login, remaining time is 20s", flush=True)
    if control.startswith("hang"):
        print("waiting until the server stops this process", flush=True)
        while True:
            time.sleep(0.1)
    if control == "fail":
        print("requested failure", flush=True)
        return 7
    if control == "no-data":
        print("completed without records", flush=True)
        return 0
    if control == "soft-error":
        print("MediaCrawler ERROR - upstream request failed", flush=True)
        return 0

    output_dir = Path(args.save_data_path) / args.platform / "jsonl"
    output_dir.mkdir(parents=True, exist_ok=True)
    content = CONTENTS.get(args.platform, CONTENTS["xhs"])
    count = 3
    with (output_dir / f"{args.type}_contents_2026-10-25.jsonl").open("w", encoding="utf-8") as handle:
        for index in range(count):
            handle.write(json.dumps({**content, "title": f"{content['title']} {index}"}, ensure_ascii=False) + "\n")
    if args.get_comment == "true":
        with (output_dir / f"{args.type}_comments_2026-10-25.jsonl").open("w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "comment_id": "c1",
                        "note_id": "n1",
                        "content": "评论",
                        "nickname": "c***",
                        "create_time": 1780000000000,
                        "like_count": "7",
                        "sub_comment_count": "2",
                        "parent_comment_id": "",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    print("MediaCrawler INFO - Crawler finished ...", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
