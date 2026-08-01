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
import json
import sys
import tempfile
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
class FakeLogger:
    def __init__(self):
        self.info_entries = []

    def info(self, *args, **kwargs):
        self.info_entries.append((args, kwargs))

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass


fake_logger = FakeLogger()
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
        self.chain.append(Plain(text))
        return self

    def url_image(self, url):
        image = Image.fromURL(url)
        self.parts.append(image)
        self.chain.append(image)
        return self

    def file_image(self, path):
        image = Image.fromFileSystem(path)
        self.parts.append(image)
        self.chain.append(image)
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


class Image:
    """模拟 Comp.Image，提供 convert_to_file_path()，与真实实现一样统一
    把任意来源（URL/本地路径/base64）的图片转换成一个可用的本地路径。"""

    def __init__(self, file=None, url=None, path=None):
        self.file = file or ""
        self.url = url or ""
        self.path = path or ""

    @staticmethod
    def fromURL(url):
        return Image(file=url, url=url)

    @staticmethod
    def fromFileSystem(path):
        return Image(file=path, path=path)

    async def convert_to_file_path(self):
        if self.path:
            return self.path
        return self.url or self.file

    def __repr__(self):
        return f"Image(file={self.file!r})"


class Forward:
    def __init__(self, id):
        self.id = id

    def __repr__(self):
        return f"Forward(id={self.id!r})"


astrbot_api_msgcomp_mod.At = At
astrbot_api_msgcomp_mod.Plain = Plain
astrbot_api_msgcomp_mod.Image = Image
astrbot_api_msgcomp_mod.Forward = Forward

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

import importlib

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
        self.last_prompt = None

    async def text_chat(self, prompt, context=None, system_prompt=""):
        self.last_prompt = prompt
        return SimpleNamespace(completion_text=self.reply_text)


class FakeVisionProvider:
    """模拟支持视觉输入的 LLM Provider，记录最近一次调用收到的 image_urls。"""

    def __init__(self, reply_text):
        self.reply_text = reply_text
        self.last_image_urls = None

    async def text_chat(self, prompt, context=None, system_prompt="", image_urls=None):
        self.last_image_urls = image_urls
        return SimpleNamespace(completion_text=self.reply_text)


class FakeContext:
    def __init__(self):
        self.sent_messages = []  # [(umo, chain)]
        self.provider_by_id = {}
        self.using_provider = None
        self.web_apis = []  # [(path, handler, methods, desc)]

    def get_provider_by_id(self, pid):
        return self.provider_by_id.get(pid)

    def get_using_provider(self, umo=None):
        return self.using_provider

    async def send_message(self, umo, chain):
        self.sent_messages.append((umo, chain))

    def register_web_api(self, path, handler, methods, desc):
        self.web_apis.append((path, handler, methods, desc))


class FakeAiocqhttpBotApi:
    def __init__(self, forward_responses=None):
        self.calls = []
        self.forward_responses = forward_responses or {}

    async def call_action(self, action, **kwargs):
        self.calls.append((action, kwargs))
        if action == "get_forward_msg":
            response = self.forward_responses.get(kwargs["id"], {"messages": []})
            if isinstance(response, Exception):
                raise response
            return response
        return {"status": "ok"}


class FakeAiocqhttpBot:
    def __init__(self, forward_responses=None):
        self.api = FakeAiocqhttpBotApi(forward_responses)


class FakeEvent(main_mod.AstrMessageEvent if False else object):
    """通用平台事件（默认不是 aiocqhttp，用来验证撤回被跳过的情况）。"""

    platform_name = "telegram"

    def __init__(self, group_id, sender_id, sender_name, text, umo=None, images=None):
        self._group_id = group_id
        self._sender_id = sender_id
        self._sender_name = sender_name
        self.message_str = text
        self.unified_msg_origin = umo or f"{self.platform_name}:GroupMessage:{group_id}"
        self.sent_results = []
        self.stopped = False
        message_chain = list(images or [])
        if text:
            message_chain.append(Plain(text))
        self.message_obj = SimpleNamespace(message_id="msg-1", message=message_chain)

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
        forward_responses = kw.pop("forward_responses", None)
        FakeEvent.__init__(self, *a, **kw)
        self.bot = FakeAiocqhttpBot(forward_responses)


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


