from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from astrbot.api import logger

try:
    from quart import jsonify
    from quart import request as quart_request
except ImportError:  # 测试环境或旧 AstrBot 可能没有 Quart
    jsonify = None
    quart_request = None

PLUGIN_NAME = "astrbot_plugin_sensitivefilter"

_OVERRIDABLE_BOOL_KEYS = (
    "enabled",
    "local_enabled",
    "api_enabled",
    "llm_enabled",
    "image_enabled",
    "recall_enabled",
    "warn_enabled",
    "notify_enabled",
    "mute_enabled",
)
_KEY_TO_SECTION = {
    "whitelist_enabled": "access_control",
    "whitelist_umos": "access_control",
    "blacklist_enabled": "access_control",
    "blacklist_umos": "access_control",
    "enabled": "basic",
    "local_enabled": "basic",
    "words": "basic",
    "user_whitelist_enabled": "user_access_control",
    "user_whitelist_ids": "user_access_control",
    "case_insensitive": "basic",
    "fuzzy_match": "basic",
    "stop_event_on_hit": "basic",
    "qq_forward_debug": "basic",
    "recall_enabled": "actions",
    "warn_enabled": "actions",
    "warn_message": "actions",
    "notify_enabled": "actions",
    "notify_message": "actions",
    "notify_umos": "actions",
    "mute_enabled": "actions",
    "mute_first_duration_seconds": "actions",
    "mute_second_duration_seconds": "actions",
    "mute_third_duration_seconds": "actions",
    "mute_reset_hour": "actions",
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
    "llm_batch_enabled": "llm_detection",
    "llm_batch_size": "llm_detection",
    "llm_batch_max_wait_minutes": "llm_detection",
    "llm_batch_prompt": "llm_detection",
    "llm_prompt": "llm_detection",
    "image_enabled": "image_detection",
    "image_provider_id": "image_detection",
    "image_prompt": "image_detection",
}
_GROUP_OVERRIDE_TEMPLATE_KEY = "group_override"
_TRISTATE_TO_BOOL = {"跟随全局": None, "开启": True, "关闭": False}
_BOOL_TO_TRISTATE = {None: "跟随全局", True: "开启", False: "关闭"}


