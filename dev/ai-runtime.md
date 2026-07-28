# Zero AI Agent Runtime 架构

## 分层

`web/backend/ai_runtime/` 将旧的集中式 AI 路径拆为六层：

1. `providers/`：四种官方异步 SDK 协议到内部事件的适配。
2. `loop.py`：`run → turn → message → tool → turn_end → run_end` 状态机。
3. `context.py`：保守 token 估算、完整 turn 选择和 checkpoint。
4. `tools.py`：JSON Schema、风险/超时/执行模式、hook、进度与结果句柄。
5. `storage.py`：追加式 typed JSONL、credentials、独立 run event JSONL。
6. `runs.py` / `service.py`：后台任务、重连、取消、预算和组合根。

每个 turn 先生成不可变 `TurnSnapshot`。运行中修改 profile、提示词或工具目录，
只会影响下一 turn。

## 统一消息

- `UserMessage`
- `AssistantMessage`：`text`、公开 `thinking_summary`、`tool_call`
- `ToolResultMessage`：`content`、`details`、`is_error`

Assistant 同时保存标准化 stop reason、provider/model、请求 ID 和
input/output/cache/reasoning token。供应商专有 thinking 签名和隐藏推理不会进入
持久化消息；跨 provider 时仅转换可见文本、公开摘要和规范化后的工具 ID。

## 上下文与证据

阈值是 `context_window - effective_reserve`。reserve 默认 16,384 且最多占窗口
25%；最近消息默认保留 20,000 token 且最多占阈值一半。压缩只处理较旧完整
turn，tool call 与对应 result 是原子组。单个超大 turn 仅压缩完整前缀组。

checkpoint 记录目标、镜像身份、带来源的确认证据、假设、动作、未决问题和最近
消息。原始 JSONL 不改写。证据目录使用 `{image_id}/{conversation_id}` 双重隔离；
没有 `plugin`、`result_id` 或 `tool_call_id` 来源的条目不能进入证据索引。

旧全局 compressed memory 无可靠镜像来源，首次 Runtime 初始化时移动到
`.zero/ai/archive/*.bak-v1`，不会注入上下文。

## 工具与取消

Volatility 工具声明为 sequential，同一 engine 使用异步锁串行执行。Registry
保留 parallel 模式用于未来独立只读工具。未知工具、非法参数、schema 缺失项和
执行异常都转成 `is_error=true` 结果回注模型；被输出 token 截断的调用不执行。

`run_plugin` 只向模型返回 result handle、列、总数和最多 12,000 字符预览。
分页结果快照按镜像和会话存放。dump 工具非幂等，服务重启时只标记 interrupted，
不会自动重跑。

取消顺序为：设置 run cancel event → `cancel_plugin(engine_id)` 硬中断 Volatility
worker → 取消 SDK task → 写入 aborted 部分回答和终止事件。

## 与本地 pi 参考的关系

设计借鉴 pi 的 provider-neutral message/event、明确 agent loop、不可变 turn
snapshot、checkpoint compaction 和 append-only session 模式。Zero 的差异：

- Gemini 始终由完整 canonical 历史转换为无服务端状态 GenerateContent 请求。
- 工具仅限取证白名单，没有 shell、通用文件系统、skill/extension 或 steering。
- Volatility 同 engine 强制串行并支持 worker 硬取消。
- 会话固定到 engine + image，证据必须带取证来源且双重隔离。
- run event 额外持久化 7 天，以支持 Web 页面刷新和 SSE after-seq 重连。

实现未复制 pi 的实质代码；`pi/` 仅是本地、被 gitignore 排除的设计参考。
