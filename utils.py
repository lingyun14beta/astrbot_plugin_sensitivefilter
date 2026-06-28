# -*- coding: utf-8 -*-
"""跨模块共享的小工具函数。"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

_TRUTHY_STRINGS = ("1", "true", "yes", "y", "hit", "命中", "是")

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


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


def extract_json(text: str) -> Optional[dict]:
    """从 LLM 回复中尽量提取出一个 JSON 对象（模型有时会附带多余文字）。

    供 llm_checker.py（文字语义审核）和 image_checker.py（图片审核）共用，
    两者都需要从模型的自然语言回复中稳健地解析出结构化结果。
    """
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = _JSON_OBJ_RE.search(text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None
