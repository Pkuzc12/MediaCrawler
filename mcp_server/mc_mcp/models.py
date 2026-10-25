from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from .errors import ToolError

PLATFORMS = ("xhs", "dy", "ks", "bili", "wb", "tieba", "zhihu")
MODES = ("search", "detail", "creator")
MAX_TOTAL_ITEMS = 100
MAX_KEYWORDS = 5
MAX_TARGETS = 20
MAX_CREATORS = 5
MAX_DATE_SPAN_DAYS = 366

# Search pages are fetched whole, so a run may return more than max_items.
SEARCH_PAGE_FLOORS = {
    "xhs": 20,
    "dy": 10,
    "ks": 20,
    "bili": 20,
    "wb": 10,
    "tieba": 10,
    "zhihu": 20,
}

# Tool value -> upstream config value, for platforms whose search order is configurable.
SORT_OPTIONS: dict[str, tuple[str, dict[str, str]]] = {
    "xhs": (
        "SORT_TYPE",
        {
            "general": "general",
            "popular": "popularity_descending",
            "latest": "time_descending",
        },
    ),
    "wb": (
        "WEIBO_SEARCH_TYPE",
        {
            "default": "default",
            "realtime": "real_time",
            "popular": "popular",
            "video": "video",
        },
    ),
}
PUBLISH_WITHIN_OPTIONS = {"day": 1, "week": 7, "half_year": 180}

DETAIL_ATTRIBUTES = {
    "xhs": "XHS_SPECIFIED_NOTE_URL_LIST",
    "dy": "DY_SPECIFIED_ID_LIST",
    "ks": "KS_SPECIFIED_ID_LIST",
    "bili": "BILI_SPECIFIED_ID_LIST",
    "wb": "WEIBO_SPECIFIED_ID_LIST",
    "tieba": "TIEBA_SPECIFIED_ID_LIST",
    "zhihu": "ZHIHU_SPECIFIED_ID_LIST",
}
CREATOR_ATTRIBUTES = {
    "xhs": "XHS_CREATOR_ID_LIST",
    "dy": "DY_CREATOR_ID_LIST",
    "ks": "KS_CREATOR_ID_LIST",
    "bili": "BILI_CREATOR_ID_LIST",
    "wb": "WEIBO_CREATOR_ID_LIST",
    "tieba": "TIEBA_CREATOR_URL_LIST",
    "zhihu": "ZHIHU_CREATOR_URL_LIST",
}


def _clean_strings(values: Iterable[str] | None, label: str) -> tuple[str, ...]:
    cleaned = tuple(str(item).strip() for item in (values or ()) if str(item).strip())
    for value in cleaned:
        if "," in value:
            raise ToolError(
                "INVALID_REQUEST",
                f"Each {label} must be a separate list item and cannot contain a comma.",
            )
        if any(ord(char) < 32 for char in value):
            raise ToolError("INVALID_REQUEST", f"{label} cannot contain control characters.")
        if len(value) > 2_048:
            raise ToolError("INVALID_REQUEST", f"A {label} exceeds 2,048 characters.")
    return cleaned


