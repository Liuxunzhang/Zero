"""AI analysis service for Zero — OpenAI-compatible streaming.

Supports runtime-configurable multi-model profiles, a built-in
prompt library, and filter-rule generation.
"""

import ast
import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from openai import AsyncOpenAI

from lexzero import config
from web.backend.services.memory_store import MemoryStore, build_memory_items_from_text

logger = logging.getLogger(__name__)

# Persist profiles / prompts alongside project root.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_FILE = _PROJECT_ROOT / "lexzero" / "config.py"
_AI_DATA_DIR = _PROJECT_ROOT / ".lexzero" / "ai"
_PROFILES_FILE = _AI_DATA_DIR / "profiles.json"
_PROMPTS_FILE = _AI_DATA_DIR / "prompts.json"
_SETTINGS_FILE = _AI_DATA_DIR / "settings.json"
_MEMORY_FILE = _AI_DATA_DIR / "compressed_memory.json"
_MEMORY_ITEMS_FILE = _AI_DATA_DIR / "memory_items.json"
_MEMORY_STATS_FILE = _AI_DATA_DIR / "memory_stats.json"
_LEGACY_PROMPTS_FILE = _PROJECT_ROOT / ".lexzero_ai_prompts.json"
_DEFAULT_SETTINGS = {
    "persist_to_config_py": False,
    "active_profile_id": None,
    "active_prompt_id": "default",
    "ai_max_tokens": 4096,
    "ai_temperature": 0.1,
    "ai_max_history": 12,
    "ai_context_max_rows": 500,
    "ai_context_max_chars": 60000,
    "ai_memory_enabled": True,
    "ai_memory_max_chars": 10000,
    "ai_memory_retrieval_top_k": 10,
    "ai_memory_retrieval_max_chars": 6000,
    "ai_memory_ttl_days": 30,
}

_MODEL_TOKEN_LIMITS = {
    "minimax-m2.5": 262144,
    "kimi/kimi-k2.5": 262144,
    "glm-5": 202752,
    "glm-4.7": 169984,
}

# ── Built-in prompt library ────────────────────────────────────────

_BUILTIN_PROMPTS: list[dict] = [
    {
        "id": "default",
        "name": "默认取证分析",
        "builtin": True,
        "content": """\
你是一位资深内存取证分析师（Memory Forensics Analyst），精通 Volatility 框架及各类内存分析技术。

工作方式：
1. 只基于当前插件输出、历史对话和明确给出的事实分析，不编造缺失字段。
2. 优先引用证据：插件名、列名、PID/PPID、路径、偏移、端口、注册表键、时间戳等。
3. 把结论分为：已确认事实、可疑迹象、无法确认/需要补证。
4. 对每个可疑项给出风险等级（高/中/低）和证据强度，不足以判断时明确说明。
5. 给出下一步 Volatility 插件验证路径，并说明每个插件要验证的假设。
6. 如需筛选结果，使用 ```filter 代码块输出 Zero 过滤规则。
7. 保持智能体式推进：先判断当前数据能回答什么，再给出最小下一步动作，不输出泛泛安全建议。
8. 需要长分析时优先输出证据摘要和决策点，避免复述完整表格。

推荐输出结构：
## 结论概览
## 关键证据
## 可疑项分级
## 无法确认的点
## 下一步验证路径

保持措辞克制、可复核，避免把常见系统行为误判为恶意。""",
    },
    {
        "id": "low_fp",
        "name": "严格低误报模式",
        "builtin": True,
        "content": """\
你是一位极度保守、精准的内存取证分析专家。你的首要原则是**绝不误报**。

分析规则：
1. **只标记有明确证据的发现**，不要基于"可能"或"通常"进行猜测
2. 对每个标记为可疑的条目，必须给出**具体的判断依据**
3. 以下情况不应标记为可疑：
   - 正常的系统进程（svchost.exe, csrss.exe 等）以标准路径和标准父进程运行
   - 没有明显异常特征的普通用户进程
   - 已知安全软件的正常行为
4. 可疑标准（必须满足至少 2 个才标记）：
   - 进程路径异常（不在标准系统目录）
   - 父进程关系异常
   - 进程名伪装（如名称拼写相似但路径不同）
   - 异常的网络连接目标
   - 异常的内存权限（如 RWX）
   - 隐藏/未链接的内核对象
5. 输出时明确标注置信度：
   - 高 (90%+): 强烈建议调查
   - 中 (60-90%): 值得关注
   - 不报告低于 60% 置信度的发现""",
    },
    {
        "id": "timeline",
        "name": "时间线分析",
        "builtin": True,
        "content": """\
你是一位专注于时间线重建的数字取证分析师。

分析重点：
1. 按时间顺序排列事件，构建攻击时间线
2. 识别关键时间节点：初始入侵、横向移动、持久化、数据外泄
3. 关注进程创建/退出的时间模式
4. 检测与正常业务时间不符的异常活动（如凌晨操作）
5. 将数据通过时间线关联，发现因果关系

输出格式：
- 使用时间线格式展示事件序列
- 标注每个事件的重要性等级
- 总结攻击阶段和关键转折点""",
    },
    {
        "id": "malware_hunt",
        "name": "恶意代码猎捕",
        "builtin": True,
        "content": """\
你是一位恶意代码分析专家，专注于在内存取证数据中发现恶意软件痕迹。

检测重点：
1. **进程注入**: 检查是否有进程加载了不应有的 DLL/模块
2. **代码注入**: 查找具有 RWX 权限的可疑内存区域
3. **进程伪装**: 名称拼写与系统进程相似但路径/PID/父进程异常
4. **Rootkit**: 进程隐藏、DKOM、SSDT hook 等
5. **持久化**: 异常的注册表项、服务、计划任务
6. **C2 通信**: 可疑的出站网络连接（非标准端口、异常域名）

每个发现都应包含：
- 具体的恶意指标（IOC）
- 关联的 MITRE ATT&CK 技术编号
- 推荐的下一步分析插件
- 提取 IOC 用于进一步威胁情报比对""",
    },
    {
        "id": "filter_expert",
        "name": "过滤规则专家",
        "builtin": True,
        "content": """\
你既是内存取证分析师，也是 Zero 过滤规则专家。

Zero 过滤规则语法：
  <column> <operator> <value> [<logic> <column> <operator> <value> ...]

支持的操作符：
  -eq (=)  -ne (≠)  -gt (>)  -lt (<)  -ge (≥)  -le (≤)
  -contain (包含)  -notcontain (不包含)
  -match (正则)  -startswith (前缀)  -endswith (后缀)

逻辑操作符：&& (与)  || (或)

你的职责：
1. 根据用户描述的分析目标，生成精确的 Zero 过滤规则
2. 基于当前插件数据分析，主动推荐有价值的过滤规则
3. 每条规则需说明用途

输出格式：
当你生成过滤规则时，必须用以下格式包裹，以便系统自动识别和应用：
```filter
<过滤规则>
```

示例：
```filter
ImageFileName -notcontain "svchost.exe" && ImageFileName -notcontain "csrss.exe" && PPID -ne 4
```

注意：列名必须与实际数据列名完全匹配（区分大小写）。""",
    },
]


