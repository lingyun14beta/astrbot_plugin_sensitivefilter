# -*- coding: utf-8 -*-
"""
群级配置存储。

AstrBot 的插件配置 (`_conf_schema.json` / `AstrBotConfig`) 是“全局唯一”的一份，
没办法直接表达“每个群一份不同配置”，因为群号是运行时才知道的动态 key。

所以这里单独实现一个非常轻量的 JSON 文件存储，专门保存“群覆盖配置”：
    - 某个开关在某个群里是否被单独打开/关闭（None 表示不覆盖，跟随全局配置）
    - 某个群自己额外追加的敏感词

数据始终落盘在插件的 `data/` 目录下（而不是插件代码目录），符合 AstrBot
插件开发规范，防止插件更新/重装时数据被覆盖。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List


class GroupConfigStore:
    """基于 JSON 文件的群配置存储，进程内用 asyncio.Lock 保证写入不冲突。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = asyncio.Lock()
        self._data: Dict[str, Dict[str, Any]] = {}
        self._load()

    # ---------------- 内部：读写文件 ----------------

    def _load(self) -> None:
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    self._data = loaded
            except Exception:
                # 文件损坏时不让插件崩溃，退化为空配置
                self._data = {}
        else:
            self._data = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        tmp_path.replace(self.path)  # 原子替换，避免写入过程中崩溃导致文件损坏

    # ---------------- 对外接口 ----------------

    def get(self, group_id: str) -> Dict[str, Any]:
        """获取某个群的覆盖配置（拷贝），不存在时返回空字典。"""
        return dict(self._data.get(str(group_id), {}))

    async def set_value(self, group_id: str, key: str, value: Any) -> None:
        """设置某个群单个配置项；value 为 None 时表示删除覆盖（跟随全局）。"""
        async with self._lock:
            gid = str(group_id)
            conf = self._data.setdefault(gid, {})
            if value is None:
                conf.pop(key, None)
            else:
                conf[key] = value
            self._save()

    async def add_extra_word(self, group_id: str, word: str) -> bool:
        """为某个群追加一个专属敏感词，已存在则返回 False。"""
        word = (word or "").strip()
        if not word:
            return False
        async with self._lock:
            gid = str(group_id)
            conf = self._data.setdefault(gid, {})
            words: List[str] = conf.setdefault("extra_words", [])
            if word in words:
                return False
            words.append(word)
            self._save()
            return True

    async def remove_extra_word(self, group_id: str, word: str) -> bool:
        """移除某个群的专属敏感词，不存在则返回 False。"""
        word = (word or "").strip()
        async with self._lock:
            gid = str(group_id)
            conf = self._data.setdefault(gid, {})
            words: List[str] = conf.get("extra_words", [])
            if word not in words:
                return False
            words.remove(word)
            self._save()
            return True

    def all_group_ids(self) -> List[str]:
        return list(self._data.keys())
