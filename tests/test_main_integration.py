# -*- coding: utf-8 -*-
"""
对 main.py 的端到端集成测试。

由于完整安装 AstrBot 本体依赖（faiss/sqlalchemy/fastapi 等）成本很高，
这里用一套最小化的 stub 模拟 AstrBot 提供给插件的运行时接口（Star 基类、
filter 装饰器、消息事件对象、Context 等），重点验证“插件自己写的业务逻辑”
是否正确：检测流程、命中后的撤回/警告/通知动作、群级配置覆盖语义、
以及管理指令的实际效果。

注意：这里不验证 AstrBot 框架本身的指令路由/权限校验机制是否正确，
那是 AstrBot 内部已经过测试的部分；我们只验证插件代码在“假设框架按文档
描述的方式调用我们的 handler”时，行为是否符合预期。
"""

import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace

# ============================================================
# 第一步：搭建最小化的 astrbot stub 包
# ============================================================

astrbot_mod = types.ModuleType("astrbot")
astrbot_api_mod = types.ModuleType("astrbot.api")
astrbot_api_event_mod = types.ModuleType("astrbot.api.event")
astrbot_api_event_filter_mod = types.ModuleType("astrbot.api.event.filter")
astrbot_api_star_mod = types.ModuleType("astrbot.api.star")
astrbot_api_msgcomp_mod = types.ModuleType("astrbot.api.message_components")

# ---- logger / AstrBotConfig ----
fake_logger = SimpleNamespace(
    info=lambda *a, **k: None,
    warning=lambda *a, **k: None,
    error=lambda *a, **k: None,
    exception=lambda *a, **k: None,
    debug=lambda *a, **k: None,
)
astrbot_api_mod.logger = fake_logger
astrbot_api_mod.AstrBotConfig = dict  # 仅用于类型标注，运行时用我们自己的 FakeConfig


# ---- event 相关 ----
class AstrMessageEvent:  # 仅用作类型标注的占位基类
    pass


class MessageChain:
    """模拟 AstrBot 的 MessageChain，链式调用收集内容。"""

    def __init__(self):
        self.parts = []
        self.chain = []

    def message(self, text):
        self.parts.append(text)
        return self


astrbot_api_event_mod.AstrMessageEvent = AstrMessageEvent
astrbot_api_event_mod.MessageChain = MessageChain
astrbot_api_event_mod.filter = astrbot_api_event_filter_mod


# ---- filter 子模块：装饰器全部实现为“透传/记录”，不做真实的指令路由 ----
def event_message_type(_type, **kwargs):
    def deco(fn):
        fn._priority = kwargs.get("priority", 0)
        return fn

    return deco


def permission_type(_type):
    def deco(fn):
        fn._requires_admin = True
        return fn

    return deco


class _FakeCommandGroup:
    """模拟 RegisteringCommandable：提供 .command() 子装饰器和 .group() 嵌套子指令组。"""

    def __init__(self, name):
        self.name = name

    def command(self, sub_name):
        def deco(fn):
            fn._command_name = f"{self.name} {sub_name}"
            return fn

        return deco

    def group(self, sub_name):
        def deco(fn):
            return _FakeCommandGroup(f"{self.name} {sub_name}")

        return deco


def command_group(name):
    def deco(fn):
        return _FakeCommandGroup(name)

    return deco


class EventMessageType:
    ALL = "ALL"
    PRIVATE_MESSAGE = "PRIVATE_MESSAGE"
    GROUP_MESSAGE = "GROUP_MESSAGE"


class PermissionType:
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"


astrbot_api_event_filter_mod.event_message_type = event_message_type
astrbot_api_event_filter_mod.permission_type = permission_type
astrbot_api_event_filter_mod.command_group = command_group
astrbot_api_event_filter_mod.EventMessageType = EventMessageType
astrbot_api_event_filter_mod.PermissionType = PermissionType


# ---- star 相关 ----
class Context:
    pass


class Star:
    def __init__(self, context):
        self.context = context


astrbot_api_star_mod.Context = Context
astrbot_api_star_mod.Star = Star


# ---- message_components ----
class At:
    def __init__(self, qq):
        self.qq = qq

    def __repr__(self):
        return f"At(qq={self.qq})"


