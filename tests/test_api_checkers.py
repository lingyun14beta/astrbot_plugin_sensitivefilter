# -*- coding: utf-8 -*-
"""对 api_checkers.py 的功能性测试。

- check_via_uapis_profanitycheck: 起一个真实的本地 aiohttp 服务器模拟
  uapis.cn 敏感词检测（快速）接口，覆盖命中/未命中/多词命中/鉴权头/400 几种场景。
- check_via_api: 同样起本地服务器模拟通用第三方审核接口，覆盖 POST/GET、
  自定义字段路径、自定义请求头等场景。

直接运行：python3 test_api_checkers.py
（假设本文件位于插件目录下的 tests/ 子目录中，会自动定位到上一级插件目录）
"""

import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import aiohttp
from aiohttp import web

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

api_checkers = importlib.import_module("astrbot_plugin_sensitivefilter.api_checkers")
_get_by_path = api_checkers._get_by_path
check_via_api = api_checkers.check_via_api
check_via_uapis_profanitycheck = api_checkers.check_via_uapis_profanitycheck

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


# ---------------- 工具函数 ----------------
check("_get_by_path 多层路径", _get_by_path({"a": {"b": {"c": 1}}}, "a.b.c") == 1)
check("_get_by_path 路径不存在", _get_by_path({"a": 1}, "a.b") is None)


# ---------------- 模拟 uapis.cn 敏感词检测（快速）接口 ----------------
async def handle_uapis(request: web.Request):
    data = await request.json()
    text = data.get("text", "")
    auth = request.headers.get("Authorization")
    if not text:
        return web.json_response(
            {
                "code": "INVALID_ARGUMENT",
                "message": "Request body is invalid or text is empty.",
            },
            status=400,
        )
    hit_words = [w for w in ["违禁词", "敏感词"] if w in text]
    if hit_words:
        return web.json_response(
            {
                "status": "forbidden",
                "original_text": text,
                "masked_text": text,
                "forbidden_words": hit_words,
                "echo_auth": auth,
            }
        )
    return web.json_response(
        {
            "status": "passed",
            "original_text": text,
            "masked_text": text,
            "forbidden_words": [],
        }
    )


async def run_uapis_tests():
    app = web.Application()
    app.router.add_post("/profanitycheck", handle_uapis)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}/profanitycheck"

    async with aiohttp.ClientSession() as session:
        # 命中
        hit, reason = await check_via_uapis_profanitycheck(
            session, "这句话里有违禁词出现", api_url=base_url, api_key=""
        )
        check("uapis命中判定正确", hit is True)
        check("uapis命中原因来自forbidden_words", reason == "违禁词")

        # 多个命中词拼接
        hit2, reason2 = await check_via_uapis_profanitycheck(
            session, "违禁词和敏感词都出现了", api_url=base_url, api_key=""
        )
        check("uapis多词命中", hit2 is True)
        check("uapis多词原因拼接正确", reason2 == "违禁词、敏感词")

        # 未命中：status != forbidden
        hit3, reason3 = await check_via_uapis_profanitycheck(
            session, "今天天气真好", api_url=base_url, api_key=""
        )
        check("uapis未命中判定正确", hit3 is False)
        check("uapis未命中原因为None", reason3 is None)

        # 携带api_key时正确发送Authorization头
        captured = {}

        async def handle_check_auth(request: web.Request):
            captured["auth"] = request.headers.get("Authorization")
            await request.json()
            return web.json_response({"status": "passed", "forbidden_words": []})

        app2 = web.Application()
        app2.router.add_post("/auth_check", handle_check_auth)
        runner2 = web.AppRunner(app2)
        await runner2.setup()
        site2 = web.TCPSite(runner2, "127.0.0.1", 0)
        await site2.start()
        port2 = site2._server.sockets[0].getsockname()[1]
        await check_via_uapis_profanitycheck(
            session,
            "随便内容",
            api_url=f"http://127.0.0.1:{port2}/auth_check",
            api_key="my-secret-key",
        )
        check(
            "提供api_key时正确携带Bearer头",
            captured.get("auth") == "Bearer my-secret-key",
        )
        await runner2.cleanup()

        # 未提供api_key时仍能正常调用（不携带Authorization头）
        hit4, _ = await check_via_uapis_profanitycheck(
            session, "无敏感内容", api_url=base_url, api_key=""
        )
        check("未提供api_key时仍能正常调用", hit4 is False)

        # 400（空文本）应被当作未命中而不是异常
        hit5, reason5 = await check_via_uapis_profanitycheck(
            session, "", api_url=base_url, api_key=""
        )
        check("空文本触发400时按未命中处理", hit5 is False)
        check("400情况下reason为None", reason5 is None)

    await runner.cleanup()