def _parse_day(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ToolError("INVALID_REQUEST", f"{label} must be YYYY-MM-DD.") from exc


def _tieba_detail(value: str) -> str:
    match = re.search(r"/p/(\d+)", value)
    return match.group(1) if match else value


def _tieba_creator(value: str) -> str:
    if value.startswith(("http://", "https://")):
        return value
    return f"https://tieba.baidu.com/home/main?id={value}"


@dataclass(frozen=True, slots=True)
class CrawlRequest:
    platform: str
    mode: str
    keywords: tuple[str, ...]
    targets: tuple[str, ...]
    max_items: int
    sort: str | None
    publish_within: str | None
    date_from: str | None
    date_to: str | None
    comments: bool
    max_comments: int
    sub_comments: bool
    media: bool
    start_page: int
    timeout_minutes: int

    @classmethod
    def create(
        cls,
        *,
        platform: str,
        mode: str,
        keywords: Iterable[str] | None = None,
        targets: Iterable[str] | None = None,
        max_items: int = 20,
        sort: str | None = None,
        publish_within: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        comments: bool = False,
        max_comments: int = 20,
        sub_comments: bool = False,
        media: bool = False,
        start_page: int = 1,
        timeout_minutes: int = 30,
    ) -> CrawlRequest:
        platform = str(platform).strip().lower()
        mode = str(mode).strip().lower()
        if isinstance(keywords, str):
            keywords = [keywords]
        if isinstance(targets, str):
            targets = [targets]
        clean_keywords = _clean_strings(keywords, "keyword")
        clean_targets = _clean_strings(targets, "target")
        sort = sort.strip().lower() if sort and sort.strip() else None
        publish_within = (
            publish_within.strip().lower()
            if publish_within and publish_within.strip()
            else None
        )
        date_from = date_from.strip() if date_from and date_from.strip() else None
        date_to = date_to.strip() if date_to and date_to.strip() else None

        if platform not in PLATFORMS:
            raise ToolError(
                "INVALID_REQUEST",
                f"Unsupported platform: {platform!r}. Supported: {', '.join(PLATFORMS)}.",
            )
        if mode not in MODES:
            raise ToolError("INVALID_REQUEST", "mode must be search, detail, or creator.")

        if mode == "search":
            if not clean_keywords:
                raise ToolError("INVALID_REQUEST", "keywords are required in search mode.")
            if len(clean_keywords) > MAX_KEYWORDS:
                raise ToolError(
                    "INVALID_REQUEST", f"At most {MAX_KEYWORDS} keywords are allowed per run."
                )
            if any(len(item) > 100 for item in clean_keywords):
                raise ToolError("INVALID_REQUEST", "A keyword cannot exceed 100 characters.")
            if clean_targets:
                raise ToolError("INVALID_REQUEST", "targets are not accepted in search mode.")
        else:
            if not clean_targets:
                raise ToolError("INVALID_REQUEST", f"targets are required in {mode} mode.")
            if clean_keywords:
                raise ToolError("INVALID_REQUEST", f"keywords are not accepted in {mode} mode.")
            if len(clean_targets) > MAX_TARGETS:
                raise ToolError(
                    "INVALID_REQUEST", f"At most {MAX_TARGETS} targets are allowed per run."
                )
            if mode == "creator" and len(clean_targets) > MAX_CREATORS:
                raise ToolError(
                    "INVALID_REQUEST", f"At most {MAX_CREATORS} creators are allowed per run."
                )
            if sort or publish_within or date_from or date_to:
                raise ToolError(
                    "INVALID_REQUEST",
                    "sort, publish_within, date_from and date_to only apply to search mode.",
                )

        if not 1 <= max_items <= MAX_TOTAL_ITEMS:
            raise ToolError(
                "INVALID_REQUEST", f"max_items must be between 1 and {MAX_TOTAL_ITEMS}."
            )
        if mode == "search" and len(clean_keywords) * max_items > MAX_TOTAL_ITEMS:
            raise ToolError(
                "INVALID_REQUEST",
                f"max_items applies per keyword; keywords x max_items cannot exceed {MAX_TOTAL_ITEMS}.",
            )

        if sort is not None:
            if platform not in SORT_OPTIONS:
                raise ToolError(
                    "INVALID_REQUEST",
                    f"MediaCrawler does not support choosing the search order on {platform}. "
                    "sort is available on xhs and wb only.",
                )
            allowed = SORT_OPTIONS[platform][1]
            if sort not in allowed:
                raise ToolError(
                    "INVALID_REQUEST",
                    f"sort on {platform} must be one of: {', '.join(allowed)}.",
                )
        if publish_within is not None:
            if platform != "dy":
                raise ToolError("INVALID_REQUEST", "publish_within is available on dy only.")
            if publish_within not in PUBLISH_WITHIN_OPTIONS:
                raise ToolError(
                    "INVALID_REQUEST",
                    f"publish_within must be one of: {', '.join(PUBLISH_WITHIN_OPTIONS)}.",
                )
        if date_from is not None or date_to is not None:
            if platform != "bili":
                raise ToolError(
                    "INVALID_REQUEST", "date_from and date_to are available on bili only."
                )
            if date_from is None or date_to is None:
                raise ToolError(
                    "INVALID_REQUEST", "date_from and date_to must be given together."
                )
            first = _parse_day(date_from, "date_from")
            last = _parse_day(date_to, "date_to")
            if first > last:
                raise ToolError("INVALID_REQUEST", "date_from cannot be after date_to.")
            if (last - first).days + 1 > MAX_DATE_SPAN_DAYS:
                raise ToolError(
                    "INVALID_REQUEST",
                    f"The date range cannot exceed {MAX_DATE_SPAN_DAYS} days.",
                )

        if not 1 <= max_comments <= 200:
            raise ToolError("INVALID_REQUEST", "max_comments must be between 1 and 200.")
        if sub_comments and not comments:
            raise ToolError("INVALID_REQUEST", "sub_comments requires comments=true.")
        if not 1 <= start_page <= 100:
            raise ToolError("INVALID_REQUEST", "start_page must be between 1 and 100.")
        if not 1 <= timeout_minutes <= 120:
            raise ToolError("INVALID_REQUEST", "timeout_minutes must be between 1 and 120.")

        return cls(
            platform=platform,
            mode=mode,
            keywords=clean_keywords,
            targets=clean_targets,
            max_items=max_items,
            sort=sort,
            publish_within=publish_within,
            date_from=date_from,
            date_to=date_to,
            comments=comments,
            max_comments=max_comments,
            sub_comments=sub_comments,
            media=media,
            start_page=start_page,
            timeout_minutes=timeout_minutes,
        )

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["keywords"] = list(self.keywords)
        value["targets"] = list(self.targets)
        return value

    def config_overrides(self) -> dict[str, Any]:
        """Upstream `config` attributes the runner sets before MediaCrawler starts."""
        overrides: dict[str, Any] = {}
        if self.mode == "search":
            overrides["KEYWORDS"] = ",".join(self.keywords)
        elif self.mode == "detail":
            targets = list(self.targets)
            if self.platform == "tieba":
                targets = [_tieba_detail(item) for item in targets]
            overrides[DETAIL_ATTRIBUTES[self.platform]] = targets
        else:
            targets = list(self.targets)
            if self.platform == "tieba":
                targets = [_tieba_creator(item) for item in targets]
            overrides[CREATOR_ATTRIBUTES[self.platform]] = targets

        if self.sort is not None:
            attribute, values = SORT_OPTIONS[self.platform]
            overrides[attribute] = values[self.sort]
        if self.publish_within is not None:
            overrides["PUBLISH_TIME_TYPE"] = PUBLISH_WITHIN_OPTIONS[self.publish_within]
        if self.date_from is not None and self.date_to is not None:
            days = (date.fromisoformat(self.date_to) - date.fromisoformat(self.date_from)).days + 1
            overrides["BILI_SEARCH_MODE"] = "all_in_time_range"
            overrides["START_DAY"] = self.date_from
            overrides["END_DAY"] = self.date_to
            # Upstream walks the range day by day; spread the budget over the days.
            overrides["MAX_NOTES_PER_DAY"] = max(1, math.ceil(self.max_items / days))
        return overrides

    def warnings(self) -> list[str]:
        warnings: list[str] = []
        floor = SEARCH_PAGE_FLOORS[self.platform]
        if self.mode == "search" and self.max_items < floor and self.date_from is None:
            warnings.append(
                f"{self.platform} search fetches whole pages, so each keyword may return up to "
                f"{floor} items even though max_items={self.max_items}."
            )
        if self.mode == "creator" and self.platform in {"dy", "ks", "bili", "wb", "zhihu"}:
            warnings.append(
                f"MediaCrawler does not enforce max_items in {self.platform} creator mode; "
                "timeout_minutes is the hard limit."
            )
        if self.sub_comments:
            warnings.append(
                "Some platforms fetch every nested reply of the selected comments; "
                "timeout_minutes is the hard limit."
            )
        return warnings