class Plain:
    def __init__(self, text):
        self.text = text

    def __repr__(self):
        return f"Plain({self.text!r})"


astrbot_api_msgcomp_mod.At = At
astrbot_api_msgcomp_mod.Plain = Plain

# ---- aiocqhttp 撤回相关（main.py 内部懒加载导入） ----
aiocqhttp_pkg = types.ModuleType(
    "astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event"
)


class AiocqhttpMessageEvent(AstrMessageEvent):
    pass


aiocqhttp_pkg.AiocqhttpMessageEvent = AiocqhttpMessageEvent

# ---- 注册进 sys.modules ----
sys.modules["astrbot"] = astrbot_mod
sys.modules["astrbot.api"] = astrbot_api_mod
sys.modules["astrbot.api.event"] = astrbot_api_event_mod
sys.modules["astrbot.api.event.filter"] = astrbot_api_event_filter_mod
sys.modules["astrbot.api.star"] = astrbot_api_star_mod
sys.modules["astrbot.api.message_components"] = astrbot_api_msgcomp_mod
sys.modules["astrbot.core"] = types.ModuleType("astrbot.core")
sys.modules["astrbot.core.platform"] = types.ModuleType("astrbot.core.platform")
sys.modules["astrbot.core.platform.sources"] = types.ModuleType(
    "astrbot.core.platform.sources"
)
sys.modules["astrbot.core.platform.sources.aiocqhttp"] = types.ModuleType(
    "astrbot.core.platform.sources.aiocqhttp"
)
sys.modules["astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event"] = (
    aiocqhttp_pkg
)

# ============================================================
# 第二步：把插件目录加入路径，作为包导入（main.py 用了相对导入 from .xxx import）
# 本文件假设位于插件目录下的 tests/ 子目录中。
# ============================================================
PLUGIN_DIR = (
    Path(__file__).resolve().parent.parent
)  # .../astrbot_plugin_sensitivefilter
PARENT_DIR = PLUGIN_DIR.parent  # 包含插件目录本身的上级目录
sys.path.insert(0, str(PARENT_DIR))

import importlib  # noqa: E402

pkg = types.ModuleType("astrbot_plugin_sensitivefilter")
pkg.__path__ = [str(PLUGIN_DIR)]
sys.modules["astrbot_plugin_sensitivefilter"] = pkg

main_mod = importlib.import_module("astrbot_plugin_sensitivefilter.main")
SensitiveFilterPlugin = main_mod.SensitiveFilterPlugin

# ============================================================
# 第三步：测试用的 FakeConfig / FakeContext / FakeEvent
# ============================================================


class FakeConfig(dict):
    def save_config(self):
        self.saved = True


class FakeProvider:
    def __init__(self, reply_text):
        self.reply_text = reply_text

    async def text_chat(self, prompt, context=None, system_prompt=""):
        return SimpleNamespace(completion_text=self.reply_text)


class FakeContext:
    def __init__(self):
        self.sent_messages = []  # [(umo, chain)]
        self.provider_by_id = {}
        self.using_provider = None

    def get_provider_by_id(self, pid):
        return self.provider_by_id.get(pid)

    def get_using_provider(self, umo=None):
        return self.using_provider

    async def send_message(self, umo, chain):
        self.sent_messages.append((umo, chain))


class FakeAiocqhttpBotApi:
    def __init__(self):
        self.calls = []

    async def call_action(self, action, **kwargs):
        self.calls.append((action, kwargs))
        return {"status": "ok"}


class FakeAiocqhttpBot:
    def __init__(self):
        self.api = FakeAiocqhttpBotApi()


