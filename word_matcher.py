# -*- coding: utf-8 -*-
"""
轻量级敏感词匹配器。

实现思路：把所有敏感词组织成一棵字典树（Trie），扫描文本时从每个位置尝试
向下匹配，命中树中标记的“结束节点”即视为命中一个敏感词。

为了防止常见的绕过手法，提供两个能力：
    1. 文本归一化 normalize_text()：去除零宽字符、把全角字符转换为半角，
       让 "敏感词" 和 "敏感词"（全角）等视觉相似的写法统一处理。
    2. 模糊匹配 fuzzy=True：匹配过程中允许跳过词语中间插入的空格、标点等
       “干扰字符”，从而识别 "敏 感*词" 这类拆字写法。

性能上是 O(n * 平均词长) 级别的简单实现，对于聊天消息（通常几十到几百字）
完全够用，没有引入 Aho-Corasick 等更复杂的结构，便于阅读和维护。
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Set

# 树中用于标记“一个词在此结束”的特殊 key，正常字符不会与之冲突
_END = "\0__end__"

# 默认的“干扰字符”集合：空格、常见标点、下划线等。
# 模糊匹配时，只要已经匹配上至少一个字符，后续遇到这些字符会被跳过，
# 而不会打断匹配过程。
DEFAULT_SKIP_CHARS: Set[str] = set(
    " \t\u3000*_-.,，。！？·~`'\"“”‘’()（）[]【】{}<>|/\\=+"
)

# 零宽字符：肉眼不可见，常被用来插入到敏感词中间以绕过检测
_ZERO_WIDTH_CHARS = "\u200b\u200c\u200d\u2060\ufeff"


def normalize_text(text: str) -> str:
    """对原始消息文本做归一化处理，便于后续匹配。

    - 移除零宽字符
    - 全角 ASCII 字符转换为半角（如全角问号、全角字母）
    """
    if not text:
        return ""
    for ch in _ZERO_WIDTH_CHARS:
        if ch in text:
            text = text.replace(ch, "")
    # 全角字符 U+FF01-U+FF5E 与对应半角字符相差 0xFEE0
    text = "".join(
        chr(ord(ch) - 0xFEE0) if 0xFF01 <= ord(ch) <= 0xFF5E else ch for ch in text
    )
    return text


class WordTrie:
    """敏感词字典树。"""

    def __init__(
        self,
        case_insensitive: bool = True,
        fuzzy: bool = True,
        skip_chars: Optional[Set[str]] = None,
    ):
        self.case_insensitive = case_insensitive
        self.fuzzy = fuzzy
        self.skip_chars = skip_chars if skip_chars is not None else DEFAULT_SKIP_CHARS
        self.root: dict = {}
        self._size = 0

    def __len__(self) -> int:
        return self._size

    def __bool__(self) -> bool:
        return self._size > 0

    def _key(self, ch: str) -> str:
        return ch.lower() if self.case_insensitive else ch

    def add_word(self, word: str) -> None:
        """向树中添加一个敏感词，自动忽略空白词条。"""
        word = (word or "").strip()
        if not word:
            return
        node = self.root
        for ch in word:
            k = self._key(ch)
            node = node.setdefault(k, {})
        if _END not in node:
            self._size += 1
        node[_END] = word

    def build(self, words: Iterable[str]) -> None:
        """使用给定词表重建整棵树（会清空旧数据）。"""
        self.root = {}
        self._size = 0
        for w in words or []:
            self.add_word(w)

    def find_first(self, text: str) -> Optional[str]:
        """返回文本中命中的第一个敏感词（原始大小写形式），未命中返回 None。"""
        if not self.root or not text:
            return None
        n = len(text)
        for i in range(n):
            node = self.root
            matched = 0
            j = i
            while j < n:
                ch = self._key(text[j])
                if self.fuzzy and matched > 0 and ch in self.skip_chars:
                    j += 1
                    continue
                nxt = node.get(ch)
                if nxt is None:
                    break
                node = nxt
                matched += 1
                j += 1
                if _END in node:
                    return node[_END]
            # 当前起点没有命中任何词，继续从下一个字符开始尝试
        return None

    def find_all(self, text: str, limit: int = 20) -> List[str]:
        """返回文本中命中的全部敏感词（去重，按出现顺序），主要用于日志/审计。"""
        if not self.root or not text:
            return []
        results: List[str] = []
        n = len(text)
        for i in range(n):
            node = self.root
            matched = 0
            j = i
            while j < n:
                ch = self._key(text[j])
                if self.fuzzy and matched > 0 and ch in self.skip_chars:
                    j += 1
                    continue
                nxt = node.get(ch)
                if nxt is None:
                    break
                node = nxt
                matched += 1
                j += 1
                if _END in node:
                    word = node[_END]
                    if word not in results:
                        results.append(word)
                        if len(results) >= limit:
                            return results
                    break
        return results
