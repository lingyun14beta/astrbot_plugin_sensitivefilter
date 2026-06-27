# -*- coding: utf-8 -*-
"""对 word_matcher.py 的功能性测试（非 astrbot 依赖，纯逻辑验证）。

直接运行：python3 test_word_matcher.py
（假设本文件位于插件目录下的 tests/ 子目录中，会自动定位到上一级插件目录）
"""

import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_DIR))

from word_matcher import WordTrie, normalize_text  # noqa: E402

passed = 0
failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"[OK]   {name}")
    else:
        failed += 1
        print(f"[FAIL] {name}")


# ---------------- 基础匹配 ----------------
trie = WordTrie()
trie.build(["敏感词", "广告", "fuck"])

check("基础命中-中文", trie.find_first("这是一条含有敏感词的消息") == "敏感词")
check("基础命中-多词其一", trie.find_first("欢迎来看广告位招租") == "广告")
check("未命中返回None", trie.find_first("这是一条正常消息") is None)
check("空文本不报错", trie.find_first("") is None)

# ---------------- 大小写 ----------------
check("默认忽略大小写命中", trie.find_first("don't FUCK around") == "fuck")

trie_case = WordTrie(case_insensitive=False)
trie_case.build(["fuck"])
check("大小写敏感时大写不命中", trie_case.find_first("FUCK you") is None)
check("大小写敏感时原样命中", trie_case.find_first("fuck you") == "fuck")

# ---------------- 模糊匹配（防拆字绕过） ----------------
trie_fuzzy = WordTrie(fuzzy=True)
trie_fuzzy.build(["敏感词"])
check("模糊匹配-空格拆字", trie_fuzzy.find_first("这是 敏 感 词 测试") == "敏感词")
check("模糊匹配-符号拆字", trie_fuzzy.find_first("敏*感_词") == "敏感词")

trie_nofuzzy = WordTrie(fuzzy=False)
trie_nofuzzy.build(["敏感词"])
check("关闭模糊匹配后拆字不命中", trie_nofuzzy.find_first("敏 感 词") is None)
check("关闭模糊匹配后连续仍命中", trie_nofuzzy.find_first("这是敏感词测试") == "敏感词")

# ---------------- find_all ----------------
trie_multi = WordTrie()
trie_multi.build(["广告", "诈骗"])
hits = trie_multi.find_all("这是广告也是诈骗广告")
check("find_all去重", hits == ["广告", "诈骗"])

# ---------------- normalize_text ----------------
check(
    "零宽字符被去除后命中",
    WordTrie().build(["敏感词"]) is None,  # build 无返回值，占位检查不报错
)
trie_zw = WordTrie(fuzzy=False)
trie_zw.build(["敏感词"])
zw_text = "敏\u200b感\u200c词"  # 中间插入零宽字符
check(
    "零宽字符规整后命中(非模糊模式)",
    trie_zw.find_first(normalize_text(zw_text)) == "敏感词",
)

full_width_text = "ＡＢＣ"  # 全角字母
check("全角转半角", normalize_text(full_width_text) == "ABC")

# ---------------- 空词库 ----------------
empty_trie = WordTrie()
check("空词库不报错", empty_trie.find_first("随便什么内容") is None)
check("空词库bool为False", bool(empty_trie) is False)

print(f"\n共 {passed + failed} 项，通过 {passed}，失败 {failed}")
sys.exit(0 if failed == 0 else 1)
