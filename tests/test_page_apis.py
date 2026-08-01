"""
对 Web 管理页（Pages）后端 API 的单元测试。

与 test_main_integration.py 一样使用最小化 astrbot stub，并额外 stub
astrbot.api.web（json_response / error_response / request），使 main.py 顶部的
守卫导入生效（_WEB_API_AVAILABLE=True），从而验证：

    1. _register_web_apis 注册的路由数量与前缀是否正确
    2. 每个 page_* handler 在合法/非法输入下的行为
    3. 页面操作与指令操作读写的是同一份配置（词库、分群配置、名单）
    4. 回归保护：空分群配置保存后条目保留（页面走显式删除，不自动清理）

运行方式：python3 tests/test_page_apis.py（或 tests/run_all.py 统一跑）
"""

import asyncio
import sys
import types
from pathlib import Path

# ============================================================
# 第一步：搭建最小化的 astrbot stub 包（含 astrbot.api.web）
# ============================================================

astrbot_mod = types.ModuleType("astrbot")
astrbot_api_mod = types.ModuleType("astrbot.api")
astrbot_api_event_mod = types.ModuleType("astrbot.api.event")
astrbot_api_event_filter_mod = types.ModuleType("astrbot.api.event.filter")
astrbot_api_star_mod = types.ModuleType("astrbot.api.star")
astrbot_api_msgcomp_mod = types.ModuleType("astrbot.api.message_components")
astrbot_api_web_mod = types.ModuleType("astrbot.api.web")


class FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass


astrbot_api_mod.logger = FakeLogger()
astrbot_api_mod.AstrBotConfig = dict  # 仅用于类型标注


class AstrMessageEvent:
    pass


class MessageChain:
    def __init__(self):
        self.parts = []

    def message(self, text):
        self.parts.append(text)
        return self


astrbot_api_event_mod.AstrMessageEvent = AstrMessageEvent
astrbot_api_event_mod.MessageChain = MessageChain
astrbot_api_event_mod.filter = astrbot_api_event_filter_mod


def event_message_type(_type, **kwargs):
    def deco(fn):
        return fn

    return deco


def permission_type(_type):
    def deco(fn):
        return fn

    return deco


class _FakeCommandGroup:
    def __init__(self, name):
        self.name = name

    def command(self, sub_name):
        def deco(fn):
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


astrbot_api_event_filter_mod.event_message_type = event_message_type
astrbot_api_event_filter_mod.permission_type = permission_type
astrbot_api_event_filter_mod.command_group = command_group
astrbot_api_event_filter_mod.EventMessageType = types.SimpleNamespace(
    ALL="ALL", PRIVATE_MESSAGE="PRIVATE_MESSAGE", GROUP_MESSAGE="GROUP_MESSAGE"
)
astrbot_api_event_filter_mod.PermissionType = types.SimpleNamespace(
    ADMIN="ADMIN", MEMBER="MEMBER"
)


class Context:
    pass


class Star:
    def __init__(self, context):
        self.context = context


astrbot_api_star_mod.Context = Context
astrbot_api_star_mod.Star = Star


class _Comp:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class At(_Comp):
    pass


class Plain(_Comp):
    def __init__(self, text):
        super().__init__(text=text)


class Image(_Comp):
    async def convert_to_file_path(self):
        return getattr(self, "path", "")


class Forward(_Comp):
    pass


astrbot_api_msgcomp_mod.At = At
astrbot_api_msgcomp_mod.Plain = Plain
astrbot_api_msgcomp_mod.Image = Image
astrbot_api_msgcomp_mod.Forward = Forward


# ---- astrbot.api.web：响应包装 + 可控的 request 代理 ----
class FakeResponse:
    """模拟 json_response / error_response 的返回值，让测试可以直接检查内容。"""

    def __init__(self, body, status_code=200, error=False):
        self.body = body
        self.status_code = status_code
        self.error = error


def json_response(data=None, *, status_code=200, headers=None):
    return FakeResponse({} if data is None else data, status_code)


