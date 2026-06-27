# -*- coding: utf-8 -*-
"""跨模块共享的小工具函数。"""

from __future__ import annotations

from typing import Any

_TRUTHY_STRINGS = ("1", "true", "yes", "y", "hit", "命中", "是")


def to_bool(value: Any) -> bool:
    """把任意类型的值尽量转换为布尔值。

    用于把外部接口 / LLM 返回的“命中与否”字段（可能是 bool / int / 字符串）
    统一转换成 Python 的 True/False。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY_STRINGS
    return bool(value)
