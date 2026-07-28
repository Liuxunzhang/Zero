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
from typing import Any, AsyncGenerator, Callable, Optional

from openai import AsyncOpenAI

from zero import config
from web.backend.services.memory_store import MemoryStore, build_memory_items_from_text
from web.backend.services.tool_markup import (
    DsmlStreamFilter,
    parse_dsml_tool_calls,
    strip_dsml_tool_markup,
)

logger = logging.getLogger(__name__)

# Persist profiles / prompts alongside project root.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_FILE = _PROJECT_ROOT / "zero" / "config.py"
_AI_DATA_DIR = _PROJECT_ROOT / ".zero" / "ai"
_PROFILES_FILE = _AI_DATA_DIR / "profiles.json"
_PROMPTS_FILE = _AI_DATA_DIR / "prompts.json"
_SETTINGS_FILE = _AI_DATA_DIR / "settings.json"
_MEMORY_FILE = _AI_DATA_DIR / "compressed_memory.json"
_MEMORY_ITEMS_FILE = _AI_DATA_DIR / "memory_items.json"
_MEMORY_STATS_FILE = _AI_DATA_DIR / "memory_stats.json"
_LEGACY_PROMPTS_FILE = _PROJECT_ROOT / ".zero_ai_prompts.json"
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

# ── Agent tool definitions (OpenAI function calling) ──────────────

# Maximum tool-calling rounds per user message to prevent infinite loops.
_MAX_TOOL_ROUNDS = 8

# The executable catalogue is discovered from the installed Volatility build at
# runtime.  Only file-writing plugins are denied here; maintaining a static
# allowlist caused valid plugins to be rejected whenever Volatility renamed or
# added a plugin.
AGENT_DENIED_PLUGINS: set[str] = {
    "linux.module_extract.ModuleExtract",
    "linux.pagecache.RecoverFs",
    "windows.dumpfiles.DumpFiles",
    "windows.memmap.Memmap",
    "windows.pedump.PEDump",
}