def error_response(message, *, status_code=400, data=None, headers=None):
    return FakeResponse(
        {"status": "error", "message": message, "data": data},
        status_code,
        error=True,
    )


class FakeRequest:
    """模拟 astrbot.api.web 的 request 代理，payload 由测试用例按需设置。"""

    def __init__(self):
        self.payload = None

    async def json(self, default=None):
        return self.payload if self.payload is not None else default


fake_request = FakeRequest()

astrbot_api_web_mod.json_response = json_response
astrbot_api_web_mod.error_response = error_response
astrbot_api_web_mod.request = fake_request

# ---- 注册进 sys.modules ----
sys.modules["astrbot"] = astrbot_mod
sys.modules["astrbot.api"] = astrbot_api_mod
sys.modules["astrbot.api.event"] = astrbot_api_event_mod
sys.modules["astrbot.api.event.filter"] = astrbot_api_event_filter_mod
sys.modules["astrbot.api.star"] = astrbot_api_star_mod
sys.modules["astrbot.api.message_components"] = astrbot_api_msgcomp_mod
sys.modules["astrbot.api.web"] = astrbot_api_web_mod

# ============================================================
# 第二步：作为包导入插件 main 模块（需在 web stub 就位之后，
# 这样 main.py 顶部的守卫导入才会判定 _WEB_API_AVAILABLE=True）
# ============================================================
PLUGIN_DIR = Path(__file__).resolve().parent.parent
PARENT_DIR = PLUGIN_DIR.parent
sys.path.insert(0, str(PARENT_DIR))

import importlib

pkg = types.ModuleType("astrbot_plugin_sensitivefilter")
pkg.__path__ = [str(PLUGIN_DIR)]
sys.modules["astrbot_plugin_sensitivefilter"] = pkg

main_mod = importlib.import_module("astrbot_plugin_sensitivefilter.main")
SensitiveFilterPlugin = main_mod.SensitiveFilterPlugin

# ============================================================
# 第三步：FakeConfig / FakeContext
# ============================================================


class FakeConfig(dict):
    def save_config(self):
        self.saved = True


class FakeContext:
    def __init__(self):
        self.registered_routes = []  # [(route, methods, desc)]

    def register_web_api(self, route, view_handler, methods, desc):
        self.registered_routes.append((route, view_handler, methods, desc))

    def get_provider_by_id(self, pid):
        return None

    def get_using_provider(self, umo=None):
        return None

    async def send_message(self, umo, chain):
        pass


def set_cfg(config, key, value):
    """按插件的 _KEY_TO_SECTION 映射写入嵌套配置。"""
    section = main_mod._KEY_TO_SECTION.get(key)
    if section is None:
        config[key] = value
    else:
        config.setdefault(section, {})[key] = value


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


# ============================================================
# 测试主体
# ============================================================