# ---------------- 模拟通用外部审核接口（generic 模式） ----------------
async def handle_post(request: web.Request):
    data = await request.json()
    text = data.get("content", "")  # 故意用非默认字段名 "content" 测试自定义 text_field
    auth = request.headers.get("Authorization")
    hit = "敏感" in text
    return web.json_response(
        {
            "result": {"is_violation": hit},
            "msg": "命中关键词" if hit else "",
            "echo_auth": auth,
        }
    )


async def handle_get(request: web.Request):
    text = request.query.get("text", "")
    hit = "敏感" in text
    return web.json_response({"hit": hit, "reason": "GET方式命中" if hit else ""})


async def run_generic_api_tests():
    app = web.Application()
    app.router.add_post("/check", handle_post)
    app.router.add_get("/check_get", handle_get)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"

    async with aiohttp.ClientSession() as session:
        # POST + 自定义字段路径 + 自定义请求头 + 命中
        hit, reason = await check_via_api(
            session,
            "这是一条敏感内容",
            api_url=f"{base_url}/check",
            method="POST",
            headers_json='{"Authorization": "Bearer test-token"}',
            text_field="content",
            hit_path="result.is_violation",
            reason_path="msg",
            timeout=3.0,
        )
        check("POST自定义字段路径-命中", hit is True)
        check("POST自定义字段路径-原因正确", reason == "命中关键词")

        # POST 未命中
        hit2, reason2 = await check_via_api(
            session,
            "这是一条正常内容",
            api_url=f"{base_url}/check",
            method="POST",
            headers_json="{}",
            text_field="content",
            hit_path="result.is_violation",
            reason_path="msg",
        )
        check("POST未命中", hit2 is False)

        # GET 方式命中
        hit3, reason3 = await check_via_api(
            session,
            "含有敏感词的内容",
            api_url=f"{base_url}/check_get",
            method="GET",
            text_field="text",
            hit_path="hit",
            reason_path="reason",
        )
        check("GET方式命中", hit3 is True)
        check("GET方式原因正确", reason3 == "GET方式命中")

        # 错误的headers_json不应崩溃
        hit4, _ = await check_via_api(
            session,
            "正常内容",
            api_url=f"{base_url}/check",
            method="POST",
            headers_json="不是合法JSON",
            text_field="content",
            hit_path="result.is_violation",
        )
        check("非法headers_json不崩溃", hit4 is False)

        # 接口不存在（连接失败）应抛出异常，由调用方在 main.py 里 try/except 兜底
        raised = False
        try:
            await check_via_api(
                session,
                "test",
                api_url="http://127.0.0.1:9/not_exist",  # 不可达端口
                method="POST",
                timeout=1.0,
            )
        except Exception:
            raised = True
        check("不可达地址抛出异常(由main.py兜底)", raised is True)

    await runner.cleanup()


asyncio.run(run_uapis_tests())
asyncio.run(run_generic_api_tests())

print(f"\n共 {passed + failed} 项，通过 {passed}，失败 {failed}")
sys.exit(0 if failed == 0 else 1)
