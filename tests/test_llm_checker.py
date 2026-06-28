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


async def run_batch_tests():
    check_via_llm_batch = llm_checker.check_via_llm_batch
    default_batch_prompt = llm_checker.DEFAULT_LLM_BATCH_PROMPT

    # 全部正常返回，部分违规部分不违规
    p1 = FakeProvider(
        '{"results": ['
        '{"index": 0, "violate": false, "reason": ""}, '
        '{"index": 1, "violate": true, "reason": "广告引流"}, '
        '{"index": 2, "violate": false, "reason": ""}'
        "]}"
    )
    results1 = await check_via_llm_batch(
        p1, ["今天天气不错", "加我微信领取奖品", "中午吃什么"]
    )
    check("批量审核返回长度与输入一致", len(results1) == 3)
    check("批量审核-第0条未违规", results1[0] == (False, None))
    check("批量审核-第1条违规且原因正确", results1[1] == (True, "广告引流"))
    check("批量审核-第2条未违规", results1[2] == (False, None))
    check(
        "批量prompt里包含每条消息和序号",
        "[0] 今天天气不错" in p1.last_prompt
        and "[1] 加我微信领取奖品" in p1.last_prompt,
    )

    # 模型返回的结果数量少于输入（缺失的按未违规兜底，不报错不崩溃）
    p2 = FakeProvider('{"results": [{"index": 0, "violate": true, "reason": "测试"}]}')
    results2 = await check_via_llm_batch(p2, ["消息A", "消息B", "消息C"])
    check("结果数量不足时仍返回与输入等长的列表", len(results2) == 3)
    check("结果数量不足-命中的那条仍正确", results2[0] == (True, "测试"))
    check("结果数量不足-缺失的兜底为未违规", results2[1] == (False, None))
    check("结果数量不足-缺失的兜底为未违规(第3条)", results2[2] == (False, None))

    # 下标乱序也能正确归位
    p3 = FakeProvider(
        '{"results": ['
        '{"index": 2, "violate": true, "reason": "C违规"}, '
        '{"index": 0, "violate": false, "reason": ""}, '
        '{"index": 1, "violate": false, "reason": ""}'
        "]}"
    )
    results3 = await check_via_llm_batch(p3, ["A", "B", "C"])
    check("下标乱序时仍按index正确归位-第0条", results3[0] == (False, None))
    check("下标乱序时仍按index正确归位-第1条", results3[1] == (False, None))
    check("下标乱序时仍按index正确归位-第2条", results3[2] == (True, "C违规"))

    # 完全无法解析（不是预期的{"results": [...]}格式）
    p4 = FakeProvider("我没办法返回JSON")
    results4 = await check_via_llm_batch(p4, ["消息1", "消息2"])
    check("无法解析时全部兜底为未违规", results4 == [(False, None), (False, None)])

    # results 不是数组类型时也不应该崩溃
    p5 = FakeProvider('{"results": "不是数组"}')
    results5 = await check_via_llm_batch(p5, ["消息1"])
    check("results字段类型不对时不崩溃且兜底未违规", results5 == [(False, None)])

    # 空列表输入直接返回空列表，不应该调用模型
    p6 = FakeProvider('{"results": []}')
    results6 = await check_via_llm_batch(p6, [])
    check("空输入返回空列表", results6 == [])
    check("空输入不会调用模型", p6.last_prompt is None)

    # provider 为 None
    results7 = await check_via_llm_batch(None, ["消息1", "消息2"])
    check(
        "provider为None时全部兜底为未违规", results7 == [(False, None), (False, None)]
    )

    # 默认批量Prompt本身渲染不报错，且包含{messages}占位符
    check(
        "默认批量Prompt包含{messages}占位符",
        "{messages}" in default_batch_prompt,
    )
    p8 = FakeProvider('{"results": [{"index": 0, "violate": false, "reason": ""}]}')
    await check_via_llm_batch(
        p8, ["默认prompt渲染测试"], prompt_template=default_batch_prompt
    )
    check(
        "默认批量Prompt渲染后正确包含消息内容",
        "默认prompt渲染测试" in p8.last_prompt,
    )

    # 自定义模板忘记写 {messages} 占位符时仍应兜底把消息列表追加进去
    p9 = FakeProvider('{"results": [{"index": 0, "violate": false, "reason": ""}]}')
    await check_via_llm_batch(p9, ["兜底测试消息"], prompt_template="请审核以下内容")
    check("自定义批量模板缺占位符时仍包含消息内容", "兜底测试消息" in p9.last_prompt)


asyncio.run(run_batch_tests())


asyncio.run(run_llm_tests())

print(f"\n共 {passed + failed} 项，通过 {passed}，失败 {failed}")
sys.exit(0 if failed == 0 else 1)
