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

返回缓存统计。

### DELETE `/api/plugin/cancel?engine=vol3`

取消当前运行插件。

## AI

AI 接口位于 `/api/ai/*`，用于配置 OpenAI-compatible 模型、提示词、会话和压缩记忆。前端默认通过这些接口管理取证分析助手。

主要能力：

- 获取/保存 AI 配置
- 管理模型配置和提示词
- 流式对话（智能体模式下可调用 `run_plugin` / `list_plugins` / `dump_process` / `dump_pe` 工具）
- 清理历史和压缩记忆
- 会话列表、加载、重命名、删除

注意：

- 会话历史单一数据源为 `conversation_store`（`.zero/ai/conversations/`）。`GET /api/ai/history` 已移除，加载历史请用 `GET /api/ai/conversations/{id}`；`DELETE /api/ai/history` 保留用于清空当前会话消息。
- 运行时 profile / 设置只写 `.zero/ai/`（`profiles.json`、`settings.json`），不会回写 `config.py`。
- 压缩记忆按 debounce 触发（每 3 轮对话或 60 秒），不是每条消息都压缩。

## Symbols

### GET `/api/symbols/remote`

浏览远程符号表仓库。

Query:

```text
query=
os=
page=1
page_size=100
```

### GET `/api/symbols/local`

列出本地 `symbols/` 目录。

### POST `/api/symbols/download`

下载远程符号表到本地。

Request:

```json
{
  "paths": [
    "ubuntu-6.8.0-100-generic.json.xz"
  ]
}
```
