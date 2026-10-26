# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/xhs/extractor.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

import json
import re
from typing import Any, Dict, Optional

import humps

from tools import utils

# window.__INITIAL_STATE__ is a JS object literal, not strict JSON. Tokens outside of
# string literals that need rewriting before json can parse it:
#   - bare `undefined` / `NaN` / `Infinity` / `-Infinity` values -> null
#   - `new Set([...])` / `new Map([...])` -> the inner array
# String literals are matched first and kept verbatim, so the word "undefined" inside a
# note title/description is never touched (a blanket str.replace produced invalid JSON).
_JS_STATE_TOKEN_RE = re.compile(
    r'"[^"\\]*(?:\\.[^"\\]*)*"'  # JSON string literal: keep as-is
    r"|new\s+(?:Set|Map)\(\s*\)"  # empty Set/Map -> []
    r"|new\s+(?:Set|Map)\("  # Set/Map wrapper start -> dropped
    r"|\)"  # wrapper end (JSON itself has no parentheses outside strings) -> dropped
    r"|-?\bInfinity\b|\bNaN\b|\bundefined\b",  # non-JSON literals -> null
    re.S,
)


def _replace_js_state_token(match: "re.Match[str]") -> str:
    token = match.group(0)
    if token.startswith('"'):
        return token
    if token.startswith("new"):
        return "[]" if token.endswith(")") else ""
    if token == ")":
        return ""
    return "null"


def _js_state_to_json(raw: str) -> str:
    """Normalize a JS __INITIAL_STATE__ literal into JSON text"""
    return _JS_STATE_TOKEN_RE.sub(_replace_js_state_token, raw)


def _decode_first_json_value(text: str) -> Any:
    """Decode only the first JSON value; the greedy page regex may capture trailing script content"""
    return json.JSONDecoder(strict=False).raw_decode(text.strip())[0]


def _error_context(text: str, pos: int, radius: int = 40) -> str:
    """repr of the text around the error position, to identify the offending literal"""
    return repr(text[max(0, pos - radius): pos + radius])


class XiaoHongShuExtractor:
    def __init__(self):
        pass

    def extract_note_detail_from_html(self, note_id: str, html: str) -> Optional[Dict]:
        """Extract note details from HTML

        Args:
            html (str): HTML string

        Returns:
            Dict: Note details dictionary
        """
        if "noteDetailMap" not in html:
            # Either a CAPTCHA appeared or the note doesn't exist
            return None

        state = _js_state_to_json(
            re.findall(r"window.__INITIAL_STATE__=({.*})</script>", html)[0]
        )
        if state == "{}":
            return None
        try:
            note_dict = humps.decamelize(_decode_first_json_value(state))
            return note_dict["note"]["note_detail_map"][note_id]["note"]
        except json.JSONDecodeError as e:
            utils.logger.warning(
                f"[XiaoHongShuExtractor.extract_note_detail_from_html] parse failed, note_id: {note_id}, "
                f"err: {e}, pos: {e.pos}, context: {_error_context(e.doc, e.pos)}"
            )
            return None
        except (KeyError, TypeError) as e:
            utils.logger.warning(
                f"[XiaoHongShuExtractor.extract_note_detail_from_html] note detail not found in state, "
                f"note_id: {note_id}, err: {e!r}"
            )
            return None

    def extract_creator_info_from_html(self, html: str) -> Optional[Dict]:
        """Extract user information from HTML

        Args:
            html (str): HTML string

        Returns:
            Dict: User information dictionary
        """
        match = re.search(
            r"<script>window.__INITIAL_STATE__=(.+)<\/script>", html, re.M
        )
        if match is None:
            return None
        try:
            info = _decode_first_json_value(_js_state_to_json(match.group(1)))
        except json.JSONDecodeError as e:
            utils.logger.warning(
                f"[XiaoHongShuExtractor.extract_creator_info_from_html] parse failed, "
                f"err: {e}, pos: {e.pos}, context: {_error_context(e.doc, e.pos)}"
            )
            return None
        if not isinstance(info, dict):
            return None
        return (info.get("user") or {}).get("userPageData")