class FakeEvent(main_mod.AstrMessageEvent if False else object):
    """通用平台事件（默认不是 aiocqhttp，用来验证撤回被跳过的情况）。"""

    platform_name = "telegram"

    def __init__(self, group_id, sender_id, sender_name, text, umo=None):
        self._group_id = group_id
        self._sender_id = sender_id
        self._sender_name = sender_name
        self.message_str = text
        self.unified_msg_origin = umo or f"{self.platform_name}:GroupMessage:{group_id}"
        self.sent_results = []
        self.stopped = False
        self.message_obj = SimpleNamespace(message_id="msg-1")

    def get_group_id(self):
        return self._group_id

    def get_sender_id(self):
        return self._sender_id

    def get_sender_name(self):
        return self._sender_name

    def get_platform_name(self):
        return self.platform_name

    def stop_event(self):
        self.stopped = True

    def plain_result(self, text):
        return ("plain", text)

    def chain_result(self, chain):
        return ("chain", chain)

    async def send(self, result):
        self.sent_results.append(result)


class FakeAiocqhttpEvent(FakeEvent, AiocqhttpMessageEvent):
    platform_name = "aiocqhttp"

    def __init__(self, *a, **kw):
        FakeEvent.__init__(self, *a, **kw)
        self.bot = FakeAiocqhttpBot()


# ============================================================
# 测试执行
# ============================================================
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


def set_cfg(config, key, value):
    """测试辅助：按插件的 _KEY_TO_SECTION 映射写入嵌套配置，等价于真实环境
    管理员在 WebUI 分组面板里修改对应字段。"""
    section = main_mod._KEY_TO_SECTION.get(key)
    if section is None:
        config[key] = value
    else:
        config.setdefault(section, {})[key] = value


def make_plugin(extra_config=None):
    config = FakeConfig(
        {
            "group_overrides": [],
            "access_control": {
                "whitelist_enabled": False,
                "whitelist_umos": [],
                "blacklist_enabled": False,
                "blacklist_umos": [],
            },
            "basic": {
                "enabled": True,
                "local_enabled": True,
                "words": ["敏感词", "广告"],
                "case_insensitive": True,
                "fuzzy_match": True,
                "stop_event_on_hit": True,
            },
            "actions": {
                "recall_enabled": True,
                "warn_enabled": True,
                "warn_message": "检测到违规内容，已自动处理（命中来源：{source}）",
                "notify_enabled": False,
                "notify_umos": [],
            },
            "api_detection": {
                "api_enabled": False,
                "api_provider": "uapis_profanitycheck",
                "api_url": "https://uapis.cn/api/v1/text/profanitycheck",
                "api_key": "",
                "api_method": "POST",
                "api_headers": "{}",
                "api_text_field": "text",
                "api_hit_path": "hit",
                "api_reason_path": "reason",
                "api_timeout": 5.0,
            },
            "llm_detection": {
                "llm_enabled": False,
                "llm_provider_id": "",
                "llm_prompt": main_mod.DEFAULT_LLM_PROMPT,
            },
        }
    )
    if extra_config:
        for k, v in extra_config.items():
            set_cfg(config, k, v)
    ctx = FakeContext()
    plugin = SensitiveFilterPlugin(ctx, config)
    return plugin, ctx, config