AGENT_PLUGIN_DESCRIPTIONS: dict[str, str] = {
    # Linux
    "linux.pslist.PsList": "进程列表 (PSList) - 列出活跃进程",
    "linux.psscan.PsScan": "进程扫描 (PSScan) - 扫描内存中的进程结构，可发现已隐藏/退出的进程",
    "linux.pstree.PsTree": "进程树 (PSTree) - 以树状结构展示进程的父子关系",
    "linux.sockscan.Sockscan": "套接字扫描 (Sockscan) - 扫描内存中的网络套接字结构",
    "linux.sockstat.Sockstat": "套接字状态 (Sockstat) - 列出进程套接字和连接状态",
    "linux.lsof.Lsof": "打开文件 (Lsof) - 列出进程打开的文件描述符、管道、套接字等",
    "linux.lsmod.Lsmod": "内核模块 (LSMod) - 列出已加载 of Linux 内核模块",
    "linux.check_afinfo.Check_afinfo": "检查网络协议操作 (Check_afinfo) - 检查网络地址族操作结构体是否被劫持 (Rootkit 检测)",
    "linux.check_creds.Check_creds": "检查进程凭证 (Check_creds) - 检查进程凭证是否被篡改或提权",
    "linux.check_modules.Check_modules": "检查内核模块 (Check_modules) - 检查内核模块列表是否与 sysfs 一致，检测隐藏模块",
    "linux.check_syscall.Check_syscall": "检查系统调用表 (Check_syscall) - 检测系统调用表 (syscall table) 是否被劫持或 Hook",
    "linux.elfs.Elfs": "进程 ELF 映像 (Elfs) - 列出进程内存空间中的 ELF 模块和库文件",
    "linux.malfind.Malfind": "恶意代码检测 (Malfind) - 扫描进程内存中具有可执行权限且未映射到文件的异常内存区域 (注入代码/Shellcode 检测)",
    "linux.proc.Maps": "进程内存映射 (Maps) - 打印进程的内存映射区间（包括权限、偏移、文件路径）",
    "linux.tty_check.tty_check": "检查 TTY 设备 (tty_check) - 检查 TTY 设备的接收/发送函数是否被劫持或 Hook",
    "linux.bash.Bash": "Bash 历史命令 (Bash) - 从内存中提取已打开 Bash 终端的历史命令和输入缓冲区",
    "linux.keyboard_notifiers.Keyboard_notifiers": "键盘通知链 - 检测内核键盘通知链是否被劫持",
    "linux.hidden_modules.Hidden_modules": "检测隐藏内核模块 - 检测通过解链隐藏的内核模块",
    "linux.capabilities.Capabilities": "进程特权集 - 列出进程 POSIX Capabilities",
    "linux.library_list.LibraryList": "进程加载库 (LibraryList) - 列出进程加载的动态链接库 (.so)",
    "linux.envars.Envars": "进程环境变量 (Envars) - 提取进程启动时的环境变量",
    "linux.kthreads.Kthreads": "内核线程 - 列出 Linux 内核线程",
    "linux.pscallstack.PsCallStack": "进程调用栈 - 查看指定进程的调用栈",
    "linux.ptrace.Ptrace": "Ptrace 检查 - 检查进程跟踪关系",
    "linux.check_idt.Check_idt": "IDT 检查 - 检测中断描述符表 Hook",
    "linux.ebpf.EBPF": "eBPF 程序 - 列出内核中的 eBPF 程序",
    "linux.netfilter.Netfilter": "Netfilter 检查 - 检测网络过滤 Hook",
    "linux.modxview.Modxview": "模块交叉视图 - 对比多种模块枚举来源",
    # Windows
    "windows.pslist.PsList": "进程列表 (PSList) - 列出活跃进程",
    "windows.psscan.PsScan": "进程扫描 (PSScan) - 扫描内存中的进程结构 (_EPROCESS)，可发现已隐藏/退出的进程",
    "windows.pstree.PsTree": "进程树 (PSTree) - 以树状结构展示进程的父子关系",
    "windows.netscan.NetScan": "网络扫描 (NetScan) - 扫描内存中的网络连接结构（TCP/UDP 连接和监听端口）",
    "windows.netstat.NetStat": "网络状态 (NetStat) - 扫描网络状态和活跃连接",
    "windows.cmdline.CmdLine": "命令行参数 (CmdLine) - 提取进程启动时的完整命令行参数",
    "windows.dlllist.DllList": "加载的 DLL 列表 (DllList) - 列出每个进程加载 of DLL，包括基址、大小和路径",
    "windows.handles.Handles": "进程句柄表 (Handles) - 列出进程打开的所有句柄（文件、注册表、互斥体、线程、进程等）",
    "windows.modules.Modules": "内核模块 (Modules) - 列出已加载 of Windows 内核驱动程序",
    "windows.modscan.ModScan": "内核模块扫描 (ModScan) - 扫描内存中的内核驱动结构，可发现隐藏驱动",
    "windows.driverscan.DriverScan": "驱动程序扫描 (DriverScan) - 扫描内存中的驱动对象 (_DRIVER_OBJECT)",
    "windows.filescan.FileScan": "文件对象扫描 (FileScan) - 扫描内存中打开的文件对象 (_FILE_OBJECT)，可发现打开的隐藏文件",
    "windows.mutantscan.MutantScan": "互斥体扫描 (MutantScan) - 扫描内存中的互斥体句柄，常用于恶意软件互斥体检测",
    "windows.hivelist.HiveList": "注册表 Hive 列表 (HiveList) - 列出内存中加载 of 注册表 Hive 配置文件及其虚拟地址",
    "windows.printkey.PrintKey": "注册表键值查询 (PrintKey) - 打印特定注册表键下的子键、值和最后修改时间",
    "windows.malfind.Malfind": "恶意代码检测 (Malfind) - 扫描进程内存中具有 PAGE_EXECUTE_READWRITE (RWX) 权限且未映射到文件的异常内存页",
    "windows.vadinfo.VadInfo": "VAD 信息 (VadInfo) - 打印进程虚拟地址描述符 (VAD) 树结构，包含内存页面的分配保护属性",
    "windows.svcscan.SvcScan": "服务扫描 (SvcScan) - 扫描内存中的 Windows 服务列表，展示服务名称、显示名称、状态、类型和关联的 PID",
    "windows.callbacks.Callbacks": "内核回调机制 (Callbacks) - 列出内核注册的各种系统回调（创建进程、创建线程、加载映像等），检测 Rootkit",
    "windows.symlinkscan.SymlinkScan": "符号链接扫描 (SymlinkScan) - 扫描内存中的符号链接对象",
    "windows.sessions.Sessions": "会话扫描 (Sessions) - 扫描活跃 of Logon Sessions",
    "windows.envars.Envars": "进程环境变量 (Envars) - 提取进程环境变量",
    "windows.getsids.GetSIDs": "进程安全标识符 (GetSIDs) - 列出每个进程的所有者、组和特权 SID",
    "windows.privileges.Privs": "进程特权 (Privs) - 列出每个进程启用的特权（如 SeDebugPrivilege）",
    "windows.mftscan.MFTScan": "MFT 记录扫描 (MFTScan) - 在内存中寻找 NTFS MFT (主文件表) 记录，用于恢复文件活动历史",
    "windows.shellbags.ShellBags": "ShellBags 注册表项解析 (ShellBags) - 解析注册表中记录的文件夹浏览历史",
    "windows.ldrmodules.LdrModules": "进程加载模块对比 (LdrModules) - 对比三种不同的进程 PEB 模块链表，检测 DLL 隐藏和解链注入",
    "windows.statistics.Statistics": "系统统计信息 (Statistics) - 报告系统的基本运行和内存统计指标",
    "windows.info.Info": "镜像系统信息 (Info) - 打印当前内存镜像的操作系统版本、内核基址、CPU 数量、时间戳等基础元数据",
    "windows.bigpools.BigPools": "大内存池申请 (BigPools) - 扫描内存中的大内存池分配，检测 Rootkit 分配的隐藏驱动内存",
    "windows.ssdt.SSDT": "系统服务描述表 (SSDT) - 检查 SSDT 的函数入口地址是否被修改/Hook",
    "windows.devicetree.DeviceTree": "设备驱动树 (DeviceTree) - 打印驱动对象及附加在其上的设备链，检查驱动劫持/过滤驱动 Rootkit",
}

