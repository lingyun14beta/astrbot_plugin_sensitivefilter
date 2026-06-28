# -*- coding: utf-8 -*-
"""对 image_checker.py 的功能性测试。

用一个假的 provider 对象模拟支持视觉的 LLM Provider，覆盖"图片违规"
"图片不违规但有文字""图片无文字""返回内容无法解析"等场景，并验证
image_urls 参数确实把图片路径传给了模型。

直接运行：python3 test_image_checker.py
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

image_checker = importlib.import_module("astrbot_plugin_sensitivefilter.image_checker")
check_image = image_checker.check_image
DEFAULT_IMAGE_PROMPT = image_checker.DEFAULT_IMAGE_PROMPT

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


class FakeVisionProvider:
    def __init__(self, reply_text):
        self.reply_text = reply_text
        self.last_kwargs = None

    async def text_chat(self, prompt, context=None, system_prompt="", image_urls=None):
        self.last_kwargs = {
            "prompt": prompt,
            "context": context,
            "system_prompt": system_prompt,
            "image_urls": image_urls,
        }
        return SimpleNamespace(completion_text=self.reply_text)


async def run_tests():
    # ---------- 图片内容违规 ----------
    p1 = FakeVisionProvider(
        '{"image_violate": true, "image_reason": "包含血腥画面", "extracted_text": ""}'
    )
    violate1, reason1, text1 = await check_image(p1, "/tmp/fake1.jpg")
    check("图片违规判定正确", violate1 is True)
    check("图片违规原因正确", reason1 == "包含血腥画面")
    check("图片违规时extracted_text为空串", text1 == "")
    check(
        "image_urls参数正确传递了图片路径",
        p1.last_kwargs["image_urls"] == ["/tmp/fake1.jpg"],
    )

    # ---------- 图片不违规但含有文字 ----------
    p2 = FakeVisionProvider(
        '{"image_violate": false, "image_reason": "", "extracted_text": "加我微信领取免费奖品"}'
    )
    violate2, reason2, text2 = await check_image(p2, "/tmp/fake2.jpg")
    check("图片不违规判定正确", violate2 is False)
    check("图片不违规时reason为None", reason2 is None)
    check("正确转写出图片中的文字", text2 == "加我微信领取免费奖品")

    # ---------- 图片无文字也不违规 ----------
    p3 = FakeVisionProvider(
        '{"image_violate": false, "image_reason": "", "extracted_text": ""}'
    )
    violate3, reason3, text3 = await check_image(p3, "/tmp/fake3.jpg")
    check("无文字无违规-判定正确", violate3 is False)
    check("无文字无违规-文字为空", text3 == "")

    # ---------- 模型回复夹杂额外文字仍能解析 ----------
    p4 = FakeVisionProvider(
        '这是我的判断：\n{"image_violate": true, "image_reason": "色情内容", "extracted_text": "测试"}\n以上。'
    )
    violate4, reason4, text4 = await check_image(p4, "/tmp/fake4.jpg")
    check("夹杂文字时仍能提取JSON-违规判定", violate4 is True)
    check("夹杂文字时仍能提取JSON-原因正确", reason4 == "色情内容")
    check("夹杂文字时仍能提取JSON-文字正确", text4 == "测试")

    # ---------- 完全无法解析 ----------
    p5 = FakeVisionProvider("我看不清这张图片")
    violate5, reason5, text5 = await check_image(p5, "/tmp/fake5.jpg")
    check("无法解析时不报错且判False", violate5 is False)
    check("无法解析时reason为None", reason5 is None)
    check("无法解析时text为空串", text5 == "")

    # ---------- provider 为 None ----------
    violate6, reason6, text6 = await check_image(None, "/tmp/fake6.jpg")
    check("provider为None时安全返回False", violate6 is False)
    check("provider为None时reason为None", reason6 is None)
    check("provider为None时text为空串", text6 == "")

    # ---------- 默认 Prompt 不需要 {text} 占位符也能正常使用 ----------
    p7 = FakeVisionProvider(
        '{"image_violate": false, "image_reason": "", "extracted_text": ""}'
    )
    await check_image(p7, "/tmp/fake7.jpg", prompt_template=DEFAULT_IMAGE_PROMPT)
    check("默认Prompt不报错", p7.last_kwargs["prompt"] == DEFAULT_IMAGE_PROMPT)
    check(
        "默认Prompt本身不包含{text}占位符（图片走image_urls不嵌入文本）",
        "{text}" not in DEFAULT_IMAGE_PROMPT,
    )

    # ---------- image_reason 为 0 长度字符串时应转为 None ----------
    p8 = FakeVisionProvider(
        '{"image_violate": true, "image_reason": "", "extracted_text": ""}'
    )
    violate8, reason8, _ = await check_image(p8, "/tmp/fake8.jpg")
    check("violate为true但reason是空串时reason归一化为None", reason8 is None)


asyncio.run(run_tests())

print(f"\n共 {passed + failed} 项，通过 {passed}，失败 {failed}")
sys.exit(0 if failed == 0 else 1)
