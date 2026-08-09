# -*- coding: utf-8 -*-
"""簡體轉台灣正體。

上游 REPORT_LANGUAGE 只支援 zh/en/ko，而且把 zh-tw 別名直接映射回 zh
（見 src/report_language.py 的 _REPORT_LANGUAGE_ALIASES），所以沒有繁中輸出。

改成在推播出口做一次 s2twp 轉換，而不是去動那支 1300 行的 report_language
模組與各語系樣板 —— 後者會在每次同步上游時衝突。

非中文內容（英文、韓文、股票代號、URL）經過轉換是 no-op，
所以這層不需要判斷 REPORT_LANGUAGE，永遠掛著是安全的。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_OPENCC_CONFIG = "s2twp"  # 簡體 -> 台灣正體，含慣用詞（軟體／記憶體／晶片）

_converter = None
_converter_unavailable = False


def _get_converter():
    """取得 OpenCC converter，只初始化一次；不可用時記錄並永久略過。"""
    global _converter, _converter_unavailable

    if _converter is not None or _converter_unavailable:
        return _converter

    try:
        from opencc import OpenCC

        _converter = OpenCC(_OPENCC_CONFIG)
    except Exception as exc:  # noqa: BLE001 - 缺套件或設定檔都不該中斷推播
        _converter_unavailable = True
        logger.warning("OpenCC 不可用，改以原文推播: %s", exc)

    return _converter


def to_traditional(text: str) -> str:
    """把簡體中文轉成台灣正體。

    轉換失敗一律回傳原文 —— 推播內容本身比字體正確性重要，
    不能因為這層而讓整份報告消失。
    """
    if not isinstance(text, str) or not text:
        return text

    converter = _get_converter()
    if converter is None:
        return text

    try:
        return converter.convert(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("簡繁轉換失敗，改以原文推播: %s", exc)
        return text