# Plugins that MUST be called with a ``pid`` parameter — running them
# without one scans every process and will almost certainly hit the
# stall timeout (120 s).  The agent is blocked from calling these
# bare; it must first identify suspicious PIDs from pslist / psscan
# and then pass the pid.
AGENT_HEAVY_PLUGINS: set[str] = {
    "linux.malfind.Malfind",
    "linux.malware.malfind.Malfind",
    "windows.malfind.Malfind",
    "windows.malware.malfind.Malfind",
    "windows.vadinfo.VadInfo",
    "linux.proc.Maps",
    "linux.vmaregexscan.VmaRegExScan",
    "linux.vmayarascan.VmaYaraScan",
    "windows.vadregexscan.VadRegExScan",
    "windows.vadyarascan.VadYaraScan",
}

_AGENT_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "run_plugin",
            "description": (
                "运行一个 Volatility 3 插件获取内存取证数据。"
                "插件名称会随 Volatility 版本和操作系统变化；必须先调用 list_plugins，"
                "再从其结果复制精确的 plugin_name，不要自行修改大小写、下划线或类名。"
                "可选参数 pid 用于指定进程 ID。"
                "重要：malfind、vadinfo、proc.Maps、VmaRegExScan 等内存扫描类插件必须传 pid 参数，"
                "否则会全量扫描所有进程导致超时卡死。"
                "正确流程：先跑 pslist/psscan 找可疑 PID → 再用 malfind/vadinfo pid=可疑PID 深入分析。"
                "如果返回 status=pid_required，系统已经自动枚举 PID；"
                "你必须自行选择 pid_candidates 并立即重试。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plugin_name": {
                        "type": "string",
                        "description": "插件短名称，如 pslist.PsList, netscan.NetScan, malfind.Malfind",
                    },
                    "pid": {
                        "type": "integer",
                        "description": "可选，目标进程 PID",
                    },
                    "pids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "可选，批量目标进程 PID；内存扫描插件可一次传多个 PID",
                    },
                    "args": {
                        "type": "object",
                        "description": (
                            "可选插件参数。仅支持安全白名单字段：pid, offset, base, key, name, "
                            "ignore-case, physical, kernel_module, regex。不要在这里传 dump=True；"
                            "dump 任务使用 dump_process 或 dump_pe。"
                        ),
                    },
                },
                "required": ["plugin_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_plugins",
            "description": (
                "列出当前镜像可用的 Volatility 3 插件，按类别分组返回。"
                "系统已知道镜像的操作系统类型，你无需指定 os_family。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dump_process",
            "description": (
                "自动按进程名或 PID dump Linux/Windows 目标进程。"
                "当用户要求 dump 某个进程名或 PID 的内存时使用这个工具；"
                "工具会自动枚举进程并匹配 PID；Windows 使用 Memmap 导出进程内存，"
                "Linux 使用 PsList 导出进程主 ELF 可执行映像。"
                "不要让用户手动运行底层 dump 插件。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "process_name": {
                        "type": "string",
                        "description": "目标进程名或关键字，如 go-winpmem_amd",
                    },
                    "pid": {
                        "type": "integer",
                        "description": "可选，已知目标 PID；提供后会直接 dump 该 PID",
                    },
                    "max_matches": {
                        "type": "integer",
                        "description": "进程名匹配到多个 PID 时最多 dump 几个，默认 3",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dump_pe",
            "description": (
                "自动使用 windows.pedump.PEDump dump 指定进程或模块中的 PE 文件。"
                "当用户明确要求 pedump、dump PE、dump 某进程 exe 或 DLL 时使用。"
                "如果只有进程名，工具会先用 pslist/psscan 找 PID，再用 dlllist 找模块 base，最后调用 PEDump。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "process_name": {
                        "type": "string",
                        "description": "目标进程名或关键字，如 go-winpmem_amd",
                    },
                    "pid": {
                        "type": "integer",
                        "description": "可选，目标进程 PID",
                    },
                    "module_name": {
                        "type": "string",
                        "description": "可选，目标 PE 模块名；未提供时默认使用 process_name",
                    },
                    "base": {
                        "type": "string",
                        "description": "可选，PE 基址，如 0x140000000；提供后直接调用 PEDump",
                    },
                    "kernel_module": {
                        "type": "boolean",
                        "description": "是否 dump 内核模块；默认 false",
                    },
                    "max_matches": {
                        "type": "integer",
                        "description": "最多 dump 几个匹配项，默认 3",
                    },
                },
                "required": [],
            },
        },
    },
]