# ── Profile management ─────────────────────────────────────────────

def _find_bracket_end(text: str, open_pos: int) -> int:
    """Find matching ] for [ at open_pos, handling strings and comments."""
    depth = 0
    i = open_pos
    in_str = None
    while i < len(text):
        c = text[i]
        if in_str:
            if c == '\\':
                i += 2
                continue
            if c == in_str:
                in_str = None
        else:
            if c == '#':
                nl = text.find('\n', i)
                i = nl if nl >= 0 else len(text)
                continue
            if c in ('"', "'"):
                in_str = c
            elif c == '[':
                depth += 1
            elif c == ']':
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1


def _update_config_value(content: str, key: str, value) -> str:
    """Update a single KEY = VALUE line in config.py, preserving inline comments."""
    val_str = json.dumps(value, ensure_ascii=False) if isinstance(value, str) else repr(value)
    pattern = rf'^({key}\s*=\s*)("(?:[^"\\]|\\.)*"|\x27(?:[^\x27\\]|\\.)*\x27|[^\s#]+)(.*?)$'

    def _replacer(m):
        prefix, _, tail = m.group(1), m.group(2), (m.group(3) or '').lstrip()
        new_val = f"{prefix}{val_str}"
        if tail:
            pad = max(1, 50 - len(new_val))
            return f"{new_val}{' ' * pad}{tail}"
        return new_val

    return re.sub(pattern, _replacer, content, count=1, flags=re.MULTILINE)


def _ensure_ai_data_dir() -> None:
    _AI_DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_ai_settings() -> dict:
    if _SETTINGS_FILE.exists():
        try:
            raw = json.loads(_SETTINGS_FILE.read_text("utf-8"))
            if isinstance(raw, dict):
                return {
                    "persist_to_config_py": bool(raw.get("persist_to_config_py", False)),
                    "active_profile_id": raw.get("active_profile_id"),
                    "active_prompt_id": raw.get("active_prompt_id") or "default",
                    "ai_max_tokens": raw.get("ai_max_tokens", _DEFAULT_SETTINGS["ai_max_tokens"]),
                    "ai_temperature": raw.get("ai_temperature", _DEFAULT_SETTINGS["ai_temperature"]),
                    "ai_max_history": raw.get("ai_max_history", _DEFAULT_SETTINGS["ai_max_history"]),
                    "ai_context_max_rows": raw.get("ai_context_max_rows", _DEFAULT_SETTINGS["ai_context_max_rows"]),
                    "ai_context_max_chars": raw.get("ai_context_max_chars", _DEFAULT_SETTINGS["ai_context_max_chars"]),
                    "ai_memory_enabled": bool(raw.get("ai_memory_enabled", _DEFAULT_SETTINGS["ai_memory_enabled"])),
                    "ai_memory_max_chars": raw.get("ai_memory_max_chars", _DEFAULT_SETTINGS["ai_memory_max_chars"]),
                    "ai_memory_retrieval_top_k": raw.get("ai_memory_retrieval_top_k", _DEFAULT_SETTINGS["ai_memory_retrieval_top_k"]),
                    "ai_memory_retrieval_max_chars": raw.get("ai_memory_retrieval_max_chars", _DEFAULT_SETTINGS["ai_memory_retrieval_max_chars"]),
                    "ai_memory_ttl_days": raw.get("ai_memory_ttl_days", _DEFAULT_SETTINGS["ai_memory_ttl_days"]),
                }
        except Exception:
            logger.warning("Failed to load AI settings JSON")
    return dict(_DEFAULT_SETTINGS)


