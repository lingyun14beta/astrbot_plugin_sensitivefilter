# -*- coding: utf-8 -*-
"""对 llm_checker.py 的功能性测试。

用一个假的 provider 对象模拟 AstrBot 的 LLM Provider，覆盖“干净 JSON”
“JSON 外面包了一层文字”“无法解析”几种模型回复场景。

直接运行：python3 test_llm_checker.py
（假设本文件位于插件目录下的 tests/ 子目录中，会自动定位到上一级插件目录）
"""

import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace

# --- 测试专用：stub 掉 astrbot.api，真实环境中这个包由 AstrBot 本体提供 ---
_fake_logger = SimpleNamespace(
    info=lambda *a, **k: None,
    warning=lambda *a, **k: None,
    error=lambda *a, **k: None,
    exception=lambda *a, **k: None,
    debug=lambda *a, **k: None,
)
_fake_astrbot_api = types.ModuleType("astrbot.api")
_fake_astrbot_api.logger = _fake_logger
sys.modules.setdefault("astrbot", types.ModuleType("astrbot"))
sys.modules["astrbot.api"] = _fake_astrbot_api
# --- stub 结束 ---

PLUGIN_DIR = (
    Path(__file__).resolve().parent.parent
)  # .../astrbot_plugin_sensitivefilter
PARENT_DIR = PLUGIN_DIR.parent  # 包含插件目录本身的上级目录
sys.path.insert(0, str(PARENT_DIR))

import importlib  # noqa: E402

_pkg = types.ModuleType("astrbot_plugin_sensitivefilter")
_pkg.__path__ = [str(PLUGIN_DIR)]
sys.modules["astrbot_plugin_sensitivefilter"] = _pkg

llm_checker = importlib.import_module("astrbot_plugin_sensitivefilter.llm_checker")
utils = importlib.import_module("astrbot_plugin_sensitivefilter.utils")
check_via_llm = llm_checker.check_via_llm
to_bool = utils.to_bool

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


# ---------------- to_bool ----------------
check("to_bool 处理字符串true", to_bool("true") is True)
check("to_bool 处理中文是", to_bool("是") is True)
check("to_bool 处理0", to_bool(0) is False)
check("to_bool 处理布尔本身", to_bool(True) is True)


# ---------------- 模拟 LLM Provider ----------------
class FakeProvider:
    def __init__(self, reply_text):
        self.reply_text = reply_text
        self.last_prompt = None

    async def text_chat(self, prompt, context=None, system_prompt=""):
        self.last_prompt = prompt
        return SimpleNamespace(completion_text=self.reply_text)


async def run_llm_tests():
    # 干净 JSON
    p1 = FakeProvider('{"violate": true, "reason": "包含广告引流信息"}')
    violate1, reason1 = await check_via_llm(p1, "加我微信123，免费领取")
    check("LLM干净JSON-违规判定", violate1 is True)
    check("LLM干净JSON-原因提取", reason1 == "包含广告引流信息")
    check("LLM prompt中包含原文", "加我微信123" in p1.last_prompt)

    # 默认提示词模板本身不应再触发 KeyError（曾经的 bug：示例 JSON 里的花括号被
    # str.format() 误判为占位符）
    default_prompt = llm_checker.DEFAULT_LLM_PROMPT

    p1b = FakeProvider('{"violate": false, "reason": "正常聊天"}')
    violate1b, _ = await check_via_llm(
        p1b, "今天中午吃什么", prompt_template=default_prompt
    )
    check("默认提示词模板渲染不报错", violate1b is False)
    check("默认模板正确替换了待审核文本", "今天中午吃什么" in p1b.last_prompt)

    # JSON外面包了文字（模型没完全听话）
    p2 = FakeProvider(
        '好的，我的判断是：\n{"violate": false, "reason": "未见异常"}\n以上。'
    )
    violate2, reason2 = await check_via_llm(p2, "今天天气不错")
    check("LLM夹杂文字仍能提取JSON", violate2 is False)
    check("LLM夹杂文字-原因正确", reason2 == "未见异常")

    # 完全无法解析
    p3 = FakeProvider("我不知道该怎么判断这条消息")
    violate3, reason3 = await check_via_llm(p3, "随便的内容")
    check("LLM无法解析时不报错且判False", violate3 is False)
    check("LLM无法解析时reason为None", reason3 is None)

    # provider 为 None（未配置）
    violate4, reason4 = await check_via_llm(None, "任意内容")
    check("provider为None时安全返回False", violate4 is False)

    # 自定义模板忘记写 {text} 占位符时，仍应把原文追加进去兜底
    p5 = FakeProvider('{"violate": false, "reason": "ok"}')
    await check_via_llm(p5, "兜底测试原文", prompt_template="审核以下内容是否违规")
    check("自定义模板缺占位符时仍包含原文", "兜底测试原文" in p5.last_prompt)


asyncio.run(run_llm_tests())

print(f"\n共 {passed + failed} 项，通过 {passed}，失败 {failed}")
sys.exit(0 if failed == 0 else 1)
