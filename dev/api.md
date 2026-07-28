# Zero API

默认服务地址：

```text
http://localhost:8000
```

所有接口返回 JSON。交互式文档可访问 `/docs`。

## Health

### GET `/api/health`

轻量健康检查，不初始化 Volatility 引擎。

Response:

```json
{ "ok": true }
```

## Engines

### GET `/api/engines`

返回可用引擎。Zero 当前只保留 `vol3`。

### GET `/api/engines/{engine_id}/settings`

返回引擎状态设置。当前用于兼容前端状态展示。

## Images

### POST `/api/image/load`

加载内存镜像。

该接口只负责加载镜像并立即返回。Web 前端在成功响应后先记录“镜像已加载”，再调用下方的流式符号准备接口，因此镜像加载和符号下载具有独立的时间和进度状态。

Request:

```json
{
  "path": "/path/to/memory.raw",
  "engine": "vol3"
}
```

Response：

```json
{
  "ok": true,
  "path": "/path/to/memory.raw",
  "engine": "vol3"
}
```

### POST `/api/image/symbols/auto`

在镜像加载成功后，流式扫描 `Linux version` banner、检查本地 ISF 并匹配远程候选。默认只返回 `available` 候选，不下载。客户确认后传 `download=true`、`paths`、`repo`，可同时传 `use_gh_proxy=true`。响应类型为 `application/x-ndjson`：

```json
{"type":"progress","data":{"stage":"downloading","percent":42.5,"downloaded_bytes":4456448,"total_bytes":10485760,"completed_files":0,"total_files":1}}
{"type":"result","data":{"enabled":true,"status":"downloaded","kernel":{"release":"5.15.0-91-generic"},"downloaded":[{"path":"Ubuntu/...json.xz"}]}}
```

`stage` 依次为 `detecting`、`matching`、`downloading`。远端未提供 `Content-Length` 时，`percent` 和 `total_bytes` 为 `null`，前端显示不定进度圆环。

常见结果 `status`：`available`、`downloaded`、`present`、`partial`、`not_detected`、`scan_limit_reached`、`no_match`、`ambiguous`、`remote_unavailable`、`scan_failed`、`download_failed`。可通过 `zero/config.py` 的 `AUTO_DOWNLOAD_LINUX_SYMBOLS_ON_LOAD` 关闭加载后检查。

### GET `/api/image/status?engine=vol3`

返回当前镜像加载状态。

### GET `/api/image/list?refresh=false`

列出项目 `dumps/` 目录下可识别的镜像文件。

支持扩展名：

```text
.raw .mem .dmp .vmem .img .bin .lime .elf .core .crash .hpak .aff4
```

## Runtime Settings

### GET `/api/settings`

返回 WebUI 可修改的白名单配置、分类和字段元数据：

```json
{
  "settings": {
    "plugin_timeout_seconds": 600,
    "plugin_stall_timeout_seconds": 120,
    "worker_heartbeat_seconds": 15,
    "auto_download_linux_symbols": true
  },
  "categories": [
    {
      "id": "runtime",
      "label": "运行与超时",
      "fields": []
    }
  ],
  "storage": ".zero/runtime_settings.json",
  "effective": "immediate"
}
```

### PUT `/api/settings`

部分或完整更新运行设置。值会进行类型、范围和关联校验，成功后立即更新已初始化的 Vol3 引擎，并持久化到 `.zero/runtime_settings.json`。

```json
{
  "settings": {
    "plugin_timeout_seconds": 1800,
    "plugin_stall_timeout_seconds": 300,
    "worker_heartbeat_seconds": 15
  }
}
```

工作进程心跳必须小于非零的无响应超时；符号索引陈旧可用时间不能短于刷新间隔。未知字段返回 `400`，避免通过该接口修改敏感或任意配置。

## Plugins

### GET `/api/plugins/{os_family}?engine=vol3`

列出插件分类。

`os_family`:

```text
linux
windows
```

### GET `/api/plugin-args/{plugin_name}?engine=vol3`

返回 Volatility 3 插件参数元数据，用于前端判断是否需要弹出参数输入框。

### GET `/api/plugin-docs/{plugin_name}?engine=vol3`

返回插件说明，取自 Volatility 3 插件类的 docstring 与各 requirement 描述，
供参数弹窗的帮助面板展示。插件不存在或没有 docstring 时 `doc` 为空对象。

```json
{
  "plugin": "pslist",
  "engine": "vol3",
  "doc": {
    "notes": "Volatility 3 插件: linux.pslist.PsList",
    "purpose": "Lists the processes present in a particular linux memory image.",
    "key_params": { "pid": "Filter on specific process IDs" }
  }
}
```

### POST `/api/plugins/reload?engine=vol3`

重新扫描插件目录。

## WebSocket Plugin Run

### WS `/ws/plugin`

运行插件并实时返回进度。

Client message:

```json
{
  "action": "run",
  "plugin": "linux.pslist.PsList",
  "engine": "vol3"
}
```

可以附带插件参数，例如：

```json
{
  "action": "run",
  "plugin": "windows.dumpfiles.DumpFiles",
  "engine": "vol3",
  "pid": 1234,
  "dump_dir": "saved_results/vol3/dumps"
}
```

可选缓存控制：`force: true` 或 `use_cache: false` 表示忽略结果缓存强制重跑。

若后端配置了 `API_TOKEN`，HTTP 请求需携带：

```text
Authorization: Bearer <token>
```

或：

```text
X-API-Token: <token>
```

WebSocket 连接：

```text
/ws/plugin?token=<token>
```

Cancel:

```json
{
  "action": "cancel",
  "engine": "vol3"
}
```

Server event types:

```text
progress
result
error
status
```

