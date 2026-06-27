# -*- coding: utf-8 -*-
"""外部接口检测。

提供两种模式：

1. check_via_uapis_profanitycheck —— 专门适配 uapis.cn 的「敏感词检测（快速）」接口，
   开箱即用，几乎不需要额外配置。
   文档：https://uapis.cn/docs/api-reference/post-sensitive-word-quick-check

2. check_via_api —— 通用模式，适配任意一个“文本审核 API”，约定一个简单、可配置的
   请求/响应协议：
       请求: 把消息文本放进一个 JSON 字段（字段名可配置，默认 "text"）发送
       响应: 从返回的 JSON 中按“点路径”取出一个布尔字段表示是否命中，
             以及一个可选的文本字段作为命中原因/分类
   不同厂商的审核 API 返回结构差异很大，这里不为某一家服务硬编码，而是把字段
   路径交给用户在插件配置里填写，做到“通用适配”。
"""

from __future__ import annotations

import json
from typing import Any

import aiohttp

from astrbot.api import logger

from .utils import to_bool

# uapis.cn「敏感词检测（快速）」接口地址，文档见模块docstring顶部链接。
UAPIS_PROFANITYCHECK_URL = "https://uapis.cn/api/v1/text/profanitycheck"


def _get_by_path(data: Any, path: str) -> Any:
    """从嵌套 dict 中按 'a.b.c' 形式的点路径取值，取不到返回 None。"""
    if not path:
        return None
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


async def check_via_uapis_profanitycheck(
    session: aiohttp.ClientSession,
    text: str,
    *,
    api_url: str = UAPIS_PROFANITYCHECK_URL,
    api_key: str = "",
    timeout: float = 5.0,
) -> tuple[bool, str | None]:
    """专门适配 uapis.cn 的「敏感词检测（快速）」接口。

    请求：POST {"text": "..."}，鉴权可选（不填走访客额度，填了走
    `Authorization: Bearer <api_key>`，额度更高更稳定）。

    响应（200）形如：
        {
          "status": "forbidden",       # 命中时固定为 "forbidden"，未命中时是别的值
          "original_text": "...",
          "masked_text": "...",
          "forbidden_words": ["..."]
        }
    响应（400）表示请求体无效或文本为空，按“未命中”处理，不视为错误。
    """
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    timeout_obj = aiohttp.ClientTimeout(total=timeout)
    async with session.post(
        api_url, json={"text": text}, headers=headers, timeout=timeout_obj
    ) as resp:
        if resp.status == 400:
            # 文本为空或请求体无效，不是真正的错误，直接当作未命中
            return False, None
        resp.raise_for_status()
        data = await resp.json(content_type=None)

    if not isinstance(data, dict):
        return False, None

    hit = data.get("status") == "forbidden"
    if not hit:
        return False, None

    forbidden_words = data.get("forbidden_words") or []
    reason = "、".join(str(w) for w in forbidden_words) if forbidden_words else None
    return True, reason


async def check_via_api(
    session: aiohttp.ClientSession,
    text: str,
    *,
    api_url: str,
    method: str = "POST",
    headers_json: str = "{}",
    text_field: str = "text",
    hit_path: str = "hit",
    reason_path: str = "reason",
    timeout: float = 5.0,
) -> tuple[bool, str | None]:
    """调用外部敏感内容检测接口（通用模式）。返回 (是否命中, 命中原因或 None)。"""
    try:
        headers = json.loads(headers_json) if headers_json else {}
        if not isinstance(headers, dict):
            headers = {}
    except Exception:
        logger.warning("[敏感词过滤] api_headers 不是合法 JSON，已忽略自定义请求头")
        headers = {}

    timeout_obj = aiohttp.ClientTimeout(total=timeout)
    method = (method or "POST").upper()

    if method == "GET":
        params = {text_field: text}
        async with session.get(
            api_url, params=params, headers=headers, timeout=timeout_obj
        ) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
    else:
        payload = {text_field: text}
        async with session.post(
            api_url, json=payload, headers=headers, timeout=timeout_obj
        ) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)

    hit = to_bool(_get_by_path(data, hit_path))
    reason = _get_by_path(data, reason_path)
    reason_str = str(reason) if reason else None
    return hit, reason_str