def _save_ai_settings(settings: dict) -> None:
    _ensure_ai_data_dir()
    payload = {
        "persist_to_config_py": bool(settings.get("persist_to_config_py", False)),
        "active_profile_id": settings.get("active_profile_id"),
        "active_prompt_id": settings.get("active_prompt_id") or "default",
        "ai_max_tokens": settings.get("ai_max_tokens", _DEFAULT_SETTINGS["ai_max_tokens"]),
        "ai_temperature": settings.get("ai_temperature", _DEFAULT_SETTINGS["ai_temperature"]),
        "ai_max_history": settings.get("ai_max_history", _DEFAULT_SETTINGS["ai_max_history"]),
        "ai_context_max_rows": settings.get("ai_context_max_rows", _DEFAULT_SETTINGS["ai_context_max_rows"]),
        "ai_context_max_chars": settings.get("ai_context_max_chars", _DEFAULT_SETTINGS["ai_context_max_chars"]),
        "ai_memory_enabled": bool(settings.get("ai_memory_enabled", _DEFAULT_SETTINGS["ai_memory_enabled"])),
        "ai_memory_max_chars": settings.get("ai_memory_max_chars", _DEFAULT_SETTINGS["ai_memory_max_chars"]),
        "ai_memory_retrieval_top_k": settings.get("ai_memory_retrieval_top_k", _DEFAULT_SETTINGS["ai_memory_retrieval_top_k"]),
        "ai_memory_retrieval_max_chars": settings.get("ai_memory_retrieval_max_chars", _DEFAULT_SETTINGS["ai_memory_retrieval_max_chars"]),
        "ai_memory_ttl_days": settings.get("ai_memory_ttl_days", _DEFAULT_SETTINGS["ai_memory_ttl_days"]),
    }
    _SETTINGS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")


def _load_compressed_memory() -> str:
    if _MEMORY_FILE.exists():
        try:
            raw = json.loads(_MEMORY_FILE.read_text("utf-8"))
            if isinstance(raw, dict):
                return str(raw.get("compressed_memory", ""))
        except Exception:
            logger.warning("Failed to load compressed memory JSON")
    return ""


def _save_compressed_memory(text: str) -> None:
    _ensure_ai_data_dir()
    payload = {"compressed_memory": text}
    _MEMORY_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")


def _load_profiles_from_config() -> list[dict]:
    """Load AI_PROFILES list from config.py."""
    try:
        content = _CONFIG_FILE.read_text("utf-8")
        match = re.search(r'AI_PROFILES\s*=\s*\[', content)
        if not match:
            return []
        bracket_start = content.index('[', match.start())
        bracket_end = _find_bracket_end(content, bracket_start)
        if bracket_end < 0:
            return []
        result = ast.literal_eval(content[bracket_start:bracket_end + 1])
        return result if isinstance(result, list) else []
    except Exception:
        logger.warning("Failed to parse AI_PROFILES from config.py")
        return []


def _save_profiles_to_config(profiles: list[dict]) -> None:
    """Write AI_PROFILES list back to config.py."""
    try:
        content = _CONFIG_FILE.read_text("utf-8")
        # Build new block
        if not profiles:
            new_block = "AI_PROFILES = []\n"
        else:
            lines = ["AI_PROFILES = ["]
            for p in profiles:
                entry = {k: p.get(k, "") for k in ("id", "name", "base_url", "api_key", "model")}
                lines.append(f"    {json.dumps(entry, ensure_ascii=False)},")
            lines.append("]")
            new_block = "\n".join(lines) + "\n"
        # Replace existing block
        match = re.search(r'AI_PROFILES\s*=\s*\[', content)
        if match:
            bracket_start = content.index('[', match.start())
            bracket_end = _find_bracket_end(content, bracket_start)
            if bracket_end >= 0:
                line_end = bracket_end + 1
                while line_end < len(content) and content[line_end] in (' ', '\t'):
                    line_end += 1
                if line_end < len(content) and content[line_end] == '\n':
                    line_end += 1
                content = content[:match.start()] + new_block + content[line_end:]
        else:
            content = content.rstrip() + "\n\n" + new_block
        _CONFIG_FILE.write_text(content, "utf-8")
        # Sync in-memory module
        config.AI_PROFILES = profiles
    except Exception as e:
        logger.error("Failed to write AI_PROFILES to config.py: %s", e, exc_info=True)


def _normalize_profiles(profiles: list[dict]) -> list[dict]:
    return [
        {k: p.get(k, "") for k in ("id", "name", "base_url", "api_key", "model")}
        for p in profiles
        if isinstance(p, dict)
    ]


def _load_profiles_from_json() -> list[dict]:
    if _PROFILES_FILE.exists():
        try:
            raw = json.loads(_PROFILES_FILE.read_text("utf-8"))
            if isinstance(raw, list):
                return _normalize_profiles(raw)
        except Exception:
            logger.warning("Failed to load profiles JSON")
    return []


def _save_profiles_to_json(profiles: list[dict]) -> None:
    _ensure_ai_data_dir()
    _PROFILES_FILE.write_text(
        json.dumps(_normalize_profiles(profiles), ensure_ascii=False, indent=2),
        "utf-8",
    )


def _persist_active_to_config(profile: Optional[dict]) -> None:
    """Write active profile's settings to AI_BASE_URL / AI_API_KEY / AI_MODEL in config.py."""
    if not profile:
        return
    try:
        content = _CONFIG_FILE.read_text("utf-8")
        content = _update_config_value(content, "AI_BASE_URL", profile.get("base_url", ""))
        content = _update_config_value(content, "AI_API_KEY", profile.get("api_key", ""))
        content = _update_config_value(content, "AI_MODEL", profile.get("model", ""))
        _CONFIG_FILE.write_text(content, "utf-8")
        # Sync in-memory module
        config.AI_BASE_URL = profile.get("base_url", "")
        config.AI_API_KEY = profile.get("api_key", "")
        config.AI_MODEL = profile.get("model", "")
    except Exception as e:
        logger.error("Failed to persist active profile to config.py: %s", e, exc_info=True)