async def run_tests():
    plugin, ctx, config = make_plugin()

    # ---------- 基础命中：撤回(非aiocqhttp跳过) + 警告 + stop_event ----------
    ev = FakeEvent("group1", "u1", "张三", "这是一条含有敏感词的消息")
    await plugin.on_group_message(ev)
    check("非aiocqhttp平台不报错地跳过撤回", True)  # 没有抛异常即通过
    check("命中后发送了警告消息", len(ev.sent_results) == 1)
    warn_kind, warn_chain = ev.sent_results[0]
    check("警告消息是chain_result", warn_kind == "chain")
    check("警告里@了发送者", any(getattr(c, "qq", None) == "u1" for c in warn_chain))
    check(
        "警告文案里包含来源说明",
        any("本地词库" in getattr(c, "text", "") for c in warn_chain),
    )
    check("命中后事件被stop", ev.stopped is True)

    # ---------- 未命中：不应有任何动作 ----------
    ev_clean = FakeEvent("group1", "u2", "李四", "这是一条很正常的消息")
    await plugin.on_group_message(ev_clean)
    check("未命中不发送消息", len(ev_clean.sent_results) == 0)
    check("未命中不stop_event", ev_clean.stopped is False)

    # ---------- aiocqhttp 平台：应真正调用撤回 ----------
    ev_qq = FakeAiocqhttpEvent("group1", "u3", "王五", "里面有广告内容")
    await plugin.on_group_message(ev_qq)
    check("aiocqhttp平台调用了delete_msg", len(ev_qq.bot.api.calls) == 1)
    action, kwargs = ev_qq.bot.api.calls[0]
    check("调用的是delete_msg", action == "delete_msg")
    check("传入了正确的message_id", kwargs.get("message_id") == "msg-1")

    # ---------- 群级覆盖：本群关闭撤回，仅保留警告 ----------
    ev_qq2 = FakeAiocqhttpEvent("group2", "u4", "赵六", "广告位招租")
    plugin._get_or_create_group_override(ev_qq2.unified_msg_origin)[
        "recall_enabled"
    ] = "关闭"
    await plugin.on_group_message(ev_qq2)
    check("本群覆盖关闭撤回后未调用delete_msg", len(ev_qq2.bot.api.calls) == 0)
    check("撤回关闭但警告仍生效", len(ev_qq2.sent_results) == 1)

    # ---------- 群级覆盖：本群完全关闭插件 ----------
    ev_g3 = FakeEvent("group3", "u5", "孙七", "这是一条含有敏感词的消息")
    plugin._get_or_create_group_override(ev_g3.unified_msg_origin)["enabled"] = "关闭"
    await plugin.on_group_message(ev_g3)
    check(
        "本群关闭插件后完全不检测", len(ev_g3.sent_results) == 0 and not ev_g3.stopped
    )

    # ---------- 群专属词库 ----------
    ev_g4 = FakeEvent("group4", "u6", "周八", "这里出现了群专属违禁词")
    override4 = plugin._get_or_create_group_override(ev_g4.unified_msg_origin)
    override4["extra_words"].append("群专属违禁词")
    plugin._invalidate_group_trie(ev_g4.unified_msg_origin)
    await plugin.on_group_message(ev_g4)
    check("群专属词库命中并警告", len(ev_g4.sent_results) == 1)
    # 全局词库不应包含这个群专属词，换一个群应不命中
    ev_g5 = FakeEvent("group5", "u7", "吴九", "这里出现了群专属违禁词")
    await plugin.on_group_message(ev_g5)
    check("群专属词不影响其他群", len(ev_g5.sent_results) == 0)

    # ---------- 通知功能 ----------
    config["actions"]["notify_enabled"] = True
    config["actions"]["notify_umos"] = ["aiocqhttp:FriendMessage:admin1"]
    ev_notify = FakeEvent("group1", "u8", "郑十", "这是广告")
    await plugin.on_group_message(ev_notify)
    check("通知功能转发到指定umo", len(ctx.sent_messages) == 1)
    notify_umo, notify_chain = ctx.sent_messages[0]
    check("通知umo正确", notify_umo == "aiocqhttp:FriendMessage:admin1")
    check(
        "通知内容包含命中词",
        any("广告" in p for p in notify_chain.parts),
    )

    # ---------- LLM 检测路径 ----------
    config["llm_detection"]["llm_enabled"] = True
    config["basic"]["words"] = []  # 清空本地词库，确保命中来自LLM
    plugin._rebuild_global_trie()
    ctx.using_provider = FakeProvider('{"violate": true, "reason": "疑似诈骗信息"}')
    ev_llm = FakeEvent("group6", "u9", "钱十一", "加我私聊领取奖品")
    await plugin.on_group_message(ev_llm)
    check("LLM检测命中后发出警告", len(ev_llm.sent_results) == 1)
    warn_kind2, warn_chain2 = ev_llm.sent_results[0]
    check(
        "LLM命中原因体现在警告文案中",
        any("AI 语义检测" in getattr(c, "text", "") for c in warn_chain2),
    )

    # ---------- 外部接口检测：uapis_profanitycheck ----------
    async def handle_uapis_e2e(request):
        from aiohttp import web as _web

        data = await request.json()
        text = data.get("text", "")
        if "诈骗" in text:
            return _web.json_response(
                {"status": "forbidden", "forbidden_words": ["诈骗"]}
            )
        return _web.json_response({"status": "passed", "forbidden_words": []})

    from aiohttp import web as _web

    app = _web.Application()
    app.router.add_post("/profanitycheck", handle_uapis_e2e)
    runner = _web.AppRunner(app)
    await runner.setup()
    site = _web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    config["api_detection"]["api_enabled"] = True
    config["api_detection"]["api_provider"] = "uapis_profanitycheck"
    config["api_detection"]["api_url"] = f"http://127.0.0.1:{port}/profanitycheck"
    config["api_detection"]["api_key"] = ""
    config["llm_detection"]["llm_enabled"] = False
    config["basic"]["words"] = []
    plugin._rebuild_global_trie()

    ev_api = FakeEvent("group7", "u10", "冯十二", "这是一个诈骗信息")
    await plugin.on_group_message(ev_api)
    check("uapis接口检测端到端命中并发出警告", len(ev_api.sent_results) == 1)
    warn_kind3, warn_chain3 = ev_api.sent_results[0]
    check(
        "外部接口命中原因体现在警告文案中",
        any("外部接口" in getattr(c, "text", "") for c in warn_chain3),
    )

    ev_api_clean = FakeEvent("group7", "u11", "陈十三", "今天天气真好")
    await plugin.on_group_message(ev_api_clean)
    check("uapis接口检测未命中不警告", len(ev_api_clean.sent_results) == 0)

    await runner.cleanup()
    config["api_detection"]["api_enabled"] = False

    # ---------- 管理指令：全局增删词 ----------
    config["basic"]["words"] = ["敏感词", "广告"]
    plugin._rebuild_global_trie()
    gen = plugin.cmd_add_word(ev, "新违禁词")
    results = [r async for r in gen]
    check("添加全局词后有反馈", len(results) == 1)
    check("新词已生效", "新违禁词" in config["basic"]["words"])
    check(
        "trie已重建包含新词",
        plugin.global_trie.find_first("含有新违禁词的句子") == "新违禁词",
    )

    gen2 = plugin.cmd_del_word(ev, "新违禁词")
    [r async for r in gen2]
    check("删除全局词后新词不在列表中", "新违禁词" not in config["basic"]["words"])

    # ---------- 管理指令：设置群覆盖 ----------
    gen3 = plugin.cmd_set_group_option(ev, "撤回", "off")
    results3 = [r async for r in gen3]
    check("设置指令有反馈", len(results3) == 1)
    check(
        "设置后群覆盖确实写入",
        plugin._find_group_override(ev.unified_msg_origin).get("recall_enabled")
        == "关闭",
    )
    check(
        "群覆盖条目带有正确的__template_key",
        plugin._find_group_override(ev.unified_msg_origin).get("__template_key")
        == "group_override",
    )
    check(
        "群覆盖条目用umo而不是群号作为匹配字段",
        plugin._find_group_override(ev.unified_msg_origin).get("umo")
        == ev.unified_msg_origin,
    )

    # ---------- 管理指令：状态查询 ----------
    gen4 = plugin.cmd_status(ev)
    status_results = [r async for r in gen4]
    check("状态指令返回一条消息", len(status_results) == 1)
    status_text = status_results[0][1]
    check("状态文案包含群号", "group1" in status_text)
    check("状态文案体现撤回已被覆盖关闭", "命中后撤回: 关闭" in status_text)
    check("状态文案包含umo", ev.unified_msg_origin in status_text)

    # ---------- 群覆盖：模拟从 WebUI 直接编辑（不经过指令）也能生效 ----------
    # WebUI 添加条目时会自动带上 __template_key，这里手动模拟同样的写法，
    # 验证插件读取覆盖配置时不依赖“一定是指令创建的”这个假设。
    ev_webui = FakeAiocqhttpEvent("group6", "u12", "webui用户", "这里有webui添加的词")
    config["group_overrides"].append(
        {
            "__template_key": "group_override",
            "umo": ev_webui.unified_msg_origin,
            "enabled": "跟随全局",
            "local_enabled": "跟随全局",
            "api_enabled": "跟随全局",
            "llm_enabled": "跟随全局",
            "recall_enabled": "关闭",
            "warn_enabled": "开启",
            "notify_enabled": "跟随全局",
            "extra_words": ["webui添加的词"],
        }
    )
    await plugin.on_group_message(ev_webui)
    check(
        "WebUI直接写入的群覆盖也能被检测到",
        len(ev_webui.sent_results) == 1,
    )
    check(
        "WebUI写入的recall_enabled覆盖生效（未调用delete_msg）",
        len(ev_webui.bot.api.calls) == 0,
    )

    # ---------- 群覆盖：重复设置同一个会话不会产生重复条目 ----------
    before_count = len(plugin._get_group_overrides())
    plugin._get_or_create_group_override(ev.unified_msg_origin)
    plugin._get_or_create_group_override(ev.unified_msg_origin)
    after_count = len(plugin._get_group_overrides())
    check("重复获取同一会话的覆盖条目不会重复创建", before_count == after_count)

    # ---------- 群覆盖：全部恢复默认后应自动清理空条目 ----------
    gen5 = plugin.cmd_set_group_option(ev, "撤回", "默认")
    _ = [r async for r in gen5]
    override1 = plugin._find_group_override(ev.unified_msg_origin)
    check(
        "全部恢复跟随全局且无专属词后条目被清理",
        override1 is None,
    )

    # ---------- 访问控制：_is_umo_allowed 单元逻辑 ----------
    # 1. 两者都关闭：默认全部允许
    check(
        "白名单黑名单都关闭时默认允许",
        plugin._is_umo_allowed("any:umo:1") is True,
    )

    # 2. 仅黑名单开启
    config["access_control"]["blacklist_enabled"] = True
    config["access_control"]["blacklist_umos"] = ["blocked:umo:1"]
    check(
        "仅黑名单开启-命中黑名单被拒绝",
        plugin._is_umo_allowed("blocked:umo:1") is False,
    )
    check(
        "仅黑名单开启-未命中黑名单仍允许",
        plugin._is_umo_allowed("other:umo:1") is True,
    )

    # 3. 仅白名单开启（纯允许列表模式）
    config["access_control"]["blacklist_enabled"] = False
    config["access_control"]["whitelist_enabled"] = True
    config["access_control"]["whitelist_umos"] = ["allowed:umo:1"]
    check(
        "仅白名单开启-命中白名单允许",
        plugin._is_umo_allowed("allowed:umo:1") is True,
    )
    check(
        "仅白名单开启-未命中白名单被拒绝",
        plugin._is_umo_allowed("other:umo:2") is False,
    )

    # 4. 两者都开启，且同一个umo同时在白名单和黑名单：白名单优先
    config["access_control"]["blacklist_enabled"] = True
    config["access_control"]["whitelist_umos"] = ["both:umo:1"]
    config["access_control"]["blacklist_umos"] = ["both:umo:1"]
    check(
        "两者都开启且同时命中两个名单-白名单优先放行",
        plugin._is_umo_allowed("both:umo:1") is True,
    )
    check(
        "两者都开启-不在白名单的会被拒绝(即使也不在黑名单)",
        plugin._is_umo_allowed("neither:umo:1") is False,
    )

    # 重置访问控制配置，避免影响后续测试
    config["access_control"]["whitelist_enabled"] = False
    config["access_control"]["whitelist_umos"] = []
    config["access_control"]["blacklist_enabled"] = False
    config["access_control"]["blacklist_umos"] = []

    # ---------- 访问控制：端到端验证 on_group_message 会被总闸拦截 ----------
    config["access_control"]["whitelist_enabled"] = True
    config["access_control"][
        "whitelist_umos"
    ] = []  # 故意留空，模拟"只允许白名单"但没人在里面
    ev_blocked = FakeEvent(
        "group_blocked", "u20", "黑名单测试", "这是一条含有敏感词的消息"
    )
    await plugin.on_group_message(ev_blocked)
    check(
        "白名单模式下未列入白名单的群完全不被处理",
        len(ev_blocked.sent_results) == 0 and not ev_blocked.stopped,
    )

    config["access_control"]["whitelist_umos"] = [ev_blocked.unified_msg_origin]
    ev_allowed = FakeEvent(
        "group_blocked",
        "u21",
        "白名单测试",
        "这是一条含有敏感词的消息",
        umo=ev_blocked.unified_msg_origin,
    )
    await plugin.on_group_message(ev_allowed)
    check(
        "加入白名单后同一会话恢复正常检测",
        len(ev_allowed.sent_results) == 1,
    )
    config["access_control"]["whitelist_enabled"] = False
    config["access_control"]["whitelist_umos"] = []

    # ---------- 访问控制：指令管理 ----------
    gen_wl_on = plugin.cmd_whitelist_on(ev)
    r_wl_on = [r async for r in gen_wl_on]
    check("白名单开启指令有反馈", len(r_wl_on) == 1)
    check(
        "白名单开启指令确实写入配置",
        config["access_control"]["whitelist_enabled"] is True,
    )

    gen_wl_add = plugin.cmd_whitelist_add(ev)
    r_wl_add = [r async for r in gen_wl_add]
    check("添加本群到白名单有反馈", len(r_wl_add) == 1)
    check(
        "本群umo被加入白名单列表",
        ev.unified_msg_origin in config["access_control"]["whitelist_umos"],
    )

    gen_wl_add_dup = plugin.cmd_whitelist_add(ev)
    r_wl_add_dup = [r async for r in gen_wl_add_dup]
    check("重复添加同一群提示已存在", "已经在" in r_wl_add_dup[0][1])

    gen_wl_list = plugin.cmd_whitelist_list(ev)
    r_wl_list = [r async for r in gen_wl_list]
    check(
        "白名单列表指令输出包含本群umo",
        ev.unified_msg_origin in r_wl_list[0][1],
    )

    gen_wl_remove = plugin.cmd_whitelist_remove(ev)
    r_wl_remove = [r async for r in gen_wl_remove]
    check("移出白名单有反馈", len(r_wl_remove) == 1)
    check(
        "本群umo已从白名单列表移除",
        ev.unified_msg_origin not in config["access_control"]["whitelist_umos"],
    )

    gen_wl_off = plugin.cmd_whitelist_off(ev)
    [r async for r in gen_wl_off]
    check(
        "白名单关闭指令确实写入配置",
        config["access_control"]["whitelist_enabled"] is False,
    )

    gen_bl_on = plugin.cmd_blacklist_on(ev)
    [r async for r in gen_bl_on]
    check(
        "黑名单开启指令确实写入配置",
        config["access_control"]["blacklist_enabled"] is True,
    )

    gen_bl_add = plugin.cmd_blacklist_add(ev)
    [r async for r in gen_bl_add]
    check(
        "本群umo被加入黑名单列表",
        ev.unified_msg_origin in config["access_control"]["blacklist_umos"],
    )

    gen_bl_list = plugin.cmd_blacklist_list(ev)
    r_bl_list = [r async for r in gen_bl_list]
    check(
        "黑名单列表指令输出包含本群umo",
        ev.unified_msg_origin in r_bl_list[0][1],
    )

    gen_bl_remove = plugin.cmd_blacklist_remove(ev)
    [r async for r in gen_bl_remove]
    check(
        "本群umo已从黑名单列表移除",
        ev.unified_msg_origin not in config["access_control"]["blacklist_umos"],
    )

    gen_bl_off = plugin.cmd_blacklist_off(ev)
    [r async for r in gen_bl_off]
    check(
        "黑名单关闭指令确实写入配置",
        config["access_control"]["blacklist_enabled"] is False,
    )

    # ---------- 嵌套配置读写：_cfg / _set_cfg ----------
    check(
        "_cfg正确从basic分组读取",
        plugin._cfg("case_insensitive") is True,
    )
    check(
        "_cfg正确从api_detection分组读取",
        plugin._cfg("api_provider") == "uapis_profanitycheck",
    )
    plugin._set_cfg("warn_message", "自定义警告：{word}")
    check(
        "_set_cfg写入后落在actions分组里",
        config["actions"]["warn_message"] == "自定义警告：{word}",
    )
    check(
        "_set_cfg写入后_cfg能读到同一个值",
        plugin._cfg("warn_message") == "自定义警告：{word}",
    )

    await plugin.terminate()


asyncio.run(run_tests())
print(f"\n共 {passed + failed} 项，通过 {passed}，失败 {failed}")
sys.exit(0 if failed == 0 else 1)