def _agent_tools_for_engine(engine_id: str) -> list[dict]:
    """Do not expose Volatility-only extraction tools to other engines."""
    if engine_id == "vol3":
        return _AGENT_TOOLS
    allowed = {"run_plugin", "list_plugins"}
    return [
        tool for tool in _AGENT_TOOLS
        if tool.get("function", {}).get("name") in allowed
    ]

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
5. 在智能体模式下，如果需要更多证据，应直接调用可用 Volatility 插件获取数据；不要把可执行的插件调用写成“下一步验证路径”让用户手动执行。
6. 如需筛选结果，使用 ```filter 代码块输出 Zero 过滤规则。
7. 保持智能体式推进：先判断当前数据能回答什么，能继续查就直接调用插件继续查；最终只总结已执行插件的证据、结论和仍无法确认的点。
8. 需要长分析时优先输出证据摘要和决策点，避免复述完整表格。

推荐输出结构：
## 结论概览
## 关键证据
## 可疑项分级
## 无法确认的点

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
    """Write non-secret AI_PROFILES metadata back to config.py."""
    try:
        content = _CONFIG_FILE.read_text("utf-8")
        # Build new block
        if not profiles:
            new_block = "AI_PROFILES = []\n"
        else:
            lines = ["AI_PROFILES = ["]
            for p in profiles:
                entry = {
                    k: p.get(k, "")
                    for k in (
                        "id", "name", "base_url", "model", "protocol",
                        "context_window", "reasoning_level", "capabilities",
                    )
                }
                entry["api_key"] = ""
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
    normalized = []
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        item = {
            key: profile.get(key, "")
            for key in ("id", "name", "base_url", "api_key", "model")
        }
        for key in ("protocol", "context_window", "reasoning_level", "capabilities"):
            if key in profile and profile.get(key) not in (None, ""):
                item[key] = profile[key]
        normalized.append(item)
    return normalized


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
    """Write active profile's non-secret settings to config.py."""
    if not profile:
        return
    try:
        content = _CONFIG_FILE.read_text("utf-8")
        content = _update_config_value(content, "AI_BASE_URL", profile.get("base_url", ""))
        content = _update_config_value(content, "AI_API_KEY", "")
        content = _update_config_value(content, "AI_MODEL", profile.get("model", ""))
        _CONFIG_FILE.write_text(content, "utf-8")
        # Sync in-memory module
        config.AI_BASE_URL = profile.get("base_url", "")
        config.AI_API_KEY = ""
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
    current_len = sum(len(line) for line in lines) + (len(lines) - 1 if lines else 0)
    shown_rows = 0
    for row in truncated:
        cells = [str(c).replace("|", "\\|").replace("\n", " ")[:180] for c in row]
        line = "| " + " | ".join(cells) + " |"
        if current_len + len(line) + 1 > max_chars:
            break
        lines.append(line)
        current_len += len(line) + 1
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
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._openai_clients: dict[tuple[str, str], AsyncOpenAI] = {}
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

    def clear_all_memory(self) -> int:
        """Clear both compressed memory and structured memory items.

        Returns the number of structured items that were removed.
        """
        self._compressed_memory = ""
        _save_compressed_memory("")
        removed = self._memory_store.clear_all()
        return removed

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
                client = self._get_openai_client(base_url, api_key)
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

    def _get_openai_client(self, base_url: str, api_key: str) -> AsyncOpenAI:
        key = (base_url, api_key)
        if key not in self._openai_clients:
            self._openai_clients[key] = AsyncOpenAI(base_url=base_url, api_key=api_key)
        return self._openai_clients[key]

    def _resolve_system_prompt(self) -> str:
        all_prompts = self.get_all_prompts()
        for p in all_prompts:
            if p["id"] == self._active_prompt_id:
                return p["content"]
        return _BUILTIN_PROMPTS[0]["content"]

    # ── Chat ──────────────────────────────────────────────────────

    def _build_messages(
        self,
        user_message: str,
        plugin_context: Optional[dict],
        os_family: str = "linux",
        conversation_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Build the initial message list for a chat turn (shared by every tool-call round)."""
        family_display = "Windows" if os_family == "windows" else "Linux"
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._resolve_system_prompt()},
            {
                "role": "system",
                "content": (
                    f"Zero 运行约束：你是内存取证智能体，当前加载的是 {family_display} 内存镜像。"
                    "回答必须优先使用当前内存镜像插件输出，"
                    "把事实、推断和无法确认的点分开；如果当前数据不足且工具可用，直接调用 Volatility 插件补证。"
                    "不要输出“下一步验证路径”或要求用户手动运行插件，除非插件被禁止、缺参数或执行失败。"
                    "如果某个插件因参数不足失败，改用带参数的调用重试；如果因 layer/symbol table 失败，停止继续跑同平台同类插件，直接说明镜像/符号层问题。"
                    "当用户要求 dump 某个进程名或 PID 的进程内存时，优先调用 dump_process。"
                    "当用户要求 pedump、dump PE、dump exe 或 dump DLL 时，优先调用 dump_pe。"
                    "除非用户要求教学解释，否则不要输出通用安全科普或与证据无关的长篇背景。"
                    "插件命名以 list_plugins 对当前运行环境返回的 plugin_name 为准；"
                    "调用 run_plugin 时必须原样复制该名称，不要凭记忆生成名称。"
                    "如果工具返回 status=pid_required，必须从 pid_candidates 中自行选择相关或可疑 PID，"
                    "立即使用 pid 或 pids 重试原插件，不要停下，也不要要求用户代为选择。"
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
            plugin_context_copy = dict(plugin_context)
            plugin_context_copy["_max_rows"] = self._resolve_context_max_rows()
            plugin_context_copy["_max_chars"] = self._resolve_context_max_chars()
            context_text = _format_plugin_context(plugin_context_copy)
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

        # Load history from the persistent store if available
        raw_history = []
        if conversation_id:
            from web.backend.services import conversation_store as conv_store
            try:
                raw_history = conv_store.get_messages(conversation_id)
            except Exception as e:
                logger.warning("Failed to lazily load conversation history: %s", e)

        if not raw_history:
            raw_history = self.get_history(conversation_id)

        # Slice history by turns (last N user queries)
        max_history = self._ai_max_history
        sliced_history = []
        if raw_history:
            user_indices = [i for i, m in enumerate(raw_history) if m.get("role") == "user"]
            if len(user_indices) > max_history:
                start_idx = user_indices[-max_history]
                sliced_history = raw_history[start_idx:]
            else:
                sliced_history = raw_history

        # Convert sliced history to standard OpenAI API message format
        for msg in sliced_history:
            role = msg.get("role")
            if role == "user":
                messages.append({"role": "user", "content": msg.get("content") or ""})
            elif role == "assistant":
                messages.append({
                    "role": "assistant",
                    "content": strip_dsml_tool_markup(msg.get("content") or ""),
                })
            elif role == "tool":
                # Convert the custom tool/result format back to standard assistant/tool API format
                tool_call_id = msg.get("tool_call_id") or "call_unknown"
                tool_name = msg.get("toolName") or "run_plugin"
                tool_args = msg.get("toolArgs") or {}

                # 1. Append assistant tool call request
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(tool_args, ensure_ascii=False) if isinstance(tool_args, dict) else str(tool_args)
                        }
                    }]
                })

                # 2. Append tool execution result
                if msg.get("toolError"):
                    content = json.dumps({"error": msg["toolError"]}, ensure_ascii=False)
                else:
                    content = json.dumps({"summary": msg.get("toolSummary", "")}, ensure_ascii=False)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": content
                })

        messages.append({"role": "user", "content": user_message})
        return messages

    @staticmethod
    def _accumulate_tool_calls(delta) -> dict[int, dict[str, Any]]:
        """Accumulate streaming tool-call deltas.

        Returns a dict keyed by tool-call index so callers can merge across chunks.
        """
        acc: dict[int, dict[str, Any]] = {}
        if not delta.tool_calls:
            return acc
        for tc_delta in delta.tool_calls:
            idx = tc_delta.index
            if idx not in acc:
                acc[idx] = {"id": "", "function_name": "", "function_arguments": ""}
            entry = acc[idx]
            if tc_delta.id:
                entry["id"] = tc_delta.id
            if tc_delta.function:
                if tc_delta.function.name:
                    entry["function_name"] += tc_delta.function.name
                if tc_delta.function.arguments:
                    entry["function_arguments"] += tc_delta.function.arguments
        return acc

    @staticmethod
    def _summarize_tool_result(result: Any) -> str:
        if not isinstance(result, dict):
            return ""
        lines = []
        summary = str(result.get("summary") or "").strip()
        if summary:
            lines.append(summary)
        files = result.get("files")
        if isinstance(files, list) and files:
            lines.append("输出文件:")
            for item in files[:10]:
                if isinstance(item, dict):
                    path = item.get("path", "")
                    size = item.get("size_bytes")
                    if path:
                        suffix = f" ({size} bytes)" if size is not None else ""
                        lines.append(f"- {path}{suffix}")
        if result.get("truncated"):
            lines.append("结果已截断，仅返回前 100 行。")
        return "\n".join(lines)

    async def chat_stream(
        self,
        user_message: str,
        plugin_context: Optional[dict] = None,
        tool_executor: Optional[Callable[..., Any]] = None,
        engine_id: str = "vol3",
        os_family: str = "linux",
        conversation_id: Optional[str] = None,
    ) -> AsyncGenerator[dict, None]:
        """Yield structured streaming events from the AI model.

        When *tool_executor* is provided, the model is given access to
        ``run_plugin`` / ``list_plugins`` tools and may autonomously
        execute Volatility 3 plugins in a loop (up to ``_MAX_TOOL_ROUNDS``
        iterations).
        """
        base_url, api_key, model = self._resolve_config()
        client = self._get_openai_client(base_url, api_key)
        max_tokens = self._resolve_max_tokens(model)
        temperature = self._ai_temperature
        agent_mode = tool_executor is not None

        # The initial message list (without the user message appended to history yet).
        base_messages = self._build_messages(
            user_message, plugin_context, os_family=os_family, conversation_id=conversation_id
        )
        messages: list[dict[str, Any]] = list(base_messages)

        # ── Tool-calling loop ──────────────────────────────────────
        tool_round = 0
        all_tool_calls_made: list[dict[str, Any]] = []
        pending_pid_retries: list[dict[str, Any]] = []

        try:
            while tool_round < (_MAX_TOOL_ROUNDS if agent_mode else 1):
                tool_round += 1

                req: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "stream": True,
                }
                if max_tokens is not None:
                    req["max_tokens"] = max_tokens
                if agent_mode:
                    req["tools"] = _agent_tools_for_engine(engine_id)
                    req["tool_choice"] = "auto"

                stream = await client.chat.completions.create(
                    model=model,
                    **{k: v for k, v in req.items() if k != "model"},
                )

                content_parts: list[str] = []
                raw_content_parts: list[str] = []
                dsml_filter = DsmlStreamFilter()
                tool_call_acc: dict[int, dict[str, Any]] = {}

                async for chunk in stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta is None:
                        continue
                    if delta.content:
                        raw_content_parts.append(delta.content)
                        visible_content = dsml_filter.feed(delta.content)
                        if visible_content:
                            content_parts.append(visible_content)
                            yield {"type": "chunk", "content": visible_content}
                    if delta.tool_calls:
                        for idx_str, tc_data in self._accumulate_tool_calls(delta).items():
                            existing = tool_call_acc.get(idx_str)
                            if existing is None:
                                tool_call_acc[idx_str] = tc_data
                            else:
                                existing["id"] = existing["id"] or tc_data["id"]
                                existing["function_name"] += tc_data["function_name"]
                                existing["function_arguments"] += tc_data["function_arguments"]

                visible_tail = dsml_filter.finish()
                if visible_tail:
                    content_parts.append(visible_tail)
                    yield {"type": "chunk", "content": visible_tail}

                # Compatibility fallback: some OpenAI-compatible providers put
                # DSML calls in delta.content rather than delta.tool_calls.
                if not tool_call_acc:
                    dsml_calls = parse_dsml_tool_calls("".join(raw_content_parts))
                    for index, dsml_call in enumerate(dsml_calls):
                        tool_call_acc[index] = {
                            "id": (
                                f"call_dsml_{tool_round}_{index}_"
                                f"{time.monotonic_ns()}"
                            ),
                            "function_name": dsml_call["name"],
                            "function_arguments": json.dumps(
                                dsml_call["arguments"],
                                ensure_ascii=False,
                            ),
                        }
                    if dsml_calls:
                        logger.info(
                            "Recovered %d textual DSML tool call(s) from provider response",
                            len(dsml_calls),
                        )

                # ── No tool calls → final text response ────────────
                if not tool_call_acc:
                    assistant_text = "".join(content_parts)
                    if pending_pid_retries and agent_mode:
                        if tool_round < _MAX_TOOL_ROUNDS:
                            if assistant_text.strip():
                                messages.append({
                                    "role": "assistant",
                                    "content": assistant_text,
                                })
                            messages.append({
                                "role": "system",
                                "content": (
                                    "以下按 PID 的原始扫描仍未执行："
                                    f"{json.dumps(pending_pid_retries, ensure_ascii=False)}。"
                                    "你不能以文字说明代替重试。请立即从上一条工具结果的 "
                                    "pid_candidates 中自行选择相关 PID，使用 pid 或 pids "
                                    "逐项再次调用 run_plugin；不要遗漏、停止或询问用户。"
                                ),
                            })
                            continue
                        break
                    if not assistant_text.strip() and agent_mode and all_tool_calls_made:
                        if tool_round < _MAX_TOOL_ROUNDS:
                            messages.append({
                                "role": "system",
                                "content": (
                                    "上一轮工具返回后你没有输出内容。不得静默停止。"
                                    "如果结果包含 status=pid_required，请从 pid_candidates "
                                    "中自行选择相关 PID，并立即用 pid 或 pids 重试原插件；"
                                    "如果工具报错，请修正插件名称或参数后重试；"
                                    "如果证据已经足够，则立即给出完整取证结论。"
                                ),
                            })
                            continue
                        break
                    if not assistant_text.strip():
                        assistant_text = "模型未生成有效回复，请重试或检查当前模型的工具调用兼容性。"
                        yield {"type": "chunk", "content": assistant_text}
                    self.append_history(conversation_id, {"role": "user", "content": user_message})
                    self.append_history(conversation_id, {"role": "assistant", "content": assistant_text})
                    if self._ai_memory_enabled:
                        yield {"type": "memory_status", "status": "queued"}
                    self._schedule_memory_update(
                        base_url=base_url, api_key=api_key, model=model,
                        user_message=user_message, assistant_message=assistant_text,
                        plugin_context=plugin_context,
                    )
                    return

                # ── Tool calls received → execute them ────────────
                assistant_tool_calls = []
                for idx in sorted(tool_call_acc.keys()):
                    tc = tool_call_acc[idx]
                    assistant_tool_calls.append({
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["function_name"],
                            "arguments": tc["function_arguments"],
                        },
                    })

                # Append the assistant message carrying tool_calls.
                messages.append({
                    "role": "assistant",
                    "content": "".join(content_parts) or None,
                    "tool_calls": assistant_tool_calls,
                })
                all_tool_calls_made.extend(assistant_tool_calls)

                # Execute each tool and feed results back.
                for tc in assistant_tool_calls:
                    tool_name = tc["function"]["name"]
                    try:
                        tool_args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError:
                        tool_args = {}

                    yield {
                        "type": "tool_call",
                        "tool_call_id": tc["id"],
                        "tool_name": tool_name,
                        "arguments": tool_args,
                    }

                    try:
                        result = await tool_executor(tool_name, tool_args, engine_id)
                        if isinstance(result, dict):
                            if result.get("status") == "pid_required":
                                pending_pid_retries.append({
                                    "plugin": result.get("plugin"),
                                    "retry_arguments": result.get("retry_arguments") or {},
                                })
                            elif pending_pid_retries and result.get("plugin"):
                                actual_args = result.get("arguments") or {}
                                matched_index = None
                                for index, pending in enumerate(pending_pid_retries):
                                    expected_args = (
                                        pending.get("retry_arguments", {}).get("args", {})
                                    )
                                    same_arguments = all(
                                        actual_args.get(key) == value
                                        for key, value in expected_args.items()
                                    )
                                    if (
                                        result.get("plugin") == pending.get("plugin")
                                        and same_arguments
                                    ):
                                        matched_index = index
                                        break
                                if matched_index is not None:
                                    pending_pid_retries.pop(matched_index)
                        result_text = json.dumps(result, ensure_ascii=False)
                        yield {
                            "type": "tool_result",
                            "tool_call_id": tc["id"],
                            "tool_name": tool_name,
                            "ok": True,
                            "summary": self._summarize_tool_result(result),
                        }
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result_text,
                        })
                    except Exception as exc:
                        error_text = f"工具执行失败: {exc}"
                        logger.warning("Tool execution failed: %s %s: %s", tool_name, tool_args, exc)
                        yield {
                            "type": "tool_result",
                            "tool_call_id": tc["id"],
                            "tool_name": tool_name,
                            "ok": False,
                            "error": error_text,
                        }
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps({"error": error_text}, ensure_ascii=False),
                        })

            # ── Ran out of tool rounds → force a final summary ──
            # The AI kept requesting tools without producing a final
            # answer.  Make one last non-tool call so it can summarise
            # everything it has collected.
            if all_tool_calls_made:
                # Append a system instruction asking for a final summary.
                messages.append({
                    "role": "system",
                    "content": (
                        "你已达到最大工具调用次数。请基于以上所有已获取的插件数据，"
                        "立即输出完整的取证分析结论，不要再调用任何工具。"
                        "按以下结构输出：结论概览 → 已执行插件 → 关键证据 → 可疑项分级 → 无法确认的点。"
                    ),
                })
                final_req: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "stream": True,
                }
                if max_tokens is not None:
                    final_req["max_tokens"] = max_tokens

                final_stream = await client.chat.completions.create(
                    model=model,
                    **{k: v for k, v in final_req.items() if k != "model"},
                )
                final_parts: list[str] = []
                final_dsml_filter = DsmlStreamFilter()
                async for chunk in final_stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta and delta.content:
                        visible_content = final_dsml_filter.feed(delta.content)
                        if visible_content:
                            final_parts.append(visible_content)
                            yield {"type": "chunk", "content": visible_content}

                final_tail = final_dsml_filter.finish()
                if final_tail:
                    final_parts.append(final_tail)
                    yield {"type": "chunk", "content": final_tail}

                assistant_text = "".join(final_parts)
                if not assistant_text.strip():
                    assistant_text = "分析完成，但模型未生成结论。请重新提问或指定更具体的问题。"
                    yield {"type": "chunk", "content": assistant_text}
                self.append_history(conversation_id, {"role": "user", "content": user_message})
                self.append_history(conversation_id, {"role": "assistant", "content": assistant_text})
                if self._ai_memory_enabled:
                    yield {"type": "memory_status", "status": "queued"}
                self._schedule_memory_update(
                    base_url=base_url, api_key=api_key, model=model,
                    user_message=user_message, assistant_message=assistant_text,
                    plugin_context=plugin_context,
                )

        except Exception as e:
            logger.error("AI streaming error: %s", e, exc_info=True)
            raise

    def _schedule_memory_update(
        self,
        base_url: str,
        api_key: str,
        model: str,
        user_message: str,
        assistant_message: str,
        plugin_context: Optional[dict],
    ) -> None:
        """Kick off a background memory-compression task (non-blocking)."""
        if not self._ai_memory_enabled:
            return
        self._memory_status = "queued"
        # Cancel any in-flight memory update before starting a new one.
        if self._memory_update_task and not self._memory_update_task.done():
            self._memory_update_task.cancel()
        self._memory_update_task = asyncio.create_task(
            self._run_memory_update_async(
                base_url=base_url,
                api_key=api_key,
                model=model,
                user_message=user_message,
                assistant_message=assistant_message,
                plugin_context=plugin_context,
            )
        )

    def get_history(self, conversation_id: Optional[str] = None) -> list[dict[str, Any]]:
        conv_key = conversation_id or ""
        if conv_key not in self._history:
            self._history[conv_key] = []
        return self._history[conv_key]

    def clear_history(self, conversation_id: Optional[str] = None) -> None:
        conv_key = conversation_id or ""
        if conv_key in self._history:
            self._history[conv_key].clear()
        else:
            self._history[conv_key] = []
        if conv_key:
            from web.backend.services import conversation_store as conv_store
            try:
                conv_store.replace_messages(conv_key, [])
            except Exception as e:
                logger.warning("Failed to clear conversation history in store: %s", e)

    def append_history(self, conversation_id: Optional[str], message: dict[str, Any]) -> None:
        conv_key = conversation_id or ""
        if conv_key not in self._history:
            self._history[conv_key] = []
        self._history[conv_key].append(message)
        # Prune to prevent memory leaks (P2 Issue #8)
        limit = self._ai_max_history * 4
        if len(self._history[conv_key]) > limit:
            self._history[conv_key] = self._history[conv_key][-limit:]

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