def _load_custom_prompts() -> list[dict]:
    if _PROMPTS_FILE.exists():
        try:
            return json.loads(_PROMPTS_FILE.read_text("utf-8"))
        except Exception:
            logger.warning("Failed to load custom prompts")
            return []

    if _LEGACY_PROMPTS_FILE.exists():
        try:
            raw = json.loads(_LEGACY_PROMPTS_FILE.read_text("utf-8"))
            prompts = raw if isinstance(raw, list) else []
            _save_custom_prompts(prompts)
            try:
                _LEGACY_PROMPTS_FILE.unlink()
            except Exception:
                logger.warning("Failed to remove legacy prompts file")
            return prompts
        except Exception:
            logger.warning("Failed to migrate legacy custom prompts")
    return []


def _save_custom_prompts(prompts: list[dict]) -> None:
    _PROMPTS_FILE.write_text(json.dumps(prompts, ensure_ascii=False, indent=2), "utf-8")


def _format_plugin_context(plugin_context: dict) -> str:
    """Format plugin results into a concise text block for the AI."""
    plugin_name = plugin_context.get("plugin", "unknown")
    columns: list[str] = plugin_context.get("columns", [])
    rows: list[list] = plugin_context.get("rows", [])
    total: int = plugin_context.get("total", len(rows))

    max_rows = plugin_context.get("_max_rows", 200)
    if max_rows == "max":
        truncated = rows
    else:
        try:
            max_rows_int = max(1, int(max_rows))
        except (TypeError, ValueError):
            max_rows_int = 200
        truncated = rows[:max_rows_int]

    try:
        max_chars = max(1000, int(plugin_context.get("_max_chars", _DEFAULT_SETTINGS["ai_context_max_chars"])))
    except (TypeError, ValueError):
        max_chars = _DEFAULT_SETTINGS["ai_context_max_chars"]

    lines = [
        f"## 当前插件: {plugin_name}",
        f"总行数: {total}（以下展示前 {len(truncated)} 行）",
        "",
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    shown_rows = 0
    for row in truncated:
        cells = [str(c).replace("|", "\\|").replace("\n", " ")[:180] for c in row]
        line = "| " + " | ".join(cells) + " |"
        if len("\n".join(lines)) + len(line) + 1 > max_chars:
            break
        lines.append(line)
        shown_rows += 1

    if shown_rows < len(truncated):
        lines.extend(
            [
                "",
                f"> 上下文已按字符预算截断（{shown_rows}/{len(truncated)} 行）",
            ]
        )

    return "\n".join(lines)


class AiService:
    """Manages multi-model profiles, prompt library, and streaming."""

    def __init__(self) -> None:
        self._history: list[dict[str, str]] = []
        # Active profile overrides — set at runtime from frontend
        self._active_profile: Optional[dict] = None
        self._persist_to_config_py: bool = False
        # Active prompt id
        self._active_prompt_id: str = "default"
        self._ai_max_tokens: int | str = 4096
        self._ai_temperature: float = _DEFAULT_SETTINGS["ai_temperature"]
        self._ai_max_history: int = 20
        self._ai_context_max_rows: int | str = _DEFAULT_SETTINGS["ai_context_max_rows"]
        self._ai_context_max_chars: int = _DEFAULT_SETTINGS["ai_context_max_chars"]
        self._ai_memory_enabled: bool = True
        self._ai_memory_max_chars: int = _DEFAULT_SETTINGS["ai_memory_max_chars"]
        self._ai_memory_retrieval_top_k: int = _DEFAULT_SETTINGS["ai_memory_retrieval_top_k"]
        self._ai_memory_retrieval_max_chars: int = _DEFAULT_SETTINGS["ai_memory_retrieval_max_chars"]
        self._ai_memory_ttl_days: int = _DEFAULT_SETTINGS["ai_memory_ttl_days"]
        self._compressed_memory: str = ""
        self._memory_store = MemoryStore(_MEMORY_ITEMS_FILE, _MEMORY_STATS_FILE)
        self._memory_status: str = "idle"
        self._memory_last_error: str = ""
        self._memory_update_task: Optional[asyncio.Task] = None
        self._memory_update_lock = asyncio.Lock()
        self._load_runtime_state()

    def _normalize_int_or_max(self, value, fallback: int, min_value: int = 1) -> int | str:
        if isinstance(value, str) and value.strip().lower() == "max":
            return "max"
        try:
            return max(min_value, int(value))
        except (TypeError, ValueError):
            return fallback

    def _normalize_temperature(self, value, fallback: float = 0.3) -> float:
        try:
            val = float(value)
        except (TypeError, ValueError):
            return fallback
        return max(0.0, min(2.0, val))

    def _model_token_limit(self, model: str) -> Optional[int]:
        key = (model or "").strip().lower()
        if not key:
            return None
        return _MODEL_TOKEN_LIMITS.get(key)

    def _resolve_max_tokens(self, model: str) -> Optional[int]:
        if self._ai_max_tokens == "max":
            return self._model_token_limit(model)
        try:
            return max(1, int(self._ai_max_tokens))
        except (TypeError, ValueError):
            return max(1, int(getattr(config, "AI_MAX_TOKENS", 4096)))

    def _resolve_context_max_rows(self) -> int | str:
        return self._ai_context_max_rows

    def _resolve_context_max_chars(self) -> int:
        return max(1000, int(self._ai_context_max_chars))

    def _resolve_memory_retrieval_top_k(self) -> int:
        return max(1, int(self._ai_memory_retrieval_top_k))

    def _resolve_memory_retrieval_max_chars(self) -> int:
        return max(500, int(self._ai_memory_retrieval_max_chars))

    def _trim_memory(self, text: str) -> str:
        limit = max(500, int(self._ai_memory_max_chars or _DEFAULT_SETTINGS["ai_memory_max_chars"]))
        text = (text or "").strip()
        if len(text) <= limit:
            return text
        return text[-limit:]

    def _build_memory_seed(
        self,
        prev_memory: str,
        user_message: str,
        assistant_message: str,
        plugin_context: Optional[dict],
    ) -> str:
        plugin_name = "none"
        if plugin_context:
            plugin_name = str(plugin_context.get("plugin") or "none")

        lines = [
            "### Previous compressed memory",
            prev_memory or "(empty)",
            "",
            "### Latest user question",
            user_message or "",
            "",
            "### Latest assistant answer",
            assistant_message or "",
            "",
            "### Data source",
            f"plugin={plugin_name}",
        ]
        return "\n".join(lines)

    async def _update_compressed_memory(
        self,
        client: AsyncOpenAI,
        model: str,
        user_message: str,
        assistant_message: str,
        plugin_context: Optional[dict],
    ) -> bool:
        if not self._ai_memory_enabled:
            return True

        seed = self._build_memory_seed(
            self._compressed_memory,
            user_message,
            assistant_message,
            plugin_context,
        )

        compress_prompt = (
            "You are a forensic conversation memory compressor. "
            "Produce concise markdown in Chinese with strict source attribution. "
            "Sections required: Facts by Source, Cross-plugin Conclusions, Unknown/Not Confirmed, Next Checks. "
            "Every fact must cite source plugin in square brackets like [cmdline] or [pslist]. "
            "Never invent missing evidence. Keep it compact."
        )

        max_tokens = self._resolve_max_tokens(model)
        req = {
            "model": model,
            "messages": [
                {"role": "system", "content": compress_prompt},
                {"role": "user", "content": seed},
            ],
            "temperature": 0.1,
            "stream": False,
        }
        if max_tokens is not None:
            req["max_tokens"] = max_tokens

        try:
            resp = await client.chat.completions.create(
                model=model,
                **{k: v for k, v in req.items() if k != "model"},
            )
            text = ""
            if resp.choices and resp.choices[0].message:
                text = resp.choices[0].message.content or ""
            self._compressed_memory = self._trim_memory(text)
            _save_compressed_memory(self._compressed_memory)
            return True
        except Exception:
            logger.warning("Failed to update compressed memory", exc_info=True)
            return False

    def _load_runtime_state(self) -> None:
        settings = _load_ai_settings()
        has_settings_file = _SETTINGS_FILE.exists()
        self._persist_to_config_py = bool(settings.get("persist_to_config_py", False))
        self._active_prompt_id = settings.get("active_prompt_id") or "default"

        fallback_max_tokens = int(getattr(config, "AI_MAX_TOKENS", _DEFAULT_SETTINGS["ai_max_tokens"]))
        fallback_temperature = float(getattr(config, "AI_TEMPERATURE", _DEFAULT_SETTINGS["ai_temperature"]))
        fallback_max_history = int(getattr(config, "AI_MAX_HISTORY", _DEFAULT_SETTINGS["ai_max_history"]))
        fallback_context_rows = int(getattr(config, "AI_CONTEXT_MAX_ROWS", _DEFAULT_SETTINGS["ai_context_max_rows"]))
        fallback_context_chars = int(_DEFAULT_SETTINGS["ai_context_max_chars"])
        fallback_memory_enabled = bool(_DEFAULT_SETTINGS["ai_memory_enabled"])
        fallback_memory_max_chars = int(_DEFAULT_SETTINGS["ai_memory_max_chars"])
        fallback_memory_top_k = int(_DEFAULT_SETTINGS["ai_memory_retrieval_top_k"])
        fallback_memory_max_context_chars = int(_DEFAULT_SETTINGS["ai_memory_retrieval_max_chars"])
        fallback_memory_ttl_days = int(_DEFAULT_SETTINGS["ai_memory_ttl_days"])

        if has_settings_file:
            self._ai_max_tokens = self._normalize_int_or_max(settings.get("ai_max_tokens"), fallback_max_tokens)
            self._ai_temperature = self._normalize_temperature(settings.get("ai_temperature"), fallback_temperature)
            self._ai_max_history = max(1, int(self._normalize_int_or_max(settings.get("ai_max_history"), fallback_max_history)))
            self._ai_context_max_rows = self._normalize_int_or_max(settings.get("ai_context_max_rows"), fallback_context_rows)
            self._ai_context_max_chars = max(1000, int(settings.get("ai_context_max_chars", fallback_context_chars)))
            self._ai_memory_enabled = bool(settings.get("ai_memory_enabled", fallback_memory_enabled))
            self._ai_memory_max_chars = max(500, int(settings.get("ai_memory_max_chars", fallback_memory_max_chars)))
            self._ai_memory_retrieval_top_k = max(1, int(settings.get("ai_memory_retrieval_top_k", fallback_memory_top_k)))
            self._ai_memory_retrieval_max_chars = max(500, int(settings.get("ai_memory_retrieval_max_chars", fallback_memory_max_context_chars)))
            self._ai_memory_ttl_days = max(1, int(settings.get("ai_memory_ttl_days", fallback_memory_ttl_days)))
        else:
            self._ai_max_tokens = fallback_max_tokens
            self._ai_temperature = self._normalize_temperature(fallback_temperature)
            self._ai_max_history = max(1, fallback_max_history)
            self._ai_context_max_rows = max(1, fallback_context_rows)
            self._ai_context_max_chars = fallback_context_chars
            self._ai_memory_enabled = fallback_memory_enabled
            self._ai_memory_max_chars = fallback_memory_max_chars
            self._ai_memory_retrieval_top_k = fallback_memory_top_k
            self._ai_memory_retrieval_max_chars = fallback_memory_max_context_chars
            self._ai_memory_ttl_days = fallback_memory_ttl_days

        self._compressed_memory = _load_compressed_memory()
        self._compressed_memory = self._trim_memory(self._compressed_memory)
        self._memory_store.cleanup(ttl_days=self._ai_memory_ttl_days)

        profiles = _load_profiles_from_json()
        if not profiles:
            profiles = _load_profiles_from_config()
            if profiles:
                _save_profiles_to_json(profiles)

        active_id = settings.get("active_profile_id")
        if active_id:
            self._active_profile = next((p for p in profiles if p.get("id") == active_id), None)

    def get_ai_settings(self) -> dict:
        _, _, model = self._resolve_config()
        return {
            "ai_max_tokens": self._ai_max_tokens,
            "ai_temperature": self._ai_temperature,
            "ai_max_history": self._ai_max_history,
            "ai_context_max_rows": self._ai_context_max_rows,
            "ai_context_max_chars": self._ai_context_max_chars,
            "ai_memory_enabled": self._ai_memory_enabled,
            "ai_memory_max_chars": self._ai_memory_max_chars,
            "ai_memory_retrieval_top_k": self._ai_memory_retrieval_top_k,
            "ai_memory_retrieval_max_chars": self._ai_memory_retrieval_max_chars,
            "ai_memory_ttl_days": self._ai_memory_ttl_days,
            "model_token_limit": self._model_token_limit(model),
            "current_model": model,
            "known_model_limits": dict(_MODEL_TOKEN_LIMITS),
            "compressed_memory": self._compressed_memory,
            "memory_status": self._memory_status,
            "memory_last_error": self._memory_last_error,
        }

    def save_ai_settings(self, payload: dict) -> dict:
        if "ai_max_tokens" in payload:
            self._ai_max_tokens = self._normalize_int_or_max(payload.get("ai_max_tokens"), 4096)
        if "ai_temperature" in payload:
            self._ai_temperature = self._normalize_temperature(payload.get("ai_temperature"), _DEFAULT_SETTINGS["ai_temperature"])
        if "ai_max_history" in payload:
            self._ai_max_history = max(1, int(self._normalize_int_or_max(payload.get("ai_max_history"), 20)))
        if "ai_context_max_rows" in payload:
            self._ai_context_max_rows = self._normalize_int_or_max(payload.get("ai_context_max_rows"), _DEFAULT_SETTINGS["ai_context_max_rows"])
        if "ai_context_max_chars" in payload:
            try:
                self._ai_context_max_chars = max(1000, int(payload.get("ai_context_max_chars")))
            except (TypeError, ValueError):
                self._ai_context_max_chars = _DEFAULT_SETTINGS["ai_context_max_chars"]
        if "ai_memory_enabled" in payload:
            self._ai_memory_enabled = bool(payload.get("ai_memory_enabled"))
        if "ai_memory_max_chars" in payload:
            try:
                self._ai_memory_max_chars = max(500, int(payload.get("ai_memory_max_chars")))
            except (TypeError, ValueError):
                self._ai_memory_max_chars = _DEFAULT_SETTINGS["ai_memory_max_chars"]
        if "ai_memory_retrieval_top_k" in payload:
            try:
                self._ai_memory_retrieval_top_k = max(1, int(payload.get("ai_memory_retrieval_top_k")))
            except (TypeError, ValueError):
                self._ai_memory_retrieval_top_k = _DEFAULT_SETTINGS["ai_memory_retrieval_top_k"]
        if "ai_memory_retrieval_max_chars" in payload:
            try:
                self._ai_memory_retrieval_max_chars = max(500, int(payload.get("ai_memory_retrieval_max_chars")))
            except (TypeError, ValueError):
                self._ai_memory_retrieval_max_chars = _DEFAULT_SETTINGS["ai_memory_retrieval_max_chars"]
        if "ai_memory_ttl_days" in payload:
            try:
                self._ai_memory_ttl_days = max(1, int(payload.get("ai_memory_ttl_days")))
            except (TypeError, ValueError):
                self._ai_memory_ttl_days = _DEFAULT_SETTINGS["ai_memory_ttl_days"]

        settings = _load_ai_settings()
        settings["ai_max_tokens"] = self._ai_max_tokens
        settings["ai_temperature"] = self._ai_temperature
        settings["ai_max_history"] = self._ai_max_history
        settings["ai_context_max_rows"] = self._ai_context_max_rows
        settings["ai_context_max_chars"] = self._ai_context_max_chars
        settings["ai_memory_enabled"] = self._ai_memory_enabled
        settings["ai_memory_max_chars"] = self._ai_memory_max_chars
        settings["ai_memory_retrieval_top_k"] = self._ai_memory_retrieval_top_k
        settings["ai_memory_retrieval_max_chars"] = self._ai_memory_retrieval_max_chars
        settings["ai_memory_ttl_days"] = self._ai_memory_ttl_days
        _save_ai_settings(settings)
        self._memory_store.cleanup(ttl_days=self._ai_memory_ttl_days)

        if self._persist_to_config_py:
            try:
                content = _CONFIG_FILE.read_text("utf-8")
                content = _update_config_value(content, "AI_MAX_TOKENS", self._ai_max_tokens)
                content = _update_config_value(content, "AI_TEMPERATURE", self._ai_temperature)
                content = _update_config_value(content, "AI_MAX_HISTORY", self._ai_max_history)
                content = _update_config_value(content, "AI_CONTEXT_MAX_ROWS", self._ai_context_max_rows)
                _CONFIG_FILE.write_text(content, "utf-8")
                config.AI_MAX_TOKENS = self._ai_max_tokens
                config.AI_TEMPERATURE = self._ai_temperature
                config.AI_MAX_HISTORY = self._ai_max_history
                config.AI_CONTEXT_MAX_ROWS = self._ai_context_max_rows
            except Exception as e:
                logger.error("Failed to persist AI settings to config.py: %s", e, exc_info=True)

        return self.get_ai_settings()

    # ── Profile API ───────────────────────────────────────────────

    def get_profiles(self) -> list[dict]:
        """Return all saved profiles."""
        profiles = _load_profiles_from_json()
        if profiles:
            return profiles
        profiles = _load_profiles_from_config()
        if profiles:
            _save_profiles_to_json(profiles)
        return profiles

    def save_profiles(self, profiles: list[dict]) -> None:
        normalized = _normalize_profiles(profiles)
        _save_profiles_to_json(normalized)
        if self._persist_to_config_py:
            _save_profiles_to_config(normalized)

        if self._active_profile:
            active_id = self._active_profile.get("id")
            self._active_profile = next((p for p in normalized if p.get("id") == active_id), None)
            settings = _load_ai_settings()
            settings["active_profile_id"] = self._active_profile.get("id") if self._active_profile else None
            _save_ai_settings(settings)
            if self._persist_to_config_py and self._active_profile:
                _persist_active_to_config(self._active_profile)

    def set_active_profile(self, profile: Optional[dict]) -> None:
        self._active_profile = profile
        settings = _load_ai_settings()
        settings["active_profile_id"] = profile.get("id") if profile else None
        _save_ai_settings(settings)
        if self._persist_to_config_py and profile:
            _persist_active_to_config(profile)

    def get_active_profile(self) -> Optional[dict]:
        return self._active_profile

    def get_persist_to_config(self) -> bool:
        return self._persist_to_config_py

    def set_persist_to_config(self, enabled: bool) -> bool:
        self._persist_to_config_py = bool(enabled)
        settings = _load_ai_settings()
        settings["persist_to_config_py"] = self._persist_to_config_py
        _save_ai_settings(settings)

        if self._persist_to_config_py:
            profiles = self.get_profiles()
            _save_profiles_to_config(profiles)
            if self._active_profile:
                _persist_active_to_config(self._active_profile)
            self.save_ai_settings({
                "ai_max_tokens": self._ai_max_tokens,
                "ai_temperature": self._ai_temperature,
                "ai_max_history": self._ai_max_history,
                "ai_context_max_rows": self._ai_context_max_rows,
            })
        return self._persist_to_config_py

    def clear_compressed_memory(self) -> None:
        self._compressed_memory = ""
        _save_compressed_memory("")

    def get_memory_stats(self) -> dict:
        stats = self._memory_store.get_stats()
        stats["status"] = self._memory_status
        stats["last_error"] = self._memory_last_error
        return stats

    @staticmethod
    def _format_retrieved_memory(items: list[dict[str, Any]]) -> str:
        if not items:
            return ""
        lines = [
            "以下为与当前问题最相关的历史记忆条目（按相关性排序）：",
        ]
        for item in items:
            lines.append(
                f"- [{item.get('source_plugin','none')}] "
                f"{item.get('type','fact')} "
                f"(conf={float(item.get('confidence', 0.5)):.2f}) "
                f"{item.get('content','')}"
            )
        return "\n".join(lines)

    async def _run_memory_update_async(
        self,
        base_url: str,
        api_key: str,
        model: str,
        user_message: str,
        assistant_message: str,
        plugin_context: Optional[dict],
    ) -> None:
        async with self._memory_update_lock:
            started_at = time.monotonic()
            self._memory_status = "compressing"
            self._memory_last_error = ""
            ok = True
            try:
                client = AsyncOpenAI(base_url=base_url, api_key=api_key)
                await self._update_compressed_memory(
                    client=client,
                    model=model,
                    user_message=user_message,
                    assistant_message=assistant_message,
                    plugin_context=plugin_context,
                )
                plugin_name = "none"
                if plugin_context:
                    plugin_name = str(plugin_context.get("plugin") or "none")
                new_items = build_memory_items_from_text(
                    assistant_message,
                    source_plugin=plugin_name,
                )
                self._memory_store.upsert_items(new_items)
                self._memory_store.cleanup(ttl_days=self._ai_memory_ttl_days)
                self._memory_status = "done"
            except Exception as e:
                ok = False
                self._memory_status = "failed"
                self._memory_last_error = str(e)
                logger.error("Async memory update failed: %s", e, exc_info=True)
            finally:
                elapsed_ms = int((time.monotonic() - started_at) * 1000)
                self._memory_store.record_compression_result(ok=ok, elapsed_ms=elapsed_ms)

    # ── Prompt API ────────────────────────────────────────────────

    def get_all_prompts(self) -> list[dict]:
        """Return built-in + custom prompts."""
        customs = _load_custom_prompts()
        return list(_BUILTIN_PROMPTS) + customs

    def save_custom_prompt(self, prompt: dict) -> None:
        customs = _load_custom_prompts()
        # Update existing or append
        for i, p in enumerate(customs):
            if p["id"] == prompt["id"]:
                customs[i] = prompt
                _save_custom_prompts(customs)
                return
        customs.append(prompt)
        _save_custom_prompts(customs)

    def delete_custom_prompt(self, prompt_id: str) -> bool:
        customs = _load_custom_prompts()
        new = [p for p in customs if p["id"] != prompt_id]
        if len(new) < len(customs):
            _save_custom_prompts(new)
            return True
        return False

    def set_active_prompt(self, prompt_id: str) -> None:
        self._active_prompt_id = prompt_id
        settings = _load_ai_settings()
        settings["active_prompt_id"] = prompt_id or "default"
        _save_ai_settings(settings)

    def get_active_prompt_id(self) -> str:
        return self._active_prompt_id

    # ── Config resolution ─────────────────────────────────────────

    def _resolve_config(self) -> tuple[str, str, str]:
        """Return (base_url, api_key, model)."""
        if self._active_profile:
            return (
                self._active_profile.get("base_url", ""),
                self._active_profile.get("api_key", "") or "sk-placeholder",
                self._active_profile.get("model", ""),
            )
        # Fall back to config.py
        base_url = getattr(config, "AI_BASE_URL", "")
        api_key = getattr(config, "AI_API_KEY", "") or "sk-placeholder"
        model = getattr(config, "AI_MODEL", "")
        return base_url, api_key, model

    def _resolve_system_prompt(self) -> str:
        all_prompts = self.get_all_prompts()
        for p in all_prompts:
            if p["id"] == self._active_prompt_id:
                return p["content"]
        return _BUILTIN_PROMPTS[0]["content"]

    # ── Chat ──────────────────────────────────────────────────────

    async def chat_stream(
        self,
        user_message: str,
        plugin_context: Optional[dict] = None,
    ) -> AsyncGenerator[dict, None]:
        """Yield structured streaming events from the AI model."""
        base_url, api_key, model = self._resolve_config()
        client = AsyncOpenAI(base_url=base_url, api_key=api_key)

        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._resolve_system_prompt()},
            {
                "role": "system",
                "content": (
                    "Zero 运行约束：你是内存取证智能体。回答必须优先使用当前内存镜像插件输出，"
                    "把事实、推断和待验证假设分开；如果当前数据不足，明确说明需要补跑的 Volatility 插件。"
                    "除非用户要求教学解释，否则不要输出通用安全科普或与证据无关的长篇背景。"
                ),
            },
        ]

        # Inject filter syntax reference when the filter-expert prompt is active
        if self._active_prompt_id == "filter_expert" and plugin_context:
            cols = plugin_context.get("columns", [])
            if cols:
                messages.append({
                    "role": "system",
                    "content": f"当前可用的数据列名: {', '.join(cols)}",
                })

        if plugin_context and plugin_context.get("columns"):
            plugin_context = dict(plugin_context)
            plugin_context["_max_rows"] = self._resolve_context_max_rows()
            plugin_context["_max_chars"] = self._resolve_context_max_chars()
            context_text = _format_plugin_context(plugin_context)
            messages.append({
                "role": "system",
                "content": f"以下是用户当前正在查看的 Volatility 插件输出数据：\n\n{context_text}",
            })

        if self._ai_memory_enabled:
            source_plugin = ""
            if plugin_context:
                source_plugin = str(plugin_context.get("plugin") or "")
            retrieved_items = self._memory_store.retrieve(
                query=user_message,
                source_plugin=source_plugin,
                top_k=self._resolve_memory_retrieval_top_k(),
                max_chars=self._resolve_memory_retrieval_max_chars(),
            )
            retrieved_text = self._format_retrieved_memory(retrieved_items)
            if retrieved_text:
                messages.append({"role": "system", "content": retrieved_text})

        if self._ai_memory_enabled and self._compressed_memory:
            messages.append({
                "role": "system",
                "content": (
                    "以下是对历史对话和已分析插件的压缩记忆，请将其作为已确认背景，"
                    "但若与当前插件原始数据冲突，应以当前原始数据为准：\n\n"
                    f"{self._compressed_memory}"
                ),
            })

        max_history = self._ai_max_history
        for msg in self._history[-(max_history * 2):]:
            messages.append(msg)

        messages.append({"role": "user", "content": user_message})
        self._history.append({"role": "user", "content": user_message})

        max_tokens = self._resolve_max_tokens(model)
        temperature = self._ai_temperature

        try:
            req = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "stream": True,
            }
            if max_tokens is not None:
                req["max_tokens"] = max_tokens

            stream = await client.chat.completions.create(
                model=model,
                **{k: v for k, v in req.items() if k != "model"},
            )

            full_response = []
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    full_response.append(delta.content)
                    yield {"type": "chunk", "content": delta.content}

            assistant_text = "".join(full_response)
            self._history.append({
                "role": "assistant",
                "content": assistant_text,
            })

            if self._ai_memory_enabled:
                self._memory_status = "queued"
                yield {"type": "memory_status", "status": "queued"}
                # Cancel any in-flight memory update before starting a new one.
                if self._memory_update_task and not self._memory_update_task.done():
                    self._memory_update_task.cancel()
                    try:
                        await self._memory_update_task
                    except (asyncio.CancelledError, Exception):
                        pass
                self._memory_update_task = asyncio.create_task(
                    self._run_memory_update_async(
                        base_url=base_url,
                        api_key=api_key,
                        model=model,
                        user_message=user_message,
                        assistant_message=assistant_text,
                        plugin_context=plugin_context,
                    )
                )

        except Exception as e:
            logger.error("AI streaming error: %s", e, exc_info=True)
            raise

    # ── History ────────────────────────────────────────────────────

    def get_history(self) -> list[dict[str, str]]:
        return list(self._history)

    def clear_history(self) -> None:
        self._history.clear()

    def get_config_info(self) -> dict:
        base_url, _, model = self._resolve_config()
        profile = self._active_profile
        has_key = bool(self._resolve_config()[1] != "sk-placeholder")
        return {
            "provider": profile.get("name", "config.py") if profile else "config.py",
            "model": model,
            "base_url": base_url,
            "has_api_key": has_key,
            "active_prompt_id": self._active_prompt_id,
            "ai_settings": self.get_ai_settings(),
        }

    def get_compressed_memory(self) -> str:
        return self._compressed_memory


# ── Module-level singleton ────────────────────────────────────────

_ai_service: Optional[AiService] = None


def get_ai_service() -> AiService:
    global _ai_service
    if _ai_service is None:
        _ai_service = AiService()
    return _ai_service