def make_plugin(extra_config=None, records_file=None):
    if records_file is None:
        records_file = (
            Path(tempfile.mkdtemp(prefix="sensitivefilter-test-")) / "records.json"
        )
    SensitiveFilterPlugin._violation_records_file = str(records_file)
    config = FakeConfig(
        {
            "group_overrides": [],
            "access_control": {
                "whitelist_enabled": False,
                "whitelist_umos": [],
                "blacklist_enabled": False,
                "blacklist_umos": [],
            },
            "user_access_control": {
                "user_whitelist_enabled": True,
                "user_whitelist_ids": [],
            },
            "basic": {
                "enabled": True,
                "local_enabled": True,
                "words": ["敏感词", "广告"],
                "case_insensitive": True,
                "fuzzy_match": True,
                "stop_event_on_hit": True,
                "qq_forward_debug": False,
            },
            "actions": {
                "recall_enabled": True,
                "warn_enabled": True,
                "warn_message": main_mod.DEFAULT_WARN_MESSAGE,
                "notify_enabled": False,
                "notify_umos": [],
                "notify_message": main_mod.DEFAULT_NOTIFY_MESSAGE,
                "mute_enabled": False,
                "mute_first_duration_seconds": 60,
                "mute_second_duration_seconds": 300,
                "mute_third_duration_seconds": 86400,
                "mute_reset_hour": 0,
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
                "llm_batch_enabled": False,
                "llm_batch_size": 3,
                "llm_batch_max_wait_minutes": 60,
                "llm_batch_prompt": main_mod.DEFAULT_LLM_BATCH_PROMPT,
            },
            "image_detection": {
                "image_enabled": False,
                "image_provider_id": "",
                "image_prompt": main_mod.DEFAULT_IMAGE_PROMPT,
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

    # ---------- 额外插件页面 Web API 注册（不是 _conf_schema 设置 UI） ----------
    registered_web_paths = {item[0] for item in ctx.web_apis}
    check(
        "额外插件页面注册统计 API",
        "/astrbot_plugin_sensitivefilter/stats" in registered_web_paths,
    )
    check(
        "额外插件页面注册分群配置保存 API",
        "/astrbot_plugin_sensitivefilter/group_overrides/save" in registered_web_paths,
    )
    check(
        "额外插件页面注册违规记录 API",
        "/astrbot_plugin_sensitivefilter/records" in registered_web_paths,
    )
    check(
        "额外插件页面注册被撤回用户 API",
        "/astrbot_plugin_sensitivefilter/moderation_users" in registered_web_paths,
    )
    check(
        "额外插件页面注册 CSV 导出 API",
        "/astrbot_plugin_sensitivefilter/records/export" in registered_web_paths,
    )
    check(
        "额外插件页面注册今日统计 API",
        "/astrbot_plugin_sensitivefilter/today_stats" in registered_web_paths,
    )
    check(
        "额外插件页面注册仪表盘趋势 API",
        "/astrbot_plugin_sensitivefilter/dashboard/trend" in registered_web_paths,
    )
    stats_handler = next(
        handler
        for path, handler, _methods, _desc in ctx.web_apis
        if path == "/astrbot_plugin_sensitivefilter/stats"
    )
    stats_payload = await stats_handler()
    check("额外插件页面统计 API 返回成功", stats_payload["status"] == "success")
    check(
        "额外插件页面统计包含全局词库数量",
        stats_payload["data"]["global_words_count"] == 2,
    )
    check(
        "额外插件页面统计包含违规记录数量",
        stats_payload["data"]["records_count"] == 0,
    )

    # ---------- 违规记录：无限条数、持久化留存、一键清空 ----------
    with tempfile.TemporaryDirectory() as tmpdir:
        records_path = Path(tmpdir) / "violation_records.json"
        persistent_plugin, _persistent_ctx, _persistent_config = make_plugin(
            records_file=records_path
        )
        for idx in range(505):
            persistent_plugin._append_violation_record(
                umo="telegram:GroupMessage:persist",
                group_id="persist",
                sender_id=f"user-{idx % 3}",
                sender_name=f"用户{idx % 3}",
                hit_word="测试原因" + str(idx),
                source="测试",
                original_text="测试消息" + str(idx),
                violation_count=idx + 1,
                recall_executed=True,
                mute_duration=0,
            )
        check(
            "违规记录不再按500条裁剪", len(persistent_plugin._violation_records) == 505
        )
        data = json.loads(records_path.read_text(encoding="utf-8"))
        check("违规记录写入持久化文件", len(data) == 505)
        reloaded_plugin, _ctx_reload, _cfg_reload = make_plugin(
            records_file=records_path
        )
        check("插件重载后保留违规记录", len(reloaded_plugin._violation_records) == 505)
        records_resp = await reloaded_plugin._web_records()
        check(
            "违规记录 API 返回全部记录",
            records_resp["total"] == 505 and len(records_resp["data"]) == 505,
        )
        check(
            "违规记录包含可查看原因字段",
            records_resp["data"][0]["reason"].startswith("测试原因"),
        )
        await reloaded_plugin._web_records_clear()
        check("一键清空会清空内存记录", reloaded_plugin._violation_records == [])
        check(
            "一键清空会清空持久化文件",
            json.loads(records_path.read_text(encoding="utf-8")) == [],
        )
    if hasattr(SensitiveFilterPlugin, "_violation_records_file"):
        delattr(SensitiveFilterPlugin, "_violation_records_file")

    # ---------- 基础命中：撤回(非aiocqhttp跳过) + 警告 + stop_event ----------
    ev = FakeEvent("group1", "u1", "张三", "这是一条含有敏感词的消息")
    await plugin.on_group_message(ev)
    check("非aiocqhttp平台不报错地跳过撤回", True)  # 没有抛异常即通过
    check("命中后发送了警告消息", len(ev.sent_results) == 1)
    warn_kind, warn_chain = ev.sent_results[0]
    check("警告消息是chain_result", warn_kind == "chain")
    check("警告里@了发送者", any(getattr(c, "qq", None) == "u1" for c in warn_chain))
    check(
        "警告文案里包含敏感词变量",
        any("检测到的敏感词：敏感词" in getattr(c, "text", "") for c in warn_chain),
    )
    check(
        "警告文案里包含违规次数变量",
        any("违规次数：第1次" in getattr(c, "text", "") for c in warn_chain),
    )
    check("命中后事件被stop", ev.stopped is True)
    records_handler = next(
        handler
        for path, handler, _methods, _desc in ctx.web_apis
        if path == "/astrbot_plugin_sensitivefilter/records"
    )
    records_payload = await records_handler()
    check("违规记录 API 返回成功", records_payload["status"] == "success")
    check(
        "违规记录 API 记录命中词",
        records_payload["data"][0]["forbidden_words"] == "敏感词",
    )
    users_handler = next(
        handler
        for path, handler, _methods, _desc in ctx.web_apis
        if path == "/astrbot_plugin_sensitivefilter/moderation_users"
    )
    users_payload = await users_handler()
    check("被撤回用户 API 返回成功", users_payload["status"] == "success")
    check("被撤回用户 API 聚合用户", users_payload["data"][0]["user_id"] == "u1")
    export_handler = next(
        handler
        for path, handler, _methods, _desc in ctx.web_apis
        if path == "/astrbot_plugin_sensitivefilter/records/export"
    )
    csv_body, csv_status, csv_headers = await export_handler()
    check("违规记录 CSV 导出成功", csv_status == 200)
    check("违规记录 CSV 包含被撤回内容", "这是一条含有敏感词的消息" in csv_body)
    check(
        "违规记录 CSV 下载文件名正确",
        "sensitivefilter_records.csv" in csv_headers.get("Content-Disposition", ""),
    )
    stats_payload_after_hit = await stats_handler()
    check(
        "总览统计违规记录数量随命中增加",
        stats_payload_after_hit["data"]["records_count"] == 1,
    )
    today_handler = next(
        handler
        for path, handler, _methods, _desc in ctx.web_apis
        if path == "/astrbot_plugin_sensitivefilter/today_stats"
    )
    today_payload = await today_handler()
    check(
        "今日统计 API 返回群排行",
        today_payload["data"]["group_ranking"][0]["count"] == 1,
    )
    trend_handler = next(
        handler
        for path, handler, _methods, _desc in ctx.web_apis
        if path == "/astrbot_plugin_sensitivefilter/dashboard/trend"
    )
    trend_payload = await trend_handler()
    check("仪表盘趋势 API 返回列表", isinstance(trend_payload["data"], list))

    # ---------- 用户白名单：命中用户 ID 后跳过所有审查，不撤回/警告/stop ----------
    plugin_allow_user, _ctx_allow_user, _cfg_allow_user = make_plugin(
        {"user_whitelist_ids": ["u-allow"]}
    )
    ev_allow_user = FakeEvent(
        "group1", "u-allow", "白名单用户", "这里出现敏感词但应因用户白名单放行"
    )
    await plugin_allow_user.on_group_message(ev_allow_user)
    check("用户白名单命中后不发送警告", len(ev_allow_user.sent_results) == 0)
    check("用户白名单命中后不stop_event", ev_allow_user.stopped is False)

    plugin_user_whitelist_off, _ctx_user_whitelist_off, _cfg_user_whitelist_off = (
        make_plugin(
            {"user_whitelist_enabled": False, "user_whitelist_ids": ["u-allow"]}
        )
    )
    ev_user_whitelist_off = FakeEvent(
        "group1", "u-allow", "白名单关闭用户", "用户白名单关闭时敏感词仍应检测"
    )
    await plugin_user_whitelist_off.on_group_message(ev_user_whitelist_off)
    check(
        "用户白名单关闭后名单用户仍会被审查",
        len(ev_user_whitelist_off.sent_results) == 1,
    )
    check("用户白名单关闭后命中会stop_event", ev_user_whitelist_off.stopped is True)

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

    # ---------- 自动禁言：aiocqhttp 按同一周期内违规次数执行阶梯禁言 ----------
    plugin_mute, _ctx_mute, _cfg_mute = make_plugin(
        {
            "mute_enabled": True,
            "mute_first_duration_seconds": 60,
            "mute_second_duration_seconds": 120,
            "mute_third_duration_seconds": 86400,
            "recall_enabled": False,
            "warn_enabled": False,
        }
    )
    ev_mute_1 = FakeAiocqhttpEvent("10001", "20002", "禁言测试", "敏感词 第一次")
    ev_mute_2 = FakeAiocqhttpEvent("10001", "20002", "禁言测试", "敏感词 第二次")
    ev_mute_3 = FakeAiocqhttpEvent("10001", "20002", "禁言测试", "敏感词 第三次")
    await plugin_mute.on_group_message(ev_mute_1)
    await plugin_mute.on_group_message(ev_mute_2)
    await plugin_mute.on_group_message(ev_mute_3)
    check(
        "自动禁言第一次使用第一档时长",
        ev_mute_1.bot.api.calls
        == [("set_group_ban", {"group_id": 10001, "user_id": 20002, "duration": 60})],
    )
    check(
        "自动禁言第二次使用第二档时长",
        ev_mute_2.bot.api.calls
        == [("set_group_ban", {"group_id": 10001, "user_id": 20002, "duration": 120})],
    )
    check(
        "自动禁言第三次及以上使用第三档时长",
        ev_mute_3.bot.api.calls
        == [
            ("set_group_ban", {"group_id": 10001, "user_id": 20002, "duration": 86400})
        ],
    )

    # ---------- 自动禁言：总开关关闭时不调用 set_group_ban ----------
    plugin_mute_off, _ctx_mute_off, _cfg_mute_off = make_plugin(
        {"mute_enabled": False, "recall_enabled": False, "warn_enabled": False}
    )
    ev_mute_off = FakeAiocqhttpEvent("10001", "20002", "禁言测试", "敏感词")
    await plugin_mute_off.on_group_message(ev_mute_off)
    check("自动禁言关闭时不调用set_group_ban", ev_mute_off.bot.api.calls == [])

    # ---------- 新模板变量：群内警告 + 管理员通知 ----------
    plugin_tpl, ctx_tpl, _cfg_tpl = make_plugin(
        {
            "mute_enabled": True,
            "mute_first_duration_seconds": 60,
            "notify_enabled": True,
            "notify_umos": ["aiocqhttp:FriendMessage:admin"],
            "recall_enabled": True,
            "warn_enabled": True,
        }
    )
    ev_tpl = FakeAiocqhttpEvent(
        "10001", "20002", "模板用户", "这是一条含有敏感词的消息"
    )
    await plugin_tpl.on_group_message(ev_tpl)
    tpl_warn_text = "".join(getattr(c, "text", "") for c in ev_tpl.sent_results[0][1])
    check("新警告模板渲染forbidden_words", "检测到的敏感词：敏感词" in tpl_warn_text)
    check("新警告模板渲染violation_count", "违规次数：第1次" in tpl_warn_text)
    check(
        "管理员通知发送到配置的umo",
        ctx_tpl.sent_messages[0][0] == "aiocqhttp:FriendMessage:admin",
    )
    notify_text = ctx_tpl.sent_messages[0][1].parts[0]
    check("管理员通知模板渲染群号", "群聊：10001" in notify_text)
    check("管理员通知模板渲染用户", "用户：模板用户 (20002)" in notify_text)
    check("管理员通知模板渲染敏感词", "敏感词：敏感词" in notify_text)
    check("管理员通知模板渲染原文", "原文：这是一条含有敏感词的消息" in notify_text)
    check("管理员通知模板渲染禁言时长", "处理：禁言60秒，消息已撤回" in notify_text)
    check("管理员通知模板渲染时间", "时间：" in notify_text)

    # ---------- 撤回状态模板变量：非 aiocqhttp 平台撤回失败，recall_status 为空 ----------
    plugin_no_recall, ctx_no_recall, _cfg_no_recall = make_plugin(
        {
            "mute_enabled": False,
            "notify_enabled": True,
            "notify_umos": ["aiocqhttp:FriendMessage:admin"],
            "recall_enabled": True,
            "warn_enabled": False,
        }
    )
    ev_no_recall = FakeEvent("group-norecall", "u99", "撤回失败用户", "敏感词")
    await plugin_no_recall.on_group_message(ev_no_recall)
    notify_no_recall = ctx_no_recall.sent_messages[0][1].parts[0]
    check(
        "非aiocqhttp平台撤回失败通知不含消息已撤回",
        "消息已撤回" not in notify_no_recall,
    )
    check(
        "非aiocqhttp平台撤回失败通知仍有处理字段", "处理：禁言0秒" in notify_no_recall
    )

    # ---------- 撤回关闭时不显示消息已撤回 ----------
    plugin_recall_off, ctx_recall_off, _cfg_recall_off = make_plugin(
        {
            "mute_enabled": False,
            "notify_enabled": True,
            "notify_umos": ["aiocqhttp:FriendMessage:admin"],
            "recall_enabled": False,
            "warn_enabled": False,
        }
    )
    ev_recall_off = FakeAiocqhttpEvent("group-off", "u98", "撤回关闭用户", "敏感词")
    await plugin_recall_off.on_group_message(ev_recall_off)
    notify_recall_off = ctx_recall_off.sent_messages[0][1].parts[0]
    check("撤回关闭通知不含消息已撤回", "消息已撤回" not in notify_recall_off)

    plugin_custom_tpl, _ctx_custom_tpl, _cfg_custom_tpl = make_plugin(
        {
            "warn_message": "词={forbidden_words};原文={original_text};脱敏={masked_text};次数={violation_count}",
            "recall_enabled": False,
            "warn_enabled": True,
        }
    )
    ev_custom_tpl = FakeEvent(
        "group-custom", "u-custom", "自定义模板用户", "包含敏感词的原文"
    )
    await plugin_custom_tpl.on_group_message(ev_custom_tpl)
    custom_warn_text = "".join(
        getattr(c, "text", "") for c in ev_custom_tpl.sent_results[0][1]
    )
    check(
        "自定义警告模板可渲染所有新变量",
        "词=敏感词;原文=包含敏感词的原文;脱敏=包含***的原文;次数=1" in custom_warn_text,
    )

    # ---------- QQ OneBot 合并转发：递归展开文本，但处罚当前转发发送者 ----------
    forward_responses = {
        "root-forward": {
            "messages": [
                {
                    "type": "node",
                    "data": {
                        "user_id": "original-user-1",
                        "nickname": "原作者一",
                        "content": [{"type": "text", "data": {"text": "正常内容"}}],
                    },
                },
                {
                    "type": "node",
                    "data": {
                        "user_id": "original-user-2",
                        "nickname": "原作者二",
                        "content": [
                            {"type": "forward", "data": {"id": "nested-forward"}}
                        ],
                    },
                },
            ]
        },
        "nested-forward": {
            "messages": [
                {
                    "type": "node",
                    "data": {
                        "user_id": "original-user-3",
                        "nickname": "原作者三",
                        "content": [{"type": "text", "data": {"text": "广告内容"}}],
                    },
                }
            ]
        },
    }
    set_cfg(config, "qq_forward_debug", True)
    ev_forward = FakeAiocqhttpEvent(
        "group-forward",
        "forward-sender",
        "转发者",
        "",
        images=[Forward("root-forward")],
        forward_responses=forward_responses,
    )
    await plugin.on_group_message(ev_forward)
    check(
        "QQ合并转发递归请求root和nested ID",
        [
            call[1].get("id")
            for call in ev_forward.bot.api.calls
            if call[0] == "get_forward_msg"
        ]
        == [
            "root-forward",
            "nested-forward",
        ],
    )
    check("QQ合并转发内部敏感词会命中", ev_forward.stopped is True)
    check(
        "QQ合并转发命中后撤回当前转发卡",
        [call for call in ev_forward.bot.api.calls if call[0] == "delete_msg"]
        == [("delete_msg", {"message_id": "msg-1"})],
    )
    check(
        "QQ合并转发命中后警告当前发送者而非节点作者",
        any(
            getattr(component, "qq", None) == "forward-sender"
            for component in ev_forward.sent_results[0][1]
        ),
    )
    check(
        "QQ合并转发调试日志包含嵌套节点文本",
        any(
            "nested-forward" in args[0] and "广告内容" in args[0]
            for args, _ in fake_logger.info_entries
        ),
    )
    set_cfg(config, "qq_forward_debug", False)

    # ---------- QQ OneBot 合并转发：NapCat 将节点返回为完整 message 事件 ----------
    event_style_forward_responses = {
        "event-style-forward": {
            "messages": [
                {
                    "user_id": "original-event-user",
                    "sender": {"nickname": "原作者事件格式"},
                    "message": [{"type": "text", "data": {"text": "广告内容"}}],
                }
            ]
        }
    }
    ev_event_style_forward = FakeAiocqhttpEvent(
        "group-event-style-forward",
        "event-style-sender",
        "事件格式转发者",
        "",
        images=[Forward("event-style-forward")],
        forward_responses=event_style_forward_responses,
    )
    await plugin.on_group_message(ev_event_style_forward)
    check(
        "QQ合并转发兼容完整OneBot message事件节点",
        ev_event_style_forward.stopped is True,
    )
    check(
        "完整OneBot message事件节点命中后撤回当前转发卡",
        [
            call
            for call in ev_event_style_forward.bot.api.calls
            if call[0] == "delete_msg"
        ]
        == [("delete_msg", {"message_id": "msg-1"})],
    )

    # ---------- QQ OneBot 合并转发：节点图片复用现有视觉审核 ----------
    config["image_detection"]["image_enabled"] = True
    config["image_detection"]["image_provider_id"] = "forward-vision"
    config["actions"]["notify_enabled"] = True
    config["actions"]["notify_umos"] = ["aiocqhttp:FriendMessage:forward-image-admin"]
    sent_before_forward_image_notify = len(ctx.sent_messages)
    ctx.provider_by_id["forward-vision"] = FakeVisionProvider(
        '{"image_violate": true, "image_reason": "违规图片", "extracted_text": ""}'
    )
    forward_image_responses = {
        "image-forward": {
            "messages": [
                {
                    "type": "node",
                    "data": {
                        "user_id": "original-image-user",
                        "content": [
                            {
                                "type": "image",
                                "data": {"url": "https://example.com/forward.jpg"},
                            }
                        ],
                    },
                }
            ]
        }
    }
    ev_forward_image = FakeAiocqhttpEvent(
        "group-forward-image",
        "forward-image-sender",
        "图片转发者",
        "",
        images=[Forward("image-forward")],
        forward_responses=forward_image_responses,
    )
    await plugin.on_group_message(ev_forward_image)
    check("QQ合并转发节点图片会触发视觉审核", ev_forward_image.stopped is True)
    check(
        "QQ合并转发节点图片传给视觉Provider",
        ctx.provider_by_id["forward-vision"].last_image_urls
        == ["https://example.com/forward.jpg"],
    )
    check(
        "QQ合并转发节点图片命中后撤回当前转发卡",
        [call for call in ev_forward_image.bot.api.calls if call[0] == "delete_msg"]
        == [("delete_msg", {"message_id": "msg-1"})],
    )
    forward_image_notify_umo, forward_image_notify_chain = ctx.sent_messages[
        sent_before_forward_image_notify
    ]
    check(
        "QQ合并转发节点图片命中后通知到配置会话",
        forward_image_notify_umo == "aiocqhttp:FriendMessage:forward-image-admin",
    )
    check(
        "QQ合并转发节点图片通知包含命中图片",
        any(
            isinstance(part, Image) and part.file == "https://example.com/forward.jpg"
            for part in forward_image_notify_chain.parts
        ),
    )
    config["actions"]["notify_enabled"] = False
    config["actions"]["notify_umos"] = []
    config["image_detection"]["image_enabled"] = False
    config["image_detection"]["image_provider_id"] = ""

    # ---------- QQ OneBot 合并转发：异常、深层 node 与拉取上限不会中断处理 ----------
    ev_broken_forward = FakeAiocqhttpEvent(
        "group-broken-forward",
        "broken-sender",
        "异常转发者",
        "",
        images=[Forward("broken-forward")],
        forward_responses={"broken-forward": RuntimeError("协议端故障")},
    )
    await plugin.on_group_message(ev_broken_forward)
    check(
        "QQ合并转发接口异常时安全跳过内部内容",
        ev_broken_forward.bot.api.calls
        == [("get_forward_msg", {"id": "broken-forward"})]
        and not ev_broken_forward.stopped,
    )

    deeply_nested_node = {"type": "text", "data": {"text": "广告内容"}}
    for _ in range(main_mod._QQ_FORWARD_MAX_COMPONENT_DEPTH + 2):
        deeply_nested_node = {
            "type": "node",
            "data": {"user_id": "nested-user", "content": [deeply_nested_node]},
        }
    ev_deep_node = FakeAiocqhttpEvent(
        "group-deep-node",
        "deep-sender",
        "深层节点转发者",
        "",
        images=[Forward("deep-node-forward")],
        forward_responses={"deep-node-forward": {"messages": [deeply_nested_node]}},
    )
    await plugin.on_group_message(ev_deep_node)
    check(
        "QQ合并转发深层node在组件深度上限处安全停止",
        ev_deep_node.bot.api.calls == [("get_forward_msg", {"id": "deep-node-forward"})]
        and not ev_deep_node.stopped,
    )

    wide_forward_responses = {
        "wide-root": {
            "messages": [
                {
                    "type": "node",
                    "data": {
                        "content": [
                            {"type": "forward", "data": {"id": f"wide-{index}"}}
                            for index in range(30)
                        ]
                    },
                }
            ]
        }
    }
    wide_forward_responses.update(
        {f"wide-{index}": {"messages": []} for index in range(30)}
    )
    ev_wide_forward = FakeAiocqhttpEvent(
        "group-wide-forward",
        "wide-sender",
        "分叉转发者",
        "",
        images=[Forward("wide-root")],
        forward_responses=wide_forward_responses,
    )
    await plugin.on_group_message(ev_wide_forward)
    check(
        "QQ合并转发总拉取次数受上限保护",
        len(ev_wide_forward.bot.api.calls) == main_mod._QQ_FORWARD_MAX_FETCHES,
    )

    # ---------- 非 aiocqhttp 平台不请求 OneBot API ----------
    ev_other_forward = FakeEvent(
        "group-other-forward",
        "other-sender",
        "其他平台用户",
        "",
        images=[Forward("root-forward")],
    )
    await plugin.on_group_message(ev_other_forward)
    check(
        "非aiocqhttp的Forward组件不会触发QQ合并转发处理",
        not ev_other_forward.stopped and not ev_other_forward.sent_results,
    )

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

    # ---------- 群级覆盖：全局关闭时本群仍可单独开启插件 ----------
    config["basic"]["enabled"] = False
    ev_g3_enabled = FakeEvent(
        "group3-enabled", "u5-enabled", "孙七", "这是一条含有敏感词的消息"
    )
    plugin._get_or_create_group_override(ev_g3_enabled.unified_msg_origin)[
        "enabled"
    ] = "开启"
    await plugin.on_group_message(ev_g3_enabled)
    check(
        "全局关闭时本群覆盖开启后仍会检测",
        len(ev_g3_enabled.sent_results) == 1 and ev_g3_enabled.stopped,
    )
    config["basic"]["enabled"] = True

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
    sent_before_notify = len(ctx.sent_messages)
    ev_notify = FakeEvent("group1", "u8", "郑十", "这是广告")
    await plugin.on_group_message(ev_notify)
    check("通知功能转发到指定umo", len(ctx.sent_messages) == sent_before_notify + 1)
    notify_umo, notify_chain = ctx.sent_messages[sent_before_notify]
    check("通知umo正确", notify_umo == "aiocqhttp:FriendMessage:admin1")
    check(
        "通知内容包含命中词",
        any("广告" in p for p in notify_chain.parts if isinstance(p, str)),
    )

    # ---------- LLM 检测路径 ----------
    config["llm_detection"]["llm_enabled"] = True
    config["basic"]["words"] = []  # 清空本地词库，确保命中来自LLM
    plugin._rebuild_global_trie()
    ctx.using_provider = FakeProvider('{"violate": true, "reason": "疑似诈骗信息"}')
    ev_llm = FakeEvent("group6", "u9", "钱十一", "加我私聊领取奖品")
    await plugin.on_group_message(ev_llm)
    check("LLM检测命中后发出警告", len(ev_llm.sent_results) == 1)
    _warn_kind2, warn_chain2 = ev_llm.sent_results[0]
    check(
        "LLM命中原因体现在警告文案中",
        any("疑似诈骗信息" in getattr(c, "text", "") for c in warn_chain2),
    )
    config["llm_detection"]["llm_enabled"] = False

    # ---------- 用户白名单：批量队列等待期间命中用户白名单后不处罚 ----------
    plugin_batch_user_allow, ctx_batch_user_allow, _cfg_batch_user_allow = make_plugin(
        {
            "llm_enabled": True,
            "llm_batch_enabled": True,
            "llm_batch_size": 2,
            "user_whitelist_ids": ["batch-allow"],
        }
    )
    ctx_batch_user_allow.using_provider = FakeProvider(
        '{"results":[{"index":0,"violate":true,"reason":"批量违规"},{"index":1,"violate":true,"reason":"批量违规"}]}'
    )
    ev_batch_user_allow = FakeEvent(
        "group-batch-user", "batch-allow", "白名单批量用户", "批量敏感词1"
    )
    ev_batch_user_normal = FakeEvent(
        "group-batch-user", "batch-normal", "普通批量用户", "批量敏感词2"
    )
    await plugin_batch_user_allow.on_group_message(ev_batch_user_allow)
    await plugin_batch_user_allow.on_group_message(ev_batch_user_normal)
    check(
        "批量审核中用户白名单命中者不处罚",
        len(ev_batch_user_allow.sent_results) == 0
        and ev_batch_user_allow.stopped is False,
    )
    check(
        "批量审核中非白名单用户仍处罚",
        len(ev_batch_user_normal.sent_results) == 1
        and ev_batch_user_normal.stopped is True,
    )

    # ---------- AI 语义检测：批量审核（按数量触发） ----------
    config["llm_detection"]["llm_enabled"] = True
    config["llm_detection"]["llm_batch_enabled"] = True
    config["llm_detection"]["llm_batch_size"] = 3
    batch_provider = FakeProvider(
        '{"results": ['
        '{"index": 0, "violate": false, "reason": ""}, '
        '{"index": 1, "violate": true, "reason": "广告引流"}, '
        '{"index": 2, "violate": false, "reason": ""}'
        "]}"
    )
    ctx.using_provider = batch_provider

    ev_b1 = FakeEvent("group_batch", "u40", "批量甲", "今天天气不错")
    ev_b2 = FakeEvent("group_batch", "u41", "批量乙", "加我微信领取奖品")
    ev_b3 = FakeEvent("group_batch", "u42", "批量丙", "中午吃什么")

    await plugin.on_group_message(ev_b1)
    check(
        "凑不齐batch_size前不会立刻有任何动作-第1条",
        len(ev_b1.sent_results) == 0 and not ev_b1.stopped,
    )
    check(
        "队列里确实攒了这条消息",
        len(plugin._llm_batches.get(ev_b1.unified_msg_origin, [])) == 1,
    )

    await plugin.on_group_message(ev_b2)
    check("凑不齐batch_size前不会立刻有任何动作-第2条", len(ev_b2.sent_results) == 0)

    await plugin.on_group_message(ev_b3)
    # 第3条进来正好凑满 batch_size=3，应该立即触发一次flush
    check(
        "凑满batch_size后队列被清空",
        ev_b1.unified_msg_origin not in plugin._llm_batches,
    )
    check("批量命中的那条(第2条)被警告", len(ev_b2.sent_results) == 1)
    check("批量未命中的不会被警告-第1条", len(ev_b1.sent_results) == 0)
    check("批量未命中的不会被警告-第3条", len(ev_b3.sent_results) == 0)
    _warn_kind_batch, warn_chain_batch = ev_b2.sent_results[0]
    check(
        "批量命中原因体现在警告文案中",
        any("广告引流" in getattr(c, "text", "") for c in warn_chain_batch),
    )
    check(
        "批量prompt里包含三条消息内容",
        "今天天气不错" in batch_provider.last_prompt
        and "加我微信领取奖品" in batch_provider.last_prompt
        and "中午吃什么" in batch_provider.last_prompt,
    )

    # ---------- QQ 合并转发在 AI 批量审核中整体作为一条消息入队 ----------
    forward_batch_responses = {
        "batch-forward": {
            "messages": [
                {
                    "type": "node",
                    "data": {
                        "user_id": "original-batch-user-1",
                        "content": [
                            {"type": "text", "data": {"text": "转发第一段内容"}}
                        ],
                    },
                },
                {
                    "type": "node",
                    "data": {
                        "user_id": "original-batch-user-2",
                        "content": [
                            {"type": "text", "data": {"text": "转发第二段内容"}}
                        ],
                    },
                },
            ]
        }
    }
    forward_batch_provider = FakeProvider(
        '{"results": [{"index": 0, "violate": true, "reason": "合并转发违规"}]}'
    )
    ctx.using_provider = forward_batch_provider
    ev_forward_batch = FakeAiocqhttpEvent(
        "group-forward-batch",
        "forward-batch-sender",
        "批量转发发送者",
        "",
        images=[Forward("batch-forward")],
        forward_responses=forward_batch_responses,
    )
    await plugin.on_group_message(ev_forward_batch)
    check(
        "QQ合并转发批量审核只占一个队列条目",
        len(plugin._llm_batches.get(ev_forward_batch.unified_msg_origin, [])) == 1,
    )
    check(
        "QQ合并转发批量审核不会即时处理",
        len(ev_forward_batch.sent_results) == 0 and not ev_forward_batch.stopped,
    )
    await plugin._flush_llm_batch(ev_forward_batch.unified_msg_origin)
    check(
        "QQ合并转发批量审核输入包含全部节点文本",
        "转发第一段内容" in forward_batch_provider.last_prompt
        and "转发第二段内容" in forward_batch_provider.last_prompt,
    )
    check(
        "QQ合并转发批量审核命中后处理卡片发送者",
        any(
            getattr(component, "qq", None) == "forward-batch-sender"
            for component in ev_forward_batch.sent_results[0][1]
        ),
    )

    # ---------- AI 语义检测：批量审核（超时兜底，不依赖真实sleep） ----------
    ev_b4 = FakeEvent("group_batch2", "u43", "批量丁", "测试超时兜底")
    ctx.using_provider = FakeProvider(
        '{"results": [{"index": 0, "violate": true, "reason": "测试超时命中"}]}'
    )
    await plugin.on_group_message(ev_b4)
    check(
        "未凑满batch_size时不会立刻发出",
        len(ev_b4.sent_results) == 0
        and len(plugin._llm_batches.get(ev_b4.unified_msg_origin, [])) == 1,
    )
    # 手动把这条消息的入队时间往前调，模拟“已经等待超过兜底超时时间”
    bucket = plugin._llm_batches[ev_b4.unified_msg_origin]
    old_item = bucket[0]
    bucket[0] = (old_item[0], old_item[1], old_item[2], old_item[3] - 9999)
    config["llm_detection"]["llm_batch_max_wait_minutes"] = 1
    await plugin._flush_overdue_llm_batches()
    check(
        "超时兜底触发后队列被清空", ev_b4.unified_msg_origin not in plugin._llm_batches
    )
    check("超时兜底触发后命中的消息被警告", len(ev_b4.sent_results) == 1)

    # ---------- 批量队列：入队后关闭本群审核，不再送审或处罚 ----------
    ev_batch_disabled = FakeEvent(
        "group-batch-disabled", "u43-disabled", "批量关闭", "排队后关闭审核"
    )
    ctx.using_provider = FakeProvider(
        '{"results": [{"index": 0, "violate": true, "reason": "不应处罚"}]}'
    )
    await plugin.on_group_message(ev_batch_disabled)
    check(
        "关闭前消息已进入批量队列",
        len(plugin._llm_batches.get(ev_batch_disabled.unified_msg_origin, [])) == 1,
    )
    plugin._get_or_create_group_override(ev_batch_disabled.unified_msg_origin)[
        "llm_enabled"
    ] = "关闭"
    await plugin._flush_llm_batch(ev_batch_disabled.unified_msg_origin)
    check(
        "关闭本群AI审核后队列消息不会被处罚",
        len(ev_batch_disabled.sent_results) == 0,
    )
    check(
        "关闭本群AI审核后队列被丢弃",
        ev_batch_disabled.unified_msg_origin not in plugin._llm_batches,
    )

    # ---------- 精确验证 llm_batch_max_wait_minutes 确实按分钟换算成秒 ----------
    # 不依赖上面那种数千秒的极端偏移量，专门验证“刚好不够 / 刚好超过”这个边界。
    ev_b4b = FakeEvent("group_batch2b", "u43b", "换算边界测试", "测试分钟换算")
    await plugin.on_group_message(ev_b4b)
    bucket_b = plugin._llm_batches[ev_b4b.unified_msg_origin]
    old_item_b = bucket_b[0]
    # 配置 2 分钟兜底，把入队时间往前调 90 秒（1.5 分钟）——还没到 2 分钟，不该被冲掉
    config["llm_detection"]["llm_batch_max_wait_minutes"] = 2
    bucket_b[0] = (old_item_b[0], old_item_b[1], old_item_b[2], old_item_b[3] - 90)
    await plugin._flush_overdue_llm_batches()
    check(
        "未达到分钟换算后的秒数时不会被冲掉",
        ev_b4b.unified_msg_origin in plugin._llm_batches,
    )
    # 再把时间往前调到总共 130 秒（超过 2 分钟=120 秒），这次应该被冲掉
    bucket_b = plugin._llm_batches[ev_b4b.unified_msg_origin]
    old_item_b2 = bucket_b[0]
    bucket_b[0] = (old_item_b2[0], old_item_b2[1], old_item_b2[2], old_item_b2[3] - 40)
    await plugin._flush_overdue_llm_batches()
    check(
        "超过分钟换算后的秒数后队列被冲掉",
        ev_b4b.unified_msg_origin not in plugin._llm_batches,
    )

    # ---------- AI 语义检测：批量审核 - Provider 缺失时不报错，队列被清空 ----------
    config["llm_detection"]["llm_provider_id"] = "not-exist-provider"
    ctx.using_provider = None
    ev_b5 = FakeEvent("group_batch3", "u44", "批量戊", "消息1")
    ev_b6 = FakeEvent("group_batch3", "u45", "批量己", "消息2")
    ev_b7 = FakeEvent("group_batch3", "u46", "批量庚", "消息3")
    await plugin.on_group_message(ev_b5)
    await plugin.on_group_message(ev_b6)
    await plugin.on_group_message(ev_b7)
    check(
        "找不到Provider时不报错且队列仍被清空",
        ev_b5.unified_msg_origin not in plugin._llm_batches,
    )
    check(
        "找不到Provider时不会误判任何消息违规",
        len(ev_b5.sent_results) == 0
        and len(ev_b6.sent_results) == 0
        and len(ev_b7.sent_results) == 0,
    )
    config["llm_detection"]["llm_provider_id"] = ""

    # ---------- AI 语义检测：图片转写出的文字不参与批量，始终即时检测 ----------
    config["llm_detection"]["llm_batch_size"] = 10  # 调大，确保不会因为凑批而被动触发
    config["image_detection"]["image_enabled"] = True
    config["image_detection"]["image_provider_id"] = "vision-batch-test"
    ctx.provider_by_id["vision-batch-test"] = FakeVisionProvider(
        '{"image_violate": false, "image_reason": "", "extracted_text": "图片里的广告文字"}'
    )
    ctx.using_provider = FakeProvider(
        '{"violate": true, "reason": "图片文字命中AI语义检测"}'
    )
    ev_img_text = FakeEvent(
        "group_batch4",
        "u47",
        "批量辛",
        "",
        images=[Image(file="https://example.com/batch.jpg")],
    )
    await plugin.on_group_message(ev_img_text)
    check(
        "图片转写文字不进入批量队列，立即得到结果并警告",
        len(ev_img_text.sent_results) == 1,
    )
    check(
        "图片转写文字检测后批量队列里没有残留",
        ev_img_text.unified_msg_origin not in plugin._llm_batches
        or len(plugin._llm_batches.get(ev_img_text.unified_msg_origin, [])) == 0,
    )
    config["image_detection"]["image_enabled"] = False
    config["image_detection"]["image_provider_id"] = ""

    config["llm_detection"]["llm_batch_enabled"] = False
    config["llm_detection"]["llm_enabled"] = False

    # ---------- 图片检测：没有配置 image_provider_id 时完全不处理图片 ----------
    config["image_detection"]["image_enabled"] = True
    config["image_detection"]["image_provider_id"] = ""  # 故意留空
    ev_img_noprov = FakeEvent(
        "group_img1",
        "u30",
        "图片测试甲",
        "",
        images=[Image(file="https://example.com/a.jpg")],
    )
    await plugin.on_group_message(ev_img_noprov)
    check(
        "未配置image_provider_id时图片检测完全跳过",
        len(ev_img_noprov.sent_results) == 0 and not ev_img_noprov.stopped,
    )

    # ---------- 图片检测：图片内容本身违规 ----------
    ctx.provider_by_id["vision-1"] = FakeVisionProvider(
        '{"image_violate": true, "image_reason": "血腥暴力画面", "extracted_text": ""}'
    )
    config["image_detection"]["image_provider_id"] = "vision-1"
    config["actions"]["notify_enabled"] = True
    config["actions"]["notify_umos"] = ["aiocqhttp:FriendMessage:image-admin"]
    sent_before_image_notify = len(ctx.sent_messages)
    ev_img_violate = FakeEvent(
        "group_img1",
        "u31",
        "图片测试乙",
        "",
        images=[Image(file="https://example.com/b.gif")],
    )
    await plugin.on_group_message(ev_img_violate)
    check("图片内容违规时发出警告", len(ev_img_violate.sent_results) == 1)
    _warn_kind_img, warn_chain_img = ev_img_violate.sent_results[0]
    check(
        "图片违规原因体现在警告文案中",
        any("血腥暴力画面" in getattr(c, "text", "") for c in warn_chain_img),
    )
    check(
        "确实把图片路径传给了视觉Provider",
        ctx.provider_by_id["vision-1"].last_image_urls == ["https://example.com/b.gif"],
    )
    image_notify_umo, image_notify_chain = ctx.sent_messages[sent_before_image_notify]
    check(
        "图片违规命中后通知到配置会话",
        image_notify_umo == "aiocqhttp:FriendMessage:image-admin",
    )
    check(
        "图片违规通知包含命中图片",
        any(
            isinstance(part, Image) and part.file == "https://example.com/b.gif"
            for part in image_notify_chain.parts
        ),
    )

    # ---------- 图片违规时通知原文显示图片标识文案 ----------
    plugin_img_notify, ctx_img_notify, _cfg_img_notify = make_plugin(
        {
            "image_detection": {"image_enabled": True, "image_provider_id": "vision-1"},
            "recall_enabled": False,
            "mute_enabled": False,
            "warn_enabled": False,
            "notify_enabled": True,
            "notify_umos": ["aiocqhttp:FriendMessage:admin"],
        }
    )
    ctx_img_notify.provider_by_id["vision-1"] = FakeVisionProvider(
        '{"image_violate": true, "image_reason": "血腥暴力画面", "extracted_text": ""}'
    )
    ev_img_notify = FakeEvent(
        "group_img1",
        "u31-notify",
        "图片通知测试",
        "",
        images=[Image(file="https://example.com/e.jpg")],
    )
    await plugin_img_notify.on_group_message(ev_img_notify)
    check(
        "图片违规时管理员通知发送到配置的umo",
        ctx_img_notify.sent_messages[0][0] == "aiocqhttp:FriendMessage:admin",
    )
    img_notify_text = ctx_img_notify.sent_messages[0][1].parts[0]
    check(
        "图片违规通知原文显示图片标识",
        "原文：图片消息（已识别到违禁内容）" in img_notify_text,
    )

    # ---------- 图片检测：图片本身不违规，但文字命中本地词库 ----------
    config["basic"]["words"] = ["敏感词", "广告"]
    plugin._rebuild_global_trie()
    ctx.provider_by_id["vision-1"].reply_text = (
        '{"image_violate": false, "image_reason": "", '
        '"extracted_text": "这张图片里写着敏感词"}'
    )
    sent_before_ocr_notify = len(ctx.sent_messages)
    ev_img_text_hit = FakeEvent(
        "group_img1",
        "u32",
        "图片测试丙",
        "",
        images=[Image(file="https://example.com/c.jpg")],
    )
    await plugin.on_group_message(ev_img_text_hit)
    check("图片文字命中本地词库时发出警告", len(ev_img_text_hit.sent_results) == 1)
    _warn_kind_img2, warn_chain_img2 = ev_img_text_hit.sent_results[0]
    check(
        "图片文字识别命中词体现在警告文案中",
        any("敏感词" in getattr(c, "text", "") for c in warn_chain_img2),
    )
    ocr_notify_umo, ocr_notify_chain = ctx.sent_messages[sent_before_ocr_notify]
    check(
        "图片文字识别命中后通知到配置会话",
        ocr_notify_umo == "aiocqhttp:FriendMessage:image-admin",
    )
    check(
        "图片文字识别命中后通知包含原图",
        any(
            isinstance(part, Image) and part.file == "https://example.com/c.jpg"
            for part in ocr_notify_chain.parts
        ),
    )
    config["actions"]["notify_enabled"] = False
    config["actions"]["notify_umos"] = []

    # ---------- 图片检测：图片不违规且没有文字 ----------
    ctx.provider_by_id[
        "vision-1"
    ].reply_text = '{"image_violate": false, "image_reason": "", "extracted_text": ""}'
    ev_img_clean = FakeEvent(
        "group_img1",
        "u33",
        "图片测试丁",
        "",
        images=[Image(file="https://example.com/d.jpg")],
    )
    await plugin.on_group_message(ev_img_clean)
    check("图片完全正常时不发出任何动作", len(ev_img_clean.sent_results) == 0)

    # ---------- 图片检测：群级覆盖关闭图片检测 ----------
    plugin._get_or_create_group_override(ev_img_clean.unified_msg_origin)[
        "image_enabled"
    ] = "关闭"
    ctx.provider_by_id[
        "vision-1"
    ].reply_text = (
        '{"image_violate": true, "image_reason": "测试", "extracted_text": ""}'
    )
    ev_img_overridden_off = FakeEvent(
        "group_img1",
        "u34",
        "图片测试戊",
        "",
        images=[Image(file="https://example.com/e.jpg")],
        umo=ev_img_clean.unified_msg_origin,
    )
    await plugin.on_group_message(ev_img_overridden_off)
    check(
        "本群覆盖关闭图片检测后即使图片违规也不处理",
        len(ev_img_overridden_off.sent_results) == 0,
    )

    config["image_detection"]["image_enabled"] = False
    config["image_detection"]["image_provider_id"] = ""

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
    _warn_kind3, warn_chain3 = ev_api.sent_results[0]
    check(
        "外部接口命中词体现在警告文案中",
        any("诈骗" in getattr(c, "text", "") for c in warn_chain3),
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

    # ---------- 管理指令：手动触发批量发送 ----------
    config["llm_detection"]["llm_enabled"] = True
    config["llm_detection"]["llm_batch_enabled"] = True
    config["llm_detection"]["llm_batch_size"] = 99  # 故意调大，确保不会自动凑满
    config["llm_detection"]["llm_provider_id"] = ""
    ctx.using_provider = FakeProvider(
        '{"results": [{"index": 0, "violate": true, "reason": "手动触发命中"}]}'
    )

    ev_manual = FakeEvent("group_manual", "u50", "手动触发测试", "测试手动批量发送")
    await plugin.on_group_message(ev_manual)
    check(
        "手动触发前队列里确实有这条消息",
        len(plugin._llm_batches.get(ev_manual.unified_msg_origin, [])) == 1,
    )

    gen_flush_empty = plugin.cmd_flush_batch(ev_llm)  # ev_llm对应的会话队列是空的
    r_flush_empty = [r async for r in gen_flush_empty]
    check("对空队列手动触发时有合适提示", "没有" in r_flush_empty[0][1])

    gen_flush = plugin.cmd_flush_batch(ev_manual)
    r_flush = [r async for r in gen_flush]
    check("手动触发批量发送有反馈", len(r_flush) == 1)
    check(
        "手动触发批量发送反馈审核结果",
        "共审核 1 条，命中 1 条" in r_flush[0][1],
    )
    check(
        "手动触发后队列被清空",
        ev_manual.unified_msg_origin not in plugin._llm_batches,
    )
    check("手动触发后命中的消息被警告", len(ev_manual.sent_results) == 1)

    config["llm_detection"]["llm_batch_size"] = 10
    config["llm_detection"]["llm_batch_enabled"] = False
    config["llm_detection"]["llm_enabled"] = False

    # ---------- 插件关闭(terminate)前会把残留队列里的消息做最后一次检测 ----------
    config["llm_detection"]["llm_enabled"] = True
    config["llm_detection"]["llm_batch_enabled"] = True
    config["llm_detection"]["llm_batch_size"] = 99
    ctx.using_provider = FakeProvider(
        '{"results": [{"index": 0, "violate": true, "reason": "关闭前兜底命中"}]}'
    )
    ev_pending = FakeEvent("group_pending", "u51", "关闭前残留测试", "测试关闭前兜底")
    await plugin.on_group_message(ev_pending)
    check(
        "插件关闭前队列里确实还有未处理的消息",
        len(plugin._llm_batches.get(ev_pending.unified_msg_origin, [])) == 1,
    )

    await plugin.terminate()

    check(
        "插件关闭后残留队列里的消息被补做了一次检测",
        len(ev_pending.sent_results) == 1,
    )
    check("插件关闭后队列已清空", len(plugin._llm_batches) == 0)


asyncio.run(run_tests())
print(f"\n共 {passed + failed} 项，通过 {passed}，失败 {failed}")
sys.exit(0 if failed == 0 else 1)
