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

Request:

```json
{
  "path": "/path/to/memory.raw",
  "engine": "vol3"
}
```

### GET `/api/image/status?engine=vol3`

返回当前镜像加载状态。

### GET `/api/image/list?refresh=false`

列出项目 `dumps/` 目录下可识别的镜像文件。

支持扩展名：

```text
.raw .mem .dmp .vmem .img .bin .lime .elf .core .crash .hpak .aff4
```

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
  "repo": "Abyss-W4tcher/volatility3-symbols"
}
```
