# 测试说明

本目录下的脚本是开发本插件时用来验证逻辑正确性的功能测试，**不是插件运行时需要的文件**，单纯放在这里方便以后改代码时回归测试，删掉也完全不影响插件正常使用。

## 怎么跑

```bash
# 一次跑全部
python3 tests/run_all.py

# 或者单独跑某一个
python3 tests/test_word_matcher.py
```

不依赖 pytest，每个 `test_*.py` 都是可以直接用 `python3` 执行的独立脚本：跑完会打印每一项的 `[OK]`/`[FAIL]`，最后汇总通过/失败数量，进程退出码非 0 表示有失败项。

## 各文件覆盖的内容

| 文件 | 覆盖范围 |
| --- | --- |
| `test_word_matcher.py` | 本地词库 Trie 匹配：大小写、模糊匹配防拆字、零宽字符/全角字符归一化 |
| `test_api_checkers.py` | 外部接口检测：uapis.cn 专用适配器（命中/未命中/多词/鉴权头/400 兜底）+ 通用模式（POST/GET/自定义字段路径） |
| `test_llm_checker.py` | AI 语义检测：单条审核（LLM 返回干净 JSON / 夹杂文字 / 无法解析等场景）+ 批量审核（多条消息一次调用、结果数量不足/下标乱序的兜底、空输入、provider 缺失等场景），以及默认 Prompt 模板渲染不报错 |
| `test_image_checker.py` | 图片检测：视觉 Provider 返回图片违规/不违规、有文字/无文字、夹杂文字、无法解析等场景，验证 `image_urls` 参数正确传递 |
| `test_main_integration.py` | 端到端集成：用最小化 stub 模拟 AstrBot 框架接口，验证检测流程（含图片检测、AI 语义检测批量审核的按数量触发/超时兜底/手动触发/插件关闭前收尾）、撤回/警告/通知动作、分群配置覆盖、访问控制白名单黑名单、各管理指令的实际效果 |

## 依赖

只需要 `aiohttp`（`api_checkers.py` 本身的运行依赖，测试里顺便用它起本地测试服务器）。不需要安装 AstrBot 本体或 pytest——所有测试文件都用最小化的 stub 模块替代了 `astrbot.api` 等真实接口（包括 `Comp.Image` 及其 `convert_to_file_path()` 方法），只验证插件自己的业务逻辑，不验证 AstrBot 框架本身。

```bash
pip install aiohttp --break-system-packages
```

## 改动代码后建议怎么用

每次改完 `main.py`/`word_matcher.py`/`api_checkers.py`/`llm_checker.py`/`image_checker.py`/`utils.py`/`_conf_schema.json` 中的任意一个，建议跑一遍 `run_all.py` 确认没有破坏现有行为，再跑一遍 `ruff check .` 和 `ruff format .`（在插件根目录下执行，针对插件代码本身，不含本目录）。
