# -*- coding: utf-8 -*-
"""
群聊敏感词检测插件。

支持三种互相独立、可叠加使用的检测方式：
    1. 本地词库匹配（Trie 树，支持模糊匹配防拆字绕过）
    2. 外部接口检测（适配任意第三方文本审核 API）
    3. AI 语义检测（调用 AstrBot 已配置的 LLM 提供商）

命中后的处理动作（撤回 / 群内警告 / 转发通知）三者互相独立，可分别开关，
并且支持在“全局默认值”基础上为每个群单独覆盖。

群级覆盖配置直接存储在插件配置的 `group_overrides` 字段（template_list 类型）
里，因此管理员既可以在 WebUI 插件配置页里以卡片形式逐群管理，也可以用
`/敏感词 设置` 等指令在群里直接操作——两者读写的是同一份数据，不存在同步问题。
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

import aiohttp

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star
import astrbot.api.message_components as Comp

from .api_checkers import (
    UAPIS_PROFANITYCHECK_URL,
    check_via_api,
    check_via_uapis_profanitycheck,
)
from .image_checker import DEFAULT_IMAGE_PROMPT, check_image
from .llm_checker import DEFAULT_LLM_PROMPT, check_via_llm
from .word_matcher import WordTrie, normalize_text

# 群配置中支持“跟随全局 / 单独覆盖”的布尔类开关
_OVERRIDABLE_BOOL_KEYS = (
    "enabled",
    "local_enabled",
    "api_enabled",
    "llm_enabled",
    "image_enabled",
    "recall_enabled",
    "warn_enabled",
    "notify_enabled",
)

# 每个全局配置 key 所属的分组（对应 _conf_schema.json 里的顶层 object）。
# _conf_schema.json 用嵌套分组来呈现更清晰的 WebUI 界面，实际存储路径变为
# self.config[section][key]；这里统一维护映射关系，避免在各处硬编码路径。
_KEY_TO_SECTION = {
    "whitelist_enabled": "access_control",
    "whitelist_umos": "access_control",
    "blacklist_enabled": "access_control",
    "blacklist_umos": "access_control",
    "enabled": "basic",
    "local_enabled": "basic",
    "words": "basic",
    "case_insensitive": "basic",
    "fuzzy_match": "basic",
    "stop_event_on_hit": "basic",
    "recall_enabled": "actions",
    "warn_enabled": "actions",
    "warn_message": "actions",
    "notify_enabled": "actions",
    "notify_umos": "actions",
    "api_enabled": "api_detection",
    "api_provider": "api_detection",
    "api_url": "api_detection",
    "api_key": "api_detection",
    "api_method": "api_detection",
    "api_headers": "api_detection",
    "api_text_field": "api_detection",
    "api_hit_path": "api_detection",
    "api_reason_path": "api_detection",
    "api_timeout": "api_detection",
    "llm_enabled": "llm_detection",
    "llm_provider_id": "llm_detection",
    "llm_prompt": "llm_detection",
    "image_enabled": "image_detection",
    "image_provider_id": "image_detection",
    "image_prompt": "image_detection",
}

# group_overrides（template_list）里唯一的模板名。WebUI 新增条目、以及插件自己
# 通过指令创建条目时，都必须在 __template_key 写入这个值，否则 WebUI 会提示
# “找不到对应模板”。
_GROUP_OVERRIDE_TEMPLATE_KEY = "group_override"

# 群覆盖里的三态开关：字符串「跟随全局/开启/关闭」 <-> Python 的 None/True/False。
# 之所以不用普通 bool，是因为需要表达“这个群没有单独设置，跟随全局默认值”这第三种状态。
_TRISTATE_TO_BOOL = {"跟随全局": None, "开启": True, "关闭": False}
_BOOL_TO_TRISTATE = {None: "跟随全局", True: "开启", False: "关闭"}

# `/敏感词 设置` 指令里，用户输入的中文项与内部配置 key 的映射
_SETTING_KEY_ALIASES = {
    "总开关": "enabled",
    "开关": "enabled",
    "本地": "local_enabled",
    "本地词库": "local_enabled",
    "接口": "api_enabled",
    "外部接口": "api_enabled",
    "ai": "llm_enabled",
    "AI": "llm_enabled",
    "llm": "llm_enabled",
    "图片": "image_enabled",
    "图片检测": "image_enabled",
    "撤回": "recall_enabled",
    "警告": "warn_enabled",
    "通知": "notify_enabled",
}

_ON_VALUES = {"on", "开", "开启", "true", "1", "启用"}
_OFF_VALUES = {"off", "关", "关闭", "false", "0", "禁用"}
_FOLLOW_VALUES = {"默认", "跟随", "auto", "follow"}


class SensitiveFilterPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 全局词库 Trie，词表变化时（指令增删 / 配置重载）需要调用 _rebuild_global_trie
        self.global_trie = WordTrie()
        # 每个群的“专属追加词”各自一棵小 Trie，按需懒加载并缓存
        self._group_tries: dict[str, WordTrie] = {}

        self._rebuild_global_trie()

        self._http_session: Optional[aiohttp.ClientSession] = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def terminate(self):
        """插件卸载/停用时调用，负责释放资源。"""
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()

    def _get_http_session(self) -> aiohttp.ClientSession:
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession()
        return self._http_session

    # ------------------------------------------------------------------
    # 词库 / 配置相关辅助方法
    # ------------------------------------------------------------------

    def _rebuild_global_trie(self) -> None:
        words = self._cfg("words", []) or []
        self.global_trie = WordTrie(
            case_insensitive=self._cfg("case_insensitive", True),
            fuzzy=self._cfg("fuzzy_match", True),
        )
        self.global_trie.build(words)

    def _get_group_trie(self, umo: str) -> Optional[WordTrie]:
        override = self._find_group_override(umo)
        extra_words = (override or {}).get("extra_words") or []
        if not extra_words:
            self._group_tries.pop(umo, None)
            return None
        trie = self._group_tries.get(umo)
        if trie is None:
            trie = WordTrie(
                case_insensitive=self._cfg("case_insensitive", True),
                fuzzy=self._cfg("fuzzy_match", True),
            )
            trie.build(extra_words)
            self._group_tries[umo] = trie
        return trie

    def _invalidate_group_trie(self, umo: str) -> None:
        self._group_tries.pop(umo, None)

    def _cfg(self, key: str, default: Any = None) -> Any:
        """读取全局配置项。根据 _KEY_TO_SECTION 自动定位到 _conf_schema.json
        里对应的嵌套分组（self.config[section][key]）。"""
        section = _KEY_TO_SECTION.get(key)
        if section is None:
            return self.config.get(key, default)
        return (self.config.get(section) or {}).get(key, default)

    def _set_cfg(self, key: str, value: Any) -> None:
        """写入全局配置项并落盘，路径规则与 _cfg 一致。"""
        section = _KEY_TO_SECTION.get(key)
        if section is None:
            self.config[key] = value
        else:
            self.config.setdefault(section, {})[key] = value
        self.config.save_config()

    # ------------------------------------------------------------------
    # 群覆盖配置（group_overrides，对应 WebUI 里的“分群配置”卡片列表）
    # 匹配键统一使用 umo（unified_msg_origin），不使用裁剪/派生出来的群号，
    # 避免跨平台时出现歧义。
    # ------------------------------------------------------------------

    def _get_group_overrides(self) -> list:
        return self.config.setdefault("group_overrides", [])

    def _find_group_override(self, umo: str) -> Optional[dict]:
        for item in self._get_group_overrides():
            if str(item.get("umo", "")) == str(umo):
                return item
        return None

    def _get_or_create_group_override(self, umo: str) -> dict:
        """获取某个会话的覆盖配置条目，不存在则创建一份带默认值的新条目。

        新建的条目必须带上 __template_key，否则 WebUI 打开 group_overrides
        列表时会提示“找不到对应模板”。
        """
        existing = self._find_group_override(umo)
        if existing is not None:
            return existing
        new_item = {
            "__template_key": _GROUP_OVERRIDE_TEMPLATE_KEY,
            "umo": str(umo),
            "enabled": "跟随全局",
            "local_enabled": "跟随全局",
            "api_enabled": "跟随全局",
            "llm_enabled": "跟随全局",
            "image_enabled": "跟随全局",
            "recall_enabled": "跟随全局",
            "warn_enabled": "跟随全局",
            "notify_enabled": "跟随全局",
            "extra_words": [],
        }
        self._get_group_overrides().append(new_item)
        return new_item

    def _prune_group_override_if_empty(self, umo: str) -> None:
        """如果一个会话的覆盖条目已经完全恢复成“全部跟随全局 + 无专属词”，
        就把这条记录从列表里删掉，避免 WebUI 里堆积大量空条目。"""
        override = self._find_group_override(umo)
        if override is None:
            return
        all_default = all(
            override.get(key, "跟随全局") == "跟随全局"
            for key in _OVERRIDABLE_BOOL_KEYS
        )
        no_extra_words = not override.get("extra_words")
        if all_default and no_extra_words:
            self._get_group_overrides().remove(override)

    def _get_effective(self, umo: str, key: str, default: Any = None) -> Any:
        """获取某个开关在某个会话的“有效值”：群覆盖优先，否则跟随全局配置。"""
        override = self._find_group_override(umo)
        if override is not None:
            tristate = override.get(key, "跟随全局")
            value = _TRISTATE_TO_BOOL.get(tristate)
            if value is not None:
                return value
        return self._cfg(key, default)

    # ------------------------------------------------------------------
    # 访问控制（白名单 / 黑名单）：决定“这个会话该不该被处理”，
    # 与 group_overrides（决定“该怎么处理”）完全独立，互不影响。
    # ------------------------------------------------------------------

    def _is_umo_allowed(self, umo: str) -> bool:
        """判断某个会话是否允许被插件处理。

        优先级：白名单命中 > 黑名单命中 > 白名单模式下未命中即拒绝 > 默认允许。
        """
        if self._cfg("whitelist_enabled", False):
            whitelist = self._cfg("whitelist_umos", []) or []
            if umo in whitelist:
                return True
            # 开启了白名单模式但没命中：不管黑名单状态如何，直接拒绝
            return False

        if self._cfg("blacklist_enabled", False):
            blacklist = self._cfg("blacklist_umos", []) or []
            if umo in blacklist:
                return False

        return True

    def _add_to_umo_list(self, list_key: str, umo: str) -> bool:
        """把 umo 加入白名单/黑名单列表，已存在则返回 False。"""
        items = list(self._cfg(list_key, []) or [])
        if umo in items:
            return False
        items.append(umo)
        self._set_cfg(list_key, items)
        return True

    def _remove_from_umo_list(self, list_key: str, umo: str) -> bool:
        """把 umo 从白名单/黑名单列表移除，不存在则返回 False。"""
        items = list(self._cfg(list_key, []) or [])
        if umo not in items:
            return False
        items.remove(umo)
        self._set_cfg(list_key, items)
        return True

    async def _gen_toggle_list_mode(
        self, event: AstrMessageEvent, enabled_key: str, label: str, on: bool
    ):
        self._set_cfg(enabled_key, on)
        if not on:
            yield event.plain_result(f"{label}已关闭")
            return
        if enabled_key == "whitelist_enabled":
            note = (
                "现在只有白名单里的会话会被检测，其余会话不再响应（优先级高于黑名单）"
            )
        else:
            note = "黑名单里的会话将被完全跳过（如果同时在白名单里，仍然会响应）"
        yield event.plain_result(f"{label}已开启：{note}")

    async def _gen_add_to_list(
        self, event: AstrMessageEvent, list_key: str, label: str
    ):
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("此指令仅能在群聊中使用")
            return
        umo = event.unified_msg_origin
        ok = self._add_to_umo_list(list_key, umo)
        if ok:
            yield event.plain_result(f"已将本群加入{label}")
        else:
            yield event.plain_result(f"本群已经在{label}中")

    async def _gen_remove_from_list(
        self, event: AstrMessageEvent, list_key: str, label: str
    ):
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("此指令仅能在群聊中使用")
            return
        umo = event.unified_msg_origin
        ok = self._remove_from_umo_list(list_key, umo)
        if ok:
            yield event.plain_result(f"已将本群移出{label}")
        else:
            yield event.plain_result(f"本群不在{label}中")

    async def _gen_list_umos(
        self, event: AstrMessageEvent, enabled_key: str, list_key: str, label: str
    ):
        enabled = self._cfg(enabled_key, False)
        items = self._cfg(list_key, []) or []
        status = "已开启" if enabled else "已关闭"
        if not items:
            yield event.plain_result(f"{label}当前{status}，列表为空")
            return
        preview = "\n".join(str(i) for i in items[:50])
        more = f"\n（共 {len(items)} 个，仅显示前 50 个）" if len(items) > 50 else ""
        yield event.plain_result(
            f"{label}当前{status}，共 {len(items)} 个：\n{preview}{more}"
        )

    # ------------------------------------------------------------------
    # 核心检测流程
    # ------------------------------------------------------------------

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=50)
    async def on_group_message(self, event: AstrMessageEvent):
        if not self._cfg("enabled", True):
            return

        group_id = event.get_group_id()
        if not group_id:
            return
        umo = event.unified_msg_origin

        if not self._is_umo_allowed(umo):
            return

        if not self._get_effective(umo, "enabled", True):
            return

        text = (event.message_str or "").strip()

        try:
            hit_word = source = None
            if text:
                hit_word, source = await self._check_text(event, umo, text)
            if not hit_word and self._get_effective(umo, "image_enabled", False):
                hit_word, source = await self._check_images(event, umo)
        except Exception:
            logger.exception("[敏感词过滤] 检测过程中发生异常，本次跳过")
            return

        if not hit_word:
            return

        sender_id = event.get_sender_id()
        logger.info(
            f"[敏感词过滤] 群 {group_id}（{umo}）用户 {sender_id} 触发敏感词「{hit_word}」"
            f"（来源：{source}）原文：{text}"
        )

        try:
            await self._handle_violation(event, umo, hit_word, source)
        except Exception:
            logger.exception("[敏感词过滤] 处理违规消息时发生异常")

    async def _check_text(
        self, event: AstrMessageEvent, umo: str, text: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """依次尝试本地词库 -> 外部接口 -> AI 语义检测，返回 (命中词/原因, 来源)。"""
        norm_text = normalize_text(text)

        if self._get_effective(umo, "local_enabled", True):
            hit = self.global_trie.find_first(norm_text)
            if hit:
                return hit, "本地词库"

            group_trie = self._get_group_trie(umo)
            if group_trie:
                hit = group_trie.find_first(norm_text)
                if hit:
                    return hit, "本群专属词库"

        if self._get_effective(umo, "api_enabled", False):
            api_url = (self._cfg("api_url") or "").strip()
            api_provider = self._cfg("api_provider", "uapis_profanitycheck")
            if api_url:
                try:
                    session = self._get_http_session()
                    if api_provider == "generic":
                        hit, reason = await check_via_api(
                            session,
                            text,
                            api_url=api_url,
                            method=self._cfg("api_method", "POST"),
                            headers_json=self._cfg("api_headers", "{}"),
                            text_field=self._cfg("api_text_field", "text"),
                            hit_path=self._cfg("api_hit_path", "hit"),
                            reason_path=self._cfg("api_reason_path", "reason"),
                            timeout=float(self._cfg("api_timeout", 5.0)),
                        )
                    else:
                        hit, reason = await check_via_uapis_profanitycheck(
                            session,
                            text,
                            api_url=api_url or UAPIS_PROFANITYCHECK_URL,
                            api_key=self._cfg("api_key", ""),
                            timeout=float(self._cfg("api_timeout", 5.0)),
                        )
                    if hit:
                        return (reason or "外部接口判定命中"), "外部接口"
                except Exception:
                    logger.exception(
                        "[敏感词过滤] 调用外部检测接口失败，已跳过此次接口检测"
                    )

        if self._get_effective(umo, "llm_enabled", False):
            try:
                provider_id = (self._cfg("llm_provider_id") or "").strip()
                if provider_id:
                    provider = self.context.get_provider_by_id(provider_id)
                else:
                    provider = self.context.get_using_provider(umo=umo)
                if provider:
                    prompt_template = self._cfg("llm_prompt") or DEFAULT_LLM_PROMPT
                    violate, reason = await check_via_llm(
                        provider, text, prompt_template=prompt_template
                    )
                    if violate:
                        return (reason or "AI 判定违规"), "AI 语义检测"
            except Exception:
                logger.exception("[敏感词过滤] AI 语义检测失败，已跳过此次检测")

        return None, None

    def _get_images_from_event(self, event: AstrMessageEvent) -> list:
        """从事件的消息链里取出所有图片组件（Comp.Image）。"""
        message = getattr(event.message_obj, "message", None) or []
        return [c for c in message if isinstance(c, Comp.Image)]

    async def _check_images(
        self, event: AstrMessageEvent, umo: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """对消息里的图片做检测：先看图片内容本身是否违规，再把图片里的文字
        转写出来复用现有的文字检测流水线。任意一张图片命中即返回，不再继续看
        后面的图片。"""
        images = self._get_images_from_event(event)
        if not images:
            return None, None

        provider_id = (self._cfg("image_provider_id") or "").strip()
        if not provider_id:
            # 没有显式配置支持视觉的 Provider 就不执行图片检测，
            # 不会盲目尝试用当前会话的文字模型去识图。
            return None, None
        provider = self.context.get_provider_by_id(provider_id)
        if not provider:
            logger.warning(
                f"[敏感词过滤] 配置的图片审核 Provider「{provider_id}」不存在，已跳过图片检测"
            )
            return None, None

        prompt_template = self._cfg("image_prompt") or DEFAULT_IMAGE_PROMPT

        for image in images:
            try:
                image_path = await image.convert_to_file_path()
            except Exception:
                logger.exception("[敏感词过滤] 图片转换为本地路径失败，跳过这张图片")
                continue

            try:
                image_violate, image_reason, extracted_text = await check_image(
                    provider, image_path, prompt_template=prompt_template
                )
            except Exception:
                logger.exception(
                    "[敏感词过滤] 调用图片审核 Provider 失败，跳过这张图片"
                )
                continue

            if image_violate:
                return (image_reason or "AI 判定图片违规"), "AI 图片审核"

            extracted_text = (extracted_text or "").strip()
            if extracted_text:
                hit_word, text_source = await self._check_text(
                    event, umo, extracted_text
                )
                if hit_word:
                    return hit_word, f"图片文字识别（{text_source}）"

        return None, None

    async def _handle_violation(
        self,
        event: AstrMessageEvent,
        umo: str,
        hit_word: str,
        source: str,
    ) -> None:
        sender_id = event.get_sender_id()
        sender_name = event.get_sender_name()
        group_id = event.get_group_id()

        if self._get_effective(umo, "recall_enabled", True):
            await self._try_recall(event)

        if self._get_effective(umo, "warn_enabled", True):
            tmpl = self._cfg("warn_message") or "检测到违规内容，已自动处理"
            warn_text = (
                tmpl.replace("{sender}", str(sender_name))
                .replace("{word}", str(hit_word))
                .replace("{source}", str(source))
            )
            chain = [Comp.At(qq=sender_id), Comp.Plain(" " + warn_text)]
            await event.send(event.chain_result(chain))

        if self._get_effective(umo, "notify_enabled", False):
            notify_targets = self._cfg("notify_umos", []) or []
            if notify_targets:
                notify_text = (
                    "⚠️ 群聊敏感词触发通知\n"
                    f"群: {group_id}\n"
                    f"umo: {umo}\n"
                    f"用户: {sender_name}({sender_id})\n"
                    f"来源: {source}\n"
                    f"命中: {hit_word}\n"
                    f"原文: {event.message_str}"
                )
                for target_umo in notify_targets:
                    try:
                        await self.context.send_message(
                            target_umo, MessageChain().message(notify_text)
                        )
                    except Exception:
                        logger.exception(f"[敏感词过滤] 发送通知到 {target_umo} 失败")

        if self._cfg("stop_event_on_hit", True):
            event.stop_event()

    async def _try_recall(self, event: AstrMessageEvent) -> bool:
        """尝试撤回触发违规的消息。不同平台支持情况不同，失败只记录日志不抛异常。"""
        platform_name = event.get_platform_name()
        try:
            if platform_name == "aiocqhttp":
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                    AiocqhttpMessageEvent,
                )

                assert isinstance(event, AiocqhttpMessageEvent)
                client = event.bot
                await client.api.call_action(
                    "delete_msg", message_id=event.message_obj.message_id
                )
                return True
            else:
                logger.info(
                    f"[敏感词过滤] 当前平台 {platform_name} 暂未适配自动撤回，"
                    "已跳过撤回操作（不影响警告/通知）"
                )
                return False
        except Exception:
            logger.exception(
                "[敏感词过滤] 撤回消息失败（可能是机器人无管理员权限或消息已超过可撤回时限）"
            )
            return False

    # ------------------------------------------------------------------
    # 管理指令
    # ------------------------------------------------------------------

    @filter.command_group("敏感词")
    def sw_group(self):
        """敏感词检测插件管理指令，发送 /敏感词 帮助 查看全部用法"""
        pass

    @filter.permission_type(filter.PermissionType.ADMIN)
    @sw_group.command("帮助")
    async def cmd_help(self, event: AstrMessageEvent):
        text = (
            "【敏感词插件指令】\n"
            "/敏感词 添加 <词>      全局词库新增一个词\n"
            "/敏感词 删除 <词>      全局词库删除一个词\n"
            "/敏感词 列表           查看全局词库\n"
            "/敏感词 本群添加 <词>  仅本群额外生效的词\n"
            "/敏感词 本群删除 <词>  删除本群专属词\n"
            "/敏感词 设置 <项> <on/off/默认>  本群单独覆盖某个开关\n"
            "    可设置项：总开关/本地/接口/ai/图片/撤回/警告/通知\n"
            "/敏感词 状态           查看本群当前生效的配置\n"
            "/敏感词 白名单 开启|关闭|添加本群|删除本群|列表\n"
            "/敏感词 黑名单 开启|关闭|添加本群|删除本群|列表\n"
            "    白名单/黑名单决定“这个群该不该被处理”，与上面的设置完全独立；\n"
            "    白名单优先级高于黑名单，详见 WebUI 配置页“访问控制”分组说明\n"
            "提示：以上设置也可以在 WebUI 插件配置页里以可视化形式查看和编辑，"
            "两边操作的是同一份数据。\n"
        )
        yield event.plain_result(text)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @sw_group.command("添加")
    async def cmd_add_word(self, event: AstrMessageEvent, word: str):
        word = (word or "").strip()
        if not word:
            yield event.plain_result("请提供要添加的词，例如：/敏感词 添加 测试词")
            return
        words = list(self._cfg("words", []) or [])
        if word in words:
            yield event.plain_result(f"「{word}」已经在全局词库中")
            return
        words.append(word)
        self._set_cfg("words", words)
        self._rebuild_global_trie()
        yield event.plain_result(f"已添加全局敏感词「{word}」，当前共 {len(words)} 个")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @sw_group.command("删除")
    async def cmd_del_word(self, event: AstrMessageEvent, word: str):
        word = (word or "").strip()
        words = list(self._cfg("words", []) or [])
        if word not in words:
            yield event.plain_result(f"全局词库中没有找到「{word}」")
            return
        words.remove(word)
        self._set_cfg("words", words)
        self._rebuild_global_trie()
        yield event.plain_result(f"已删除全局敏感词「{word}」，当前共 {len(words)} 个")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @sw_group.command("列表")
    async def cmd_list_words(self, event: AstrMessageEvent):
        words = list(self._cfg("words", []) or [])
        if not words:
            yield event.plain_result("全局词库当前为空")
            return
        preview = "、".join(words[:50])
        more = f"\n（共 {len(words)} 个，仅显示前 50 个）" if len(words) > 50 else ""
        yield event.plain_result(f"全局词库（{len(words)} 个）：\n{preview}{more}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @sw_group.command("本群添加")
    async def cmd_add_group_word(self, event: AstrMessageEvent, word: str):
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("此指令仅能在群聊中使用")
            return
        umo = event.unified_msg_origin
        word = (word or "").strip()
        if not word:
            yield event.plain_result("请提供要添加的词，例如：/敏感词 本群添加 测试词")
            return

        override = self._get_or_create_group_override(umo)
        extra_words = override.setdefault("extra_words", [])
        if word in extra_words:
            yield event.plain_result(f"「{word}」已经在本群专属词库中")
            return
        extra_words.append(word)
        self.config.save_config()
        self._invalidate_group_trie(umo)
        yield event.plain_result(f"已为本群添加专属敏感词「{word}」")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @sw_group.command("本群删除")
    async def cmd_del_group_word(self, event: AstrMessageEvent, word: str):
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("此指令仅能在群聊中使用")
            return
        umo = event.unified_msg_origin
        word = (word or "").strip()

        override = self._find_group_override(umo)
        extra_words = (override or {}).get("extra_words") or []
        if word not in extra_words:
            yield event.plain_result(f"本群专属词库中没有找到「{word}」")
            return
        extra_words.remove(word)
        self._prune_group_override_if_empty(umo)
        self.config.save_config()
        self._invalidate_group_trie(umo)
        yield event.plain_result(f"已删除本群专属敏感词「{word}」")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @sw_group.command("设置")
    async def cmd_set_group_option(
        self, event: AstrMessageEvent, item: str, value: str
    ):
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("此指令仅能在群聊中使用")
            return
        umo = event.unified_msg_origin

        key = _SETTING_KEY_ALIASES.get(item.strip())
        if key is None or key not in _OVERRIDABLE_BOOL_KEYS:
            options = "/".join(sorted(set(_SETTING_KEY_ALIASES.keys())))
            yield event.plain_result(f"未知的设置项「{item}」，可选：{options}")
            return

        value_norm = value.strip().lower()
        if value_norm in _ON_VALUES:
            new_value: Optional[bool] = True
        elif value_norm in _OFF_VALUES:
            new_value = False
        elif value_norm in _FOLLOW_VALUES:
            new_value = None  # 取消覆盖，跟随全局
        else:
            yield event.plain_result("第二个参数请填 on / off / 默认 之一")
            return

        override = self._get_or_create_group_override(umo)
        override[key] = _BOOL_TO_TRISTATE[new_value]
        if new_value is None:
            self._prune_group_override_if_empty(umo)
        self.config.save_config()
        desc = _BOOL_TO_TRISTATE[new_value]
        yield event.plain_result(f"本群「{item}」已设置为：{desc}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @sw_group.command("状态")
    async def cmd_status(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result(
                f"当前 unified_msg_origin：{event.unified_msg_origin}\n"
                "（私聊中无群级配置，以下开关均为全局默认值）"
            )
            return
        umo = event.unified_msg_origin

        def fmt(key: str, label: str) -> str:
            override = self._find_group_override(umo)
            tristate = (override or {}).get(key, "跟随全局")
            overridden = tristate != "跟随全局"
            effective = self._get_effective(umo, key, True)
            tag = "（本群覆盖）" if overridden else "（全局默认）"
            return f"{label}: {'开启' if effective else '关闭'} {tag}"

        override = self._find_group_override(umo)
        extra_words = (override or {}).get("extra_words") or []
        allowed = self._is_umo_allowed(umo)
        access_note = "允许处理" if allowed else "被访问控制拦截，不会响应"
        lines = [
            f"群 {group_id} 当前生效配置：",
            f"访问控制: {access_note}",
            fmt("enabled", "插件总开关"),
            fmt("local_enabled", "本地词库检测"),
            fmt("api_enabled", "外部接口检测"),
            fmt("llm_enabled", "AI 语义检测"),
            fmt("image_enabled", "图片检测"),
            fmt("recall_enabled", "命中后撤回"),
            fmt("warn_enabled", "命中后警告"),
            fmt("notify_enabled", "命中后通知"),
            f"本群专属词库：{len(extra_words)} 个",
            f"umo（在 WebUI「分群配置」里新增条目时请填这个）：{umo}",
        ]
        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------
    # 白名单 / 黑名单子指令组
    # ------------------------------------------------------------------

    @sw_group.group("白名单")
    def whitelist_group(self):
        """白名单管理子指令，见 /敏感词 帮助"""
        pass

    @filter.permission_type(filter.PermissionType.ADMIN)
    @whitelist_group.command("开启")
    async def cmd_whitelist_on(self, event: AstrMessageEvent):
        async for r in self._gen_toggle_list_mode(
            event, "whitelist_enabled", "白名单", True
        ):
            yield r

    @filter.permission_type(filter.PermissionType.ADMIN)
    @whitelist_group.command("关闭")
    async def cmd_whitelist_off(self, event: AstrMessageEvent):
        async for r in self._gen_toggle_list_mode(
            event, "whitelist_enabled", "白名单", False
        ):
            yield r

    @filter.permission_type(filter.PermissionType.ADMIN)
    @whitelist_group.command("添加本群")
    async def cmd_whitelist_add(self, event: AstrMessageEvent):
        async for r in self._gen_add_to_list(event, "whitelist_umos", "白名单"):
            yield r

    @filter.permission_type(filter.PermissionType.ADMIN)
    @whitelist_group.command("删除本群")
    async def cmd_whitelist_remove(self, event: AstrMessageEvent):
        async for r in self._gen_remove_from_list(event, "whitelist_umos", "白名单"):
            yield r

    @filter.permission_type(filter.PermissionType.ADMIN)
    @whitelist_group.command("列表")
    async def cmd_whitelist_list(self, event: AstrMessageEvent):
        async for r in self._gen_list_umos(
            event, "whitelist_enabled", "whitelist_umos", "白名单"
        ):
            yield r

    @sw_group.group("黑名单")
    def blacklist_group(self):
        """黑名单管理子指令，见 /敏感词 帮助"""
        pass

    @filter.permission_type(filter.PermissionType.ADMIN)
    @blacklist_group.command("开启")
    async def cmd_blacklist_on(self, event: AstrMessageEvent):
        async for r in self._gen_toggle_list_mode(
            event, "blacklist_enabled", "黑名单", True
        ):
            yield r

    @filter.permission_type(filter.PermissionType.ADMIN)
    @blacklist_group.command("关闭")
    async def cmd_blacklist_off(self, event: AstrMessageEvent):
        async for r in self._gen_toggle_list_mode(
            event, "blacklist_enabled", "黑名单", False
        ):
            yield r

    @filter.permission_type(filter.PermissionType.ADMIN)
    @blacklist_group.command("添加本群")
    async def cmd_blacklist_add(self, event: AstrMessageEvent):
        async for r in self._gen_add_to_list(event, "blacklist_umos", "黑名单"):
            yield r

    @filter.permission_type(filter.PermissionType.ADMIN)
    @blacklist_group.command("删除本群")
    async def cmd_blacklist_remove(self, event: AstrMessageEvent):
        async for r in self._gen_remove_from_list(event, "blacklist_umos", "黑名单"):
            yield r

    @filter.permission_type(filter.PermissionType.ADMIN)
    @blacklist_group.command("列表")
    async def cmd_blacklist_list(self, event: AstrMessageEvent):
        async for r in self._gen_list_umos(
            event, "blacklist_enabled", "blacklist_umos", "黑名单"
        ):
            yield r
