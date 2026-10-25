from __future__ import annotations

import pytest

from mc_mcp.errors import ToolError
from mc_mcp.models import CrawlRequest


def search(**overrides: object) -> CrawlRequest:
    values: dict[str, object] = {"platform": "xhs", "mode": "search", "keywords": ["海优"]}
    values.update(overrides)
    return CrawlRequest.create(**values)


def test_search_overrides_join_keywords_and_map_sort() -> None:
    request = search(keywords=["海优", "优青"], sort="latest")
    assert request.config_overrides() == {
        "KEYWORDS": "海优,优青",
        "SORT_TYPE": "time_descending",
    }


def test_single_keyword_string_is_accepted() -> None:
    assert search(keywords="海优").keywords == ("海优",)


def test_weibo_and_douyin_filters() -> None:
    assert search(platform="wb", sort="realtime").config_overrides()["WEIBO_SEARCH_TYPE"] == "real_time"
    assert search(platform="dy", publish_within="week").config_overrides()["PUBLISH_TIME_TYPE"] == 7


def test_bili_date_range_spreads_budget_over_days() -> None:
    overrides = search(
        platform="bili", date_from="2026-10-01", date_to="2026-10-10", max_items=25
    ).config_overrides()
    assert overrides["BILI_SEARCH_MODE"] == "all_in_time_range"
    assert overrides["START_DAY"] == "2026-10-01"
    assert overrides["END_DAY"] == "2026-10-10"
    assert overrides["MAX_NOTES_PER_DAY"] == 3


@pytest.mark.parametrize(
    "overrides",
    [
        {"sort": "latest", "platform": "ks"},
        {"sort": "hot"},
        {"publish_within": "week"},
        {"date_from": "2026-10-01", "date_to": "2026-10-02"},
        {"platform": "bili", "date_from": "2026-10-01"},
        {"platform": "bili", "date_from": "2026-10-05", "date_to": "2026-10-01"},
        {"platform": "bili", "date_from": "2025-01-01", "date_to": "2026-10-01"},
        {"keywords": ["a,b"]},
        {"keywords": ["a", "b", "c", "d", "e", "f"]},
        {"keywords": ["a", "b"], "max_items": 60},
        {"max_items": 101},
        {"sub_comments": True},
        {"platform": "douyin"},
        {"targets": ["x"]},
    ],
)
def test_invalid_search_requests_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ToolError) as error:
        search(**overrides)
    assert error.value.code == "INVALID_REQUEST"


def test_detail_and_creator_targets_go_to_platform_lists() -> None:
    detail = CrawlRequest.create(
        platform="tieba", mode="detail", targets=["https://tieba.baidu.com/p/123?pn=2"]
    )
    assert detail.config_overrides() == {"TIEBA_SPECIFIED_ID_LIST": ["123"]}
    creator = CrawlRequest.create(
        platform="zhihu", mode="creator", targets=["https://www.zhihu.com/people/x"]
    )
    assert creator.config_overrides() == {
        "ZHIHU_CREATOR_URL_LIST": ["https://www.zhihu.com/people/x"]
    }


def test_filters_are_rejected_outside_search() -> None:
    with pytest.raises(ToolError):
        CrawlRequest.create(platform="xhs", mode="detail", targets=["u"], sort="latest")


def test_creator_mode_limits_targets() -> None:
    with pytest.raises(ToolError):
        CrawlRequest.create(platform="xhs", mode="creator", targets=[str(i) for i in range(6)])


def test_warnings_mention_page_floor() -> None:
    assert any("whole pages" in item for item in search(max_items=5).warnings())