async def main():
    context = FakeContext()
    config = FakeConfig()
    plugin = SensitiveFilterPlugin(context, config)

    try:
        # ----------------------------------------------------
        # 1. 路由注册
        # ----------------------------------------------------
        routes = context.registered_routes
        check("web stub 生效（_WEB_API_AVAILABLE=True）", main_mod._WEB_API_AVAILABLE)
        check("共注册 10 条页面 API 路由", len(routes) == 10)
        check(
            "所有路由都带 /{插件名}/page 前缀",
            all(
                r[0].startswith("/astrbot_plugin_sensitivefilter/page/")
                for r in routes
            ),
        )
        check(
            "路由都绑定了 handler 且带描述",
            all(callable(r[1]) and r[3] for r in routes),
        )

        # ----------------------------------------------------
        # 2. 总览
        # ----------------------------------------------------
        set_cfg(config, "words", ["测试词A", "测试词B"])
        plugin._rebuild_global_trie()
        set_cfg(config, "llm_batch_size", "非数字")  # 故意写脏数据
        resp = await plugin.page_overview()
        check("overview 不报错（脏数字配置有容错）", not resp.error)
        check("overview.words 计数正确", resp.body["counts"]["words"] == 2)
        check(
            "overview.batch.size 回退默认值",
            resp.body["batch"]["size"] == 10
            and resp.body["batch"]["max_wait_minutes"] == 30,
        )
        check(
            "overview 结构完整",
            {"global", "counts", "access", "batch"} <= set(resp.body.keys()),
        )
        set_cfg(config, "llm_batch_size", 10)

        # ----------------------------------------------------
        # 3. 全局词库
        # ----------------------------------------------------
        resp = await plugin.page_list_words()
        check("words 列表返回现有词", resp.body["words"] == ["测试词A", "测试词B"])

        fake_request.payload = {"words": ["新词1", " 新词2 ", "新词1", "", "测试词A"]}
        resp = await plugin.page_add_words()
        check(
            "add 批量添加并去重/去空/跳过已存在",
            not resp.error
            and resp.body["added"] == ["新词1", "新词2"]
            and resp.body["skipped"] == ["新词1", "测试词A"]  # 批内重复 + 已存在
            and resp.body["total"] == 4,
        )
        check("add 后配置已更新", len(set_cfg_words(config)) == 4)

        fake_request.payload = {"words": ["测试词A"]}
        resp = await plugin.page_add_words()
        check("add 全部已存在时返回错误", resp.error)

        fake_request.payload = {"word": "单个词"}
        resp = await plugin.page_add_words()
        check("add 兼容单个 word 字段", not resp.error and resp.body["added"] == ["单个词"])

        fake_request.payload = {"words": "单个字符串也接受"}
        resp = await plugin.page_add_words()
        check(
            "add 字符串按单个词处理（兼容设计）",
            not resp.error and resp.body["added"] == ["单个字符串也接受"],
        )

        fake_request.payload = {"words": 123}
        resp = await plugin.page_add_words()
        check("add 非法类型返回错误", resp.error)

        fake_request.payload = {"word": "新词1"}
        resp = await plugin.page_delete_word()
        check("delete 删除成功", not resp.error and resp.body["deleted"] == "新词1")

        fake_request.payload = {"word": "不存在的词"}
        resp = await plugin.page_delete_word()
        check("delete 不存在的词返回错误", resp.error)

        # 词库变化应重建 Trie：页面添加的词立刻能被命中测试命中
        fake_request.payload = {"text": "这句话包含单个词在里面"}
        resp = await plugin.page_test_text()
        check(
            "页面加词后 Trie 已重建（命中测试可见）",
            not resp.error and resp.body["hit"] and resp.body["word"] == "单个词",
        )

        # ----------------------------------------------------
        # 4. 分群配置
        # ----------------------------------------------------
        umo = "aiocqhttp:GroupMessage:123456"
        fake_request.payload = {
            "umo": umo,
            "settings": {"recall_enabled": "关闭", "mute_enabled": "开启"},
            "extra_words": [" 群专属词 ", ""],
        }
        resp = await plugin.page_save_group()
        override = plugin._find_group_override(umo)
        check("groups/save 创建条目", not resp.error and override is not None)
        check(
            "groups/save 写入 __template_key（WebUI 配置页兼容）",
            override.get("__template_key") == "group_override",
        )
        check(
            "groups/save 三态值与专属词清洗正确",
            override["recall_enabled"] == "关闭"
            and override["mute_enabled"] == "开启"
            and override["extra_words"] == ["群专属词"],
        )

        resp = await plugin.page_list_groups()
        check(
            "groups 列表返回条目与元信息",
            len(resp.body["groups"]) == 1
            and resp.body["groups"][0]["umo"] == umo
            and "跟随全局" in resp.body["tristate_options"],
        )

        # 群专属词应能被命中测试命中（Trie 已失效重建）
        fake_request.payload = {"text": "这是一条群专属词消息", "umo": umo}
        resp = await plugin.page_test_text()
        check(
            "群专属词经页面保存后即时生效",
            resp.body["hit"] and resp.body["source"] == "本群专属词库",
        )

        # 回归保护：全部恢复“跟随全局”且无专属词时，页面保存不自动清理条目
        fake_request.payload = {
            "umo": umo,
            "settings": {"recall_enabled": "跟随全局", "mute_enabled": "跟随全局"},
            "extra_words": [],
        }
        resp = await plugin.page_save_group()
        check(
            "回归：空配置保存后条目保留（页面走显式删除）",
            not resp.error and plugin._find_group_override(umo) is not None,
        )

        fake_request.payload = {"umo": umo, "settings": {"不存在的key": "开启"}}
        resp = await plugin.page_save_group()
        check("groups/save 未知设置项报错", resp.error)

        fake_request.payload = {"umo": umo, "settings": {"recall_enabled": "非法值"}}
        resp = await plugin.page_save_group()
        check("groups/save 非法三态值报错", resp.error)

        fake_request.payload = {"umo": "", "settings": {}}
        resp = await plugin.page_save_group()
        check("groups/save 空 umo 报错", resp.error)

        fake_request.payload = {"umo": umo}
        resp = await plugin.page_delete_group()
        check(
            "groups/delete 删除成功",
            not resp.error and plugin._find_group_override(umo) is None,
        )

        fake_request.payload = {"umo": umo}
        resp = await plugin.page_delete_group()
        check("groups/delete 不存在的条目报错", resp.error)

        # ----------------------------------------------------
        # 5. 名单管理
        # ----------------------------------------------------
        fake_request.payload = None
        resp = await plugin.page_list_access_lists()
        check(
            "lists 返回三类名单",
            {"whitelist", "blacklist", "user"} <= set(resp.body.keys()),
        )

        fake_request.payload = {"list": "whitelist", "add": umo}
        resp = await plugin.page_save_access_list()
        check(
            "lists/save 添加白名单",
            not resp.error and resp.body["added"] is True and umo in resp.body["items"],
        )

        fake_request.payload = {"list": "whitelist", "add": umo}
        resp = await plugin.page_save_access_list()
        check("lists/save 重复添加返回 added=False", resp.body["added"] is False)

        fake_request.payload = {"list": "whitelist", "enabled": True}
        resp = await plugin.page_save_access_list()
        check(
            "lists/save 切换启用开关",
            not resp.error
            and resp.body["enabled"] is True
            and plugin._cfg("whitelist_enabled") is True,
        )

        fake_request.payload = {"list": "user", "add": "10001"}
        resp = await plugin.page_save_access_list()
        check(
            "lists/save 用户白名单走用户 ID 逻辑",
            resp.body["added"] is True and plugin._is_user_whitelisted("10001"),
        )

        fake_request.payload = {"list": "user", "remove": "10001"}
        resp = await plugin.page_save_access_list()
        check(
            "lists/save 移除用户",
            resp.body["removed"] is True and not plugin._is_user_whitelisted("10001"),
        )

        fake_request.payload = {"list": "不存在的名单", "add": "x"}
        resp = await plugin.page_save_access_list()
        check("lists/save 非法名单名报错", resp.error)

        # ----------------------------------------------------
        # 6. 命中测试
        # ----------------------------------------------------
        fake_request.payload = {"text": ""}
        resp = await plugin.page_test_text()
        check("test 空文本报错", resp.error)

        fake_request.payload = {"text": "完全正常的聊天内容"}
        resp = await plugin.page_test_text()
        check("test 未命中返回 hit=False", not resp.error and resp.body["hit"] is False)

        # 页面操作与指令读写同一份配置：直接改配置后页面接口立即可见
        set_cfg(config, "words", ["配置层词"])
        plugin._rebuild_global_trie()
        fake_request.payload = {"text": "试试配置层词"}
        resp = await plugin.page_test_text()
        check(
            "test 命中全局词库（与配置同源）",
            resp.body["hit"] and resp.body["source"] == "全局词库",
        )
    finally:
        fake_request.payload = None
        await plugin.terminate()


def set_cfg_words(config):
    """读取当前 words 配置（与插件 _cfg 同路径）。"""
    return (config.get("basic") or {}).get("words", [])


asyncio.run(main())

print(f"\n通过 {passed} 项，失败 {failed} 项")
sys.exit(0 if failed == 0 else 1)