`result` 仅包含元数据（不含全量 rows），完整数据请用 `GET /api/results` 分页拉取：

```json
{
  "type": "result",
  "data": {
    "plugin": "linux.pslist.PsList",
    "total": 128,
    "columns": ["PID", "PPID", "COMM"]
  },
  "engine": "vol3"
}
```

## Results

### GET `/api/results`

Query:

```text
filter=<filter-expression>
sort=<column>
desc=false
page=1
page_size=200
engine=vol3
```

返回当前插件结果，支持分页、排序和过滤。

### POST `/api/export`

导出当前结果。

Request:

```json
{
  "format": "csv",
  "engine": "vol3"
}
```

`format` 支持：

```text
csv
json
txt
```

### DELETE `/api/cache`

清理结果缓存。

Request:

```json
{
  "plugin": null,
  "engine": "vol3"
}
```

### GET `/api/cache/stats?engine=vol3`

返回缓存统计。`memory_entries` / `memory_entries_max` 为常驻内存结果集的当前条数与
LRU 上限（`config.RESULTS_MEMORY_CACHE_MAX`）；被淘汰的结果下次访问从磁盘缓存复原。

### DELETE `/api/plugin/cancel?engine=vol3`

取消当前运行插件。

## AI

AI 接口位于 `/api/ai/*`，用于配置 OpenAI-compatible 模型、提示词、会话和压缩记忆。前端默认通过这些接口管理取证分析助手。

主要能力：

- 获取/保存 AI 配置
- 管理模型配置和提示词
- 流式对话
- 清理历史和压缩记忆
- 会话列表、加载、重命名、删除

## Symbols

### GET `/api/symbols/repos`

列出可切换的远程符号表仓库索引（默认：`Abyss-W4tcher/volatility3-symbols`、`Sunmedalia/volatility3-symbols`）。

### GET `/api/symbols/remote`

浏览远程符号表仓库索引。

索引会落盘到 `.zero/symbols/remote_index/`，默认 6 小时内不重复请求 GitHub；刷新失败时最长可继续使用 7 天内的陈旧缓存。

未配置 token 时 GitHub REST 匿名配额约 **60 次/小时**；配置 `SYMBOL_GITHUB_TOKEN` 或环境变量 `GITHUB_TOKEN` / `ZERO_GITHUB_TOKEN` 后约 **5000 次/小时**。符号文件下载走 `raw.githubusercontent.com`，不占用列表 API 配额。

下载页可选用 `https://gh-proxy.com` 代理符号文件；该选项不会代理上述 GitHub 索引 API 请求。

Query:

```text
query=
os=            # linux | mac | windows | 空=全部
page=1
page_size=100
repo=          # owner/name，默认首个配置仓库
force_refresh= # true 时绕过软 TTL 强制拉 GitHub
```

### GET `/api/symbols/local`

列出本地 `symbols/` 目录。

### POST `/api/symbols/download`

从指定远程仓库下载符号表到本地。

Request:

```json
{
  "paths": [
    "Ubuntu/ubuntu-6.8.0-100-generic.json.xz"
  ],
  "repo": "Abyss-W4tcher/volatility3-symbols",
  "use_gh_proxy": true
}
```

`use_gh_proxy` 默认为 `false`。设为 `true` 时，服务使用固定格式 `https://gh-proxy.com/https://raw.githubusercontent.com/...` 下载选中的符号文件。

## AI Agent Runtime v1

所有运行事件均为可重连 SSE。事件 envelope：

```json
{
  "version": 1,
  "seq": 42,
  "run_id": "run_...",
  "conversation_id": "abc123",
  "timestamp": "2026-07-28T05:00:00Z",
  "type": "text_delta",
  "data": {"text": "证据"}
}
```

客户端必须按 `seq` 幂等归并，断线后使用最后一个已处理序号续接。

### POST `/api/ai/conversations/{id}/runs`

创建后台运行，立即返回 `run_id`。默认预算为 12 turns、20 次工具调用、1800 秒。

```json
{
  "message": "排查当前镜像中的可疑进程",
  "engine_id": "vol3",
  "mode": "agent",
  "max_turns": 12,
  "max_tool_calls": 20,
  "max_seconds": 1800
}
```

### GET `/api/ai/runs/{id}/events?after_seq=N`

读取历史事件并继续等待新事件。事件类型包括 `run_start`、`turn_start`、
`context`、`text_delta`、`thinking_summary_delta`、`tool_start`、
`tool_progress`、`tool_end`、`retry`、`compaction`、`usage`、
`budget_exhausted`、`turn_end` 和 `run_end`。

### GET `/api/ai/runs/{id}`

查询运行状态、最新序号和 turn/工具/时间预算。

### POST `/api/ai/runs/{id}/cancel`

先中止服务端 SDK 请求并调用对应 engine 的 `cancel_plugin`，再由客户端关闭 SSE。

### POST `/api/ai/conversations/{id}/compact`

手动创建 checkpoint。原始 typed JSONL 历史不会删除。

### GET `/api/ai/conversations/{id}/context`

返回上下文窗口、reserve、阈值、估算 token、占用率和 checkpoint 数量。

### GET `/api/ai/tool-results/{result_id}`

按 `conversation_id` 查询不透明结果句柄。支持 `filter`、`sort_column`、
`sort_desc`、逗号分隔的 `columns`、`page` 和最多 200 的 `page_size`。

### POST `/api/ai/profiles/test`

使用指定协议执行最小流式连接测试。Profile 支持
`openai_responses`、`openai_chat`、`anthropic_messages`、`google_genai`，
以及 `context_window`、`reasoning_level=off|low|medium|high` 和能力覆盖项。

`POST /api/ai/chat` 保留兼容，但内部创建同一种后台 run。