class WebMixin:
    """AstrBot 额外插件页面（pages/dashboard）使用的 Web API。

    register_web_api 注册的接口由 AstrBot Dashboard 的插件页面 Bridge 调用；
    这些接口不同于 _conf_schema.json 生成的“设置页 UI”。
    """

    def _json(self, data: Any):
        if jsonify is None:
            return data
        return jsonify(data)

    def _wrap_web_handler(self, handler):
        async def _wrapped(*args, **kwargs):
            try:
                return await handler(*args, **kwargs)
            except Exception as e:  # noqa: BLE001
                logger.exception(f"[敏感词过滤] WebUI API {handler.__name__} 处理失败")
                return self._json(
                    {"status": "error", "message": str(e) or e.__class__.__name__}
                )

        _wrapped.__name__ = handler.__name__
        return _wrapped

    def _register_web_apis(self) -> None:
        if not hasattr(self.context, "register_web_api"):
            logger.info(
                "[敏感词过滤] 当前 AstrBot Context 不支持额外插件页面 API，已跳过注册"
            )
            return
        routes = [
            ("/stats", self._web_stats, ["GET"], "获取敏感词插件统计"),
            ("/records", self._web_records, ["GET"], "获取全部违规记录"),
            ("/records/clear", self._web_records_clear, ["POST"], "清空最近违规记录"),
            ("/records/export", self._web_records_export, ["GET"], "导出违规记录 CSV"),
            ("/today_stats", self._web_today_stats, ["GET"], "获取今日违规排行"),
            (
                "/dashboard/trend",
                self._web_dashboard_trend,
                ["GET"],
                "获取违规趋势数据",
            ),
            (
                "/dashboard/distribution",
                self._web_dashboard_distribution,
                ["GET"],
                "获取违规类型分布",
            ),
            ("/dashboard/hourly", self._web_dashboard_hourly, ["GET"], "获取时段分布"),
            (
                "/dashboard/group_ranking",
                self._web_dashboard_group_ranking,
                ["GET"],
                "获取群违规历史排行",
            ),
            (
                "/moderation_users",
                self._web_moderation_users,
                ["GET"],
                "获取被撤回用户聚合列表",
            ),
            ("/config", self._web_get_config, ["GET"], "获取敏感词插件配置"),
            ("/config", self._web_update_config, ["POST"], "更新敏感词插件配置"),
            ("/providers", self._web_get_providers, ["GET"], "获取 LLM Provider 列表"),
            ("/group_overrides", self._web_group_overrides, ["GET"], "获取分群配置"),
            (
                "/group_overrides/save",
                self._web_save_group_override,
                ["POST"],
                "保存分群配置",
            ),
            (
                "/group_overrides/delete",
                self._web_delete_group_override,
                ["POST"],
                "删除分群配置",
            ),
            ("/words/add", self._web_add_word, ["POST"], "添加全局敏感词"),
            ("/words/delete", self._web_delete_word, ["POST"], "删除全局敏感词"),
            (
                "/user_whitelist/add",
                self._web_add_user_whitelist,
                ["POST"],
                "添加用户白名单",
            ),
            (
                "/user_whitelist/delete",
                self._web_delete_user_whitelist,
                ["POST"],
                "删除用户白名单",
            ),
        ]
        for path, handler, methods, desc in routes:
            self.context.register_web_api(
                f"/{PLUGIN_NAME}{path}",
                self._wrap_web_handler(handler),
                methods,
                desc,
            )
        logger.info("[敏感词过滤] 额外插件页面 WebUI API 已注册")

    def _save_web_config(self) -> None:
        save = getattr(self.config, "save_config", None)
        if callable(save):
            save()

    @staticmethod
    def _as_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            v = value.strip().lower()
            if v in {"1", "true", "yes", "on", "开", "开启", "启用"}:
                return True
            if v in {"0", "false", "no", "off", "关", "关闭", "禁用", ""}:
                return False
        return default

    @staticmethod
    def _as_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        if isinstance(value, str):
            return [x.strip() for x in value.replace("，", ",").split(",") if x.strip()]
        return []

    @staticmethod
    def _csv_safe(value: Any) -> str:
        text = "" if value is None else str(value)
        if text and text[0] in {"=", "+", "-", "@", "\t", "\r", "\n"}:
            return "'" + text
        return text

    def _schema_snapshot(self) -> dict[str, Any]:
        return {
            "sections": {
                section: {
                    "description": meta.get("description", section),
                    "hint": meta.get("hint", ""),
                    "items": meta.get("items", {}),
                }
                for section, meta in self._raw_config_schema().items()
                if meta.get("type") == "object"
            },
            "tristate_options": list(_TRISTATE_TO_BOOL.keys()),
            "overridable_bool_keys": list(_OVERRIDABLE_BOOL_KEYS),
        }

    def _raw_config_schema(self) -> dict[str, Any]:
        # _conf_schema.json 是分组 schema；懒加载供额外插件页面渲染表单。
        cached = getattr(self, "_web_config_schema", None)
        if cached is not None:
            return cached
        try:
            schema_path = Path(__file__).with_name("_conf_schema.json")
            cached = json.loads(schema_path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[敏感词过滤] 读取 WebUI schema 失败: {e}")
            cached = {}
        self._web_config_schema = cached
        return cached

    async def _web_stats(self):
        words = self._cfg("words", []) or []
        group_overrides = self._get_group_overrides()
        extra_words_count = sum(
            len(item.get("extra_words") or []) for item in group_overrides
        )
        enabled_overrides = sum(
            1
            for item in group_overrides
            if item.get("enabled", "跟随全局") != "跟随全局"
        )
        pending_batches = sum(
            len(bucket) for bucket in getattr(self, "_llm_batches", {}).values()
        )
        violation_users = len(getattr(self, "_violation_counts", {}))
        records = list(getattr(self, "_violation_records", []))
        today_prefix = ""
        try:
            import time

            today_prefix = time.strftime("%Y-%m-%d", time.localtime())
        except Exception:  # noqa: BLE001
            today_prefix = ""
        today_records = [
            r for r in records if str(r.get("time", "")).startswith(today_prefix)
        ]
        source_counts: dict[str, int] = {}
        group_counts: dict[str, int] = {}
        user_counts: dict[str, int] = {}
        for record in records:
            source = str(record.get("source", "未知来源") or "未知来源")
            group_id = str(record.get("group_id", "未知群") or "未知群")
            user = f"{record.get('user_name', '')}({record.get('user_id', '')})"
            source_counts[source] = source_counts.get(source, 0) + 1
            group_counts[group_id] = group_counts.get(group_id, 0) + 1
            user_counts[user] = user_counts.get(user, 0) + 1
        data = {
            "plugin_name": PLUGIN_NAME,
            "global_words_count": len(words),
            "group_overrides_count": len(group_overrides),
            "extra_words_count": extra_words_count,
            "enabled_overrides_count": enabled_overrides,
            "user_whitelist_count": len(self._cfg("user_whitelist_ids", []) or []),
            "notify_targets_count": len(self._cfg("notify_umos", []) or []),
            "pending_llm_batches": pending_batches,
            "violation_users_count": violation_users,
            "records_count": len(records),
            "today_records_count": len(today_records),
            "today_blocked": len(today_records),
            "today_total": len(today_records),
            "group_white_list_count": len(self._cfg("whitelist_umos", []) or []),
            "group_black_list_count": len(self._cfg("blacklist_umos", []) or []),
            "user_black_list_count": 0,
            "admin_list_count": 0,
            "total_logs": len(records),
            "recalled_records_count": sum(
                1 for r in records if r.get("recall_executed")
            ),
            "muted_records_count": sum(
                1 for r in records if int(r.get("mute_duration") or 0) > 0
            ),
            "source_ranking": sorted(
                source_counts.items(), key=lambda x: x[1], reverse=True
            )[:8],
            "group_ranking": sorted(
                group_counts.items(), key=lambda x: x[1], reverse=True
            )[:8],
            "user_ranking": sorted(
                user_counts.items(), key=lambda x: x[1], reverse=True
            )[:8],
            "local_enabled": bool(self._cfg("local_enabled", True)),
            "api_enabled": bool(self._cfg("api_enabled", False)),
            "llm_enabled": bool(self._cfg("llm_enabled", False)),
            "image_enabled": bool(self._cfg("image_enabled", False)),
            "recall_enabled": bool(self._cfg("recall_enabled", True)),
            "warn_enabled": bool(self._cfg("warn_enabled", True)),
            "notify_enabled": bool(self._cfg("notify_enabled", False)),
            "mute_enabled": bool(self._cfg("mute_enabled", False)),
        }
        return self._json({"status": "success", "data": data})

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _record_day(record: dict[str, Any]) -> str:
        value = str(record.get("time", ""))
        if len(value) >= 10:
            return value[:10]
        try:
            import time

            ts = int(record.get("ts") or 0)
            if ts > 0:
                return time.strftime("%Y-%m-%d", time.localtime(ts))
        except Exception:  # noqa: BLE001
            return ""
        return ""

    @staticmethod
    def _record_hour(record: dict[str, Any]) -> int:
        value = str(record.get("time", ""))
        if len(value) >= 13:
            try:
                return int(value[11:13])
            except ValueError:
                pass
        try:
            import time

            ts = int(record.get("ts") or 0)
            if ts > 0:
                return int(time.strftime("%H", time.localtime(ts)))
        except Exception:  # noqa: BLE001
            return 0
        return 0

    def _records_since_days(self, days: int) -> list[dict[str, Any]]:
        try:
            import time

            cutoff = int(time.time()) - max(int(days), 1) * 86400
        except Exception:  # noqa: BLE001
            cutoff = 0
        return [
            record
            for record in list(getattr(self, "_violation_records", []))
            if int(record.get("ts") or 0) >= cutoff
        ]

    async def _web_today_stats(self):
        try:
            import time

            today = time.strftime("%Y-%m-%d", time.localtime())
        except Exception:  # noqa: BLE001
            today = ""
        records = [
            record
            for record in list(getattr(self, "_violation_records", []))
            if self._record_day(record) == today
        ]
        group_counts: dict[str, int] = {}
        user_map: dict[str, dict[str, Any]] = {}
        for record in records:
            group_id = str(record.get("group_id", "") or "未知群")
            group_counts[group_id] = group_counts.get(group_id, 0) + 1
            user_id = str(record.get("user_id", "") or "未知用户")
            item = user_map.setdefault(
                user_id,
                {
                    "user_id": user_id,
                    "user_name": record.get("user_name", ""),
                    "count": 0,
                },
            )
            item["count"] += 1
        group_ranking = [
            {"group_id": group_id, "count": count}
            for group_id, count in sorted(
                group_counts.items(), key=lambda item: item[1], reverse=True
            )[:10]
        ]
        user_ranking = sorted(
            user_map.values(), key=lambda item: item["count"], reverse=True
        )[:10]
        return self._json(
            {
                "status": "success",
                "data": {"group_ranking": group_ranking, "user_ranking": user_ranking},
            }
        )

    async def _web_dashboard_trend(self):
        days = 7
        if quart_request is not None:
            days = min(90, max(1, self._safe_int(quart_request.args.get("days", 7), 7)))
        records = self._records_since_days(days)
        try:
            import time

            today_ts = int(time.time())
            labels = [
                time.strftime(
                    "%Y-%m-%d", time.localtime(today_ts - (days - 1 - i) * 86400)
                )
                for i in range(days)
            ]
        except Exception:  # noqa: BLE001
            labels = []
        counts = {label: 0 for label in labels}
        for record in records:
            day = self._record_day(record)
            if day in counts:
                counts[day] += 1
        return self._json(
            {
                "status": "success",
                "data": [
                    {"date": label, "blocked": counts.get(label, 0), "passed": 0}
                    for label in labels
                ],
            }
        )

    async def _web_dashboard_distribution(self):
        days = 7
        if quart_request is not None:
            days = min(90, max(1, self._safe_int(quart_request.args.get("days", 7), 7)))
        counts: dict[str, int] = {}
        for record in self._records_since_days(days):
            reason = str(
                record.get("source") or record.get("forbidden_words") or "未知来源"
            )
            counts[reason] = counts.get(reason, 0) + 1
        data = [
            {"reason": reason, "count": count}
            for reason, count in sorted(
                counts.items(), key=lambda item: item[1], reverse=True
            )
        ]
        return self._json({"status": "success", "data": data})

    async def _web_dashboard_hourly(self):
        days = 7
        if quart_request is not None:
            days = min(90, max(1, self._safe_int(quart_request.args.get("days", 7), 7)))
        counts: dict[int, int] = {}
        for record in self._records_since_days(days):
            hour = self._record_hour(record)
            counts[hour] = counts.get(hour, 0) + 1
        data = [
            {"hour": hour, "count": count}
            for hour, count in sorted(counts.items())
            if count > 0
        ]
        return self._json({"status": "success", "data": data})

    async def _web_dashboard_group_ranking(self):
        days = 7
        top = 10
        if quart_request is not None:
            days = min(90, max(1, self._safe_int(quart_request.args.get("days", 7), 7)))
            top = min(50, max(1, self._safe_int(quart_request.args.get("top", 10), 10)))
        counts: dict[str, int] = {}
        for record in self._records_since_days(days):
            group_id = str(record.get("group_id", "") or "未知群")
            counts[group_id] = counts.get(group_id, 0) + 1
        data = [
            {"group_id": group_id, "count": count}
            for group_id, count in sorted(
                counts.items(), key=lambda item: item[1], reverse=True
            )[:top]
        ]
        return self._json({"status": "success", "data": data})

    async def _web_records(self):
        records = list(getattr(self, "_violation_records", []))
        records.sort(key=lambda item: int(item.get("id") or 0), reverse=True)
        return self._json({"status": "success", "data": records, "total": len(records)})

    async def _web_records_clear(self):
        clear_records = getattr(self, "_clear_violation_records", None)
        if callable(clear_records):
            clear_records()
        else:
            self._violation_records = []
            self._next_violation_record_id = 1
        return self._json({"status": "success"})

    async def _web_moderation_users(self):
        records = list(getattr(self, "_violation_records", []))
        only_recalled = False
        if quart_request is not None:
            only_recalled = self._as_bool(
                quart_request.args.get("recalled", False), False
            )
        users: dict[str, dict[str, Any]] = {}
        for record in records:
            if only_recalled and not record.get("recall_executed"):
                continue
            user_id = str(record.get("user_id", "")).strip()
            if not user_id:
                continue
            if user_id not in users:
                users[user_id] = {
                    "user_id": user_id,
                    "user_name": record.get("user_name", ""),
                    "count": 0,
                    "recalled_count": 0,
                    "groups": set(),
                    "records": [],
                    "last_ts": 0,
                }
            item = users[user_id]
            item["count"] += 1
            if record.get("recall_executed"):
                item["recalled_count"] += 1
            group_id = str(record.get("group_id", ""))
            if group_id:
                item["groups"].add(group_id)
            item["last_ts"] = max(
                int(item.get("last_ts") or 0), int(record.get("ts") or 0)
            )
            item["records"].append(record)
        output = []
        for item in users.values():
            item["groups"] = sorted(item["groups"])
            item["group_count"] = len(item["groups"])
            item["records"].sort(key=lambda r: int(r.get("id") or 0), reverse=True)
            output.append(item)
        output.sort(
            key=lambda u: (
                u.get("recalled_count", 0),
                u.get("count", 0),
                u.get("last_ts", 0),
            ),
            reverse=True,
        )
        return self._json({"status": "success", "data": output, "total": len(output)})

    async def _web_records_export(self):
        records = list(getattr(self, "_violation_records", []))
        records.sort(key=lambda item: int(item.get("id") or 0))
        output = io.StringIO()
        output.write("\ufeff")
        writer = csv.writer(output)
        writer.writerow(
            [
                "ID",
                "时间",
                "群号",
                "UMO",
                "用户ID",
                "用户名",
                "敏感词/原因",
                "来源",
                "原文",
                "脱敏文本",
                "违规次数",
                "是否撤回",
                "禁言秒数",
                "媒体路径",
            ]
        )
        for record in records:
            writer.writerow(
                [
                    self._csv_safe(record.get("id", "")),
                    self._csv_safe(record.get("time", "")),
                    self._csv_safe(record.get("group_id", "")),
                    self._csv_safe(record.get("umo", "")),
                    self._csv_safe(record.get("user_id", "")),
                    self._csv_safe(record.get("user_name", "")),
                    self._csv_safe(record.get("forbidden_words", "")),
                    self._csv_safe(record.get("source", "")),
                    self._csv_safe(record.get("original_text", "")),
                    self._csv_safe(record.get("masked_text", "")),
                    self._csv_safe(record.get("violation_count", "")),
                    self._csv_safe("是" if record.get("recall_executed") else "否"),
                    self._csv_safe(record.get("mute_duration", "")),
                    self._csv_safe(record.get("media_path", "")),
                ]
            )
        return (
            output.getvalue(),
            200,
            {
                "Content-Type": "text/csv; charset=utf-8",
                "Content-Disposition": "attachment; filename=sensitivefilter_records.csv",
            },
        )

    async def _web_get_config(self):
        data = {"_schema": self._schema_snapshot()}
        for section in {v for v in _KEY_TO_SECTION.values()}:
            data[section] = dict(self.config.get(section) or {})
        data["group_overrides"] = self._get_group_overrides()
        return self._json({"status": "success", "data": data})

    async def _web_update_config(self):
        if quart_request is None:
            return self._json({"status": "error", "message": "Quart request 不可用"})
        payload = await quart_request.get_json(force=True, silent=True) or {}
        updated: list[str] = []
        for key, value in payload.items():
            if key not in _KEY_TO_SECTION:
                continue
            section = _KEY_TO_SECTION[key]
            item_schema = (
                self._raw_config_schema().get(section, {}).get("items", {}) or {}
            ).get(key, {})
            typ = item_schema.get("type")
            default = item_schema.get("default")
            if typ == "bool":
                value = self._as_bool(value, bool(default))
            elif typ == "int":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    value = int(default or 0)
            elif typ == "float":
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    value = float(default or 0)
            elif typ == "list":
                value = self._as_list(value)
            elif typ in {"string", "text"}:
                value = str(value)
            self.config.setdefault(section, {})[key] = value
            updated.append(key)
        if any(k in updated for k in ("words", "case_insensitive", "fuzzy_match")):
            self._rebuild_global_trie()
            self._group_tries.clear()
        self._save_web_config()
        return self._json({"status": "success", "updated": updated})

    async def _web_get_providers(self):
        providers = []
        try:
            all_providers = []
            if hasattr(self.context, "get_all_providers"):
                all_providers = self.context.get_all_providers() or []
            for provider in all_providers:
                meta = provider.meta() if hasattr(provider, "meta") else None
                pid = getattr(meta, "id", "") if meta else ""
                model = getattr(meta, "model", "") if meta else ""
                if pid:
                    providers.append({"id": pid, "name": model or pid, "model": model})
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[敏感词过滤] 获取 Provider 列表失败: {e}")
        return self._json({"status": "success", "data": providers})

    async def _web_group_overrides(self):
        return self._json({"status": "success", "data": self._get_group_overrides()})

    def _normalize_group_override_payload(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        umo = str(payload.get("umo", "")).strip()
        item = {
            "__template_key": _GROUP_OVERRIDE_TEMPLATE_KEY,
            "umo": umo,
            "extra_words": self._as_list(payload.get("extra_words", [])),
        }
        for key in _OVERRIDABLE_BOOL_KEYS:
            value = payload.get(key, "跟随全局")
            if isinstance(value, bool):
                value = _BOOL_TO_TRISTATE[value]
            value = str(value).strip() or "跟随全局"
            if value not in _TRISTATE_TO_BOOL:
                value = "跟随全局"
            item[key] = value
        return item

    async def _web_save_group_override(self):
        if quart_request is None:
            return self._json({"status": "error", "message": "Quart request 不可用"})
        payload = await quart_request.get_json(force=True, silent=True) or {}
        item = self._normalize_group_override_payload(payload)
        if not item["umo"]:
            return self._json({"status": "error", "message": "缺少 umo"})
        existing = self._find_group_override(item["umo"])
        if existing is None:
            self._get_group_overrides().append(item)
        else:
            existing.clear()
            existing.update(item)
        self._invalidate_group_trie(item["umo"])
        self._prune_group_override_if_empty(item["umo"])
        self._save_web_config()
        return self._json(
            {"status": "success", "data": self._find_group_override(item["umo"])}
        )

    async def _web_delete_group_override(self):
        if quart_request is None:
            return self._json({"status": "error", "message": "Quart request 不可用"})
        payload = await quart_request.get_json(force=True, silent=True) or {}
        umo = str(payload.get("umo", "")).strip()
        existing = self._find_group_override(umo)
        if existing is not None:
            self._get_group_overrides().remove(existing)
            self._invalidate_group_trie(umo)
            self._save_web_config()
        return self._json({"status": "success"})

    async def _web_add_word(self):
        if quart_request is None:
            return self._json({"status": "error", "message": "Quart request 不可用"})
        payload = await quart_request.get_json(force=True, silent=True) or {}
        word = str(payload.get("word", "")).strip()
        if not word:
            return self._json({"status": "error", "message": "缺少敏感词"})
        words = self._as_list(self._cfg("words", []))
        if word not in words:
            words.append(word)
            self._set_cfg("words", words)
            self._rebuild_global_trie()
        return self._json({"status": "success", "data": words})

    async def _web_delete_word(self):
        if quart_request is None:
            return self._json({"status": "error", "message": "Quart request 不可用"})
        payload = await quart_request.get_json(force=True, silent=True) or {}
        word = str(payload.get("word", "")).strip()
        words = [w for w in self._as_list(self._cfg("words", [])) if w != word]
        self._set_cfg("words", words)
        self._rebuild_global_trie()
        return self._json({"status": "success", "data": words})

    async def _web_add_user_whitelist(self):
        if quart_request is None:
            return self._json({"status": "error", "message": "Quart request 不可用"})
        payload = await quart_request.get_json(force=True, silent=True) or {}
        user_id = str(payload.get("user_id", "")).strip()
        if not user_id:
            return self._json({"status": "error", "message": "缺少用户 ID"})
        self._add_to_user_whitelist(user_id)
        return self._json(
            {"status": "success", "data": self._cfg("user_whitelist_ids", []) or []}
        )

    async def _web_delete_user_whitelist(self):
        if quart_request is None:
            return self._json({"status": "error", "message": "Quart request 不可用"})
        payload = await quart_request.get_json(force=True, silent=True) or {}
        user_id = str(payload.get("user_id", "")).strip()
        self._remove_from_user_whitelist(user_id)
        return self._json(
            {"status": "success", "data": self._cfg("user_whitelist_ids", []) or []}
        )
