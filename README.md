# Zero

Zero 是一个面向内存取证的多引擎 Web 工作台。它把镜像加载、Volatility 3 / YARA-X 扫描、结果过滤、规则与符号管理和 AI 辅助分析放在同一个浏览器界面里。

AI Runtime 的分层、会话、checkpoint、工具和重连设计见
[`dev/ai-runtime.md`](dev/ai-runtime.md)，接口见 [`dev/api.md`](dev/api.md)。

当前内置 Volatility 3 与 YARA-X 1.19.0，不包含 MemProcFS、Volatility 2 或 TUI。

## 适合什么场景

- 快速浏览 Linux / Windows 内存镜像中的进程、网络、模块、文件和注册表痕迹
- 对大结果集做列过滤、排序、分页和导出
- 使用 AI 助手基于当前插件结果提炼证据、生成过滤规则和规划下一步插件
- 按需生成 Volatility 3 Linux 符号表，不把符号表数据塞进业务仓库
- 在服务器上单端口启动 Web UI，直接从浏览器访问

## 功能概览

- Volatility 3 插件分类、搜索、运行和取消
- YARA-X 规则包扫描、ZIP 安全导入、多文件编辑、不可变版本与 GitHub 规则市场
- 引擎级镜像、任务、结果、缓存与 Findings 隔离；Volatility 3 保持默认
- 内存镜像路径输入与 `dumps/` 目录下拉选择
- 表格过滤、列名补全、操作符补全、排序、分页和导出
- 插件参数弹窗，自动识别必填参数
- Linux 符号表本地列表和发行版生成脚本
- 加载 Linux 镜像时识别 kernel banner，先检查本地 ISF，再询问是否下载
- AI 取证分析助手，支持当前插件上下文和过滤规则输出
- 白天 / 黑夜双主题

## 环境要求

- Linux 或 macOS
- Python 3.10+
- Node.js 18+
- curl
- npm

## 快速安装

```bash
make
```

`make` 会自动完成：

- 下载或复用 `uv`
- 创建 `.venv`
- 使用阿里云 PyPI 源安装 Python 依赖
- 安装前端依赖
- 构建静态前端

默认 PyPI 源：

```text
https://mirrors.aliyun.com/pypi/simple
```

如需覆盖：

```bash
make PYPI_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
```

## 启动

```bash
make run
```

访问：

```text
http://localhost:8000
```

`make run` 只启动 FastAPI，并直接服务 `web/frontend/dist` 中的静态前端，不启动 Vite。

默认只监听本机 `127.0.0.1`。局域网访问需要显式放开：

```bash
make run ZERO_BIND_HOST=0.0.0.0
```

## 开发模式

```bash
make test-run
```

访问：

```text
http://localhost:5173
```

`make test-run` 会启动后端 reload 和 Vite 开发服务器。

## 测试

```bash
make test
```

运行 `tests/` 下的 pytest（缓存 key、磁盘缓存、过滤、分页 LRU 等，不依赖真实内存镜像）。

## 安全部署

| 配置 | 说明 |
|------|------|
| `BIND_HOST` / `ZERO_BIND_HOST` | 默认 `127.0.0.1`；公网/局域网改为 `0.0.0.0` 时务必配合鉴权 |
| `API_TOKEN`（`zero/config.py`） | 非空时，API/WS 需要 `Authorization: Bearer <token>` 或 `X-API-Token`；WebSocket 可用 `?token=` |
| 前端 Token | 顶栏 **Token** 按钮，写入本机 `localStorage`（`zero-api-token`） |
| `ALLOW_IMAGE_PATHS` | 若设为路径列表，则 `image/load` 只能加载白名单根目录下的镜像 |

## 结果缓存

插件结果会写入磁盘以便重启后复用：

```text
saved_results/vol3/{image_id}/{plugin}/{kwargs_digest}.csv
saved_results/vol3/{image_id}/{plugin}/{kwargs_digest}.meta.json
```

- `image_id` 由镜像路径 + mtime + size 生成，同名不同内容的 dump 不会撞缓存。
- 缓存 key **包含插件参数**（如 pid）；不同参数不会互相命中。
- 旧版 `saved_results/vol3/{image_name}/{plugin}.csv` **不再读取**，可手动删除或使用清理脚本。
- UI 状态栏 **强制重跑** 会忽略缓存重新执行插件（`force: true`）。

清理磁盘缓存：

```bash
python scripts/clear_stale_cache.py --all --dry-run
python scripts/clear_stale_cache.py --older-than-days 30
python scripts/clear_stale_cache.py --all
```

## 基本使用

1. 将内存镜像放入 `dumps/`，或在顶部输入镜像绝对路径。
2. 点击“加载”。
3. 在左侧选择 Linux 或 Windows 插件分类。
4. 搜索并运行 Volatility 3 插件。
5. 使用表格过滤、排序、分页和导出定位证据。
6. 将结果发送给取证分析助手继续分析。

## YARA-X 规则中心

从左上角引擎选择器切换到 **YARA-X**，再打开“规则中心”：

- **已安装**：启停、编辑、导出或删除规则包；一个包对应一个
  `package.<package_id>` 扫描插件。
- **ZIP 导入**：先预览安全解压与整包编译诊断，再确认安装；编译失败的包可保留为
  禁用草稿。
- **规则编辑器**：目录树、多标签、YARA 高亮、服务端草稿、结构化诊断、修订冲突保护，
  以及“提交并激活”不可变版本。
- **版本历史**：查看内容摘要并从历史创建恢复版本。
- **在线市场**：内置 YARA Forge Core / Extended / Full、signature-base 和
  ReversingLabs；自定义源只接受 GitHub `owner/repo`、ref 和包内子目录。

规则包可包含 `zero-yara.json`：

```json
{
  "schema_version": 1,
  "name": "Incident rules",
  "version": "1.0.0",
  "description": "Local hunt rules",
  "license": "Apache-2.0",
  "homepage": "https://github.com/example/rules",
  "entrypoints": ["main.yar"],
  "globals": {
    "tenant": {"type": "string", "default": "default"}
  }
}
```

Include 只能指向当前包根目录内的相对路径；绝对路径、`..`、符号链接、硬链接、循环
include 和大小写冲突都会被拒绝。YARA-X 默认扫描超时 300 秒、每 Pattern 1000 个匹配、
最多 100000 行，并关闭 fast-scan；这些选项可在系统设置的 YARA-X 页面调整。

## 过滤搜索

过滤栏支持两种模式：普通文本搜索和结构化表达式。

普通文本搜索会在所有列中做包含匹配：

```text
systemd
```

结构化表达式用于精确过滤列：

```text
PID -eq 1
COMM -contain ssh
Path -match "/tmp/.*"
```

支持逻辑组合：

```text
COMM -contain ssh && PID -gt 100
Path -startswith /usr || Path -contain deleted
```

支持的操作符：

```text
-eq           等于
-ne           不等于
-gt           大于
-lt           小于
-ge           大于等于
-le           小于等于
-contain      包含
-notcontain   不包含
-match        正则匹配
-startswith   前缀匹配
-endswith     后缀匹配
```

补全能力：

- 输入列名前缀时，会补全当前结果表的列名。
- 输入列名后按空格，会补全操作符。
- 输入操作符后，会基于当前列的可见结果给出样例值。
- 完成一个条件后，会补全 `&&` 和 `||`。
- `Tab` 选择当前补全项，`Enter` 应用过滤，`Esc` 清空。

表达式不完整时，前端不会立即发送到后端，避免半截表达式触发错误提示。

## 符号表

仓库不迁移符号表数据。需要符号表时按需生成。统一入口在 `scripts/import_symbols.sh`，它归纳了 Ubuntu、Debian、CentOS 旧脚本的流程：安装或下载内核调试包，准备 `dwarf2json`，再从 `vmlinux` / `System.map` 生成 Volatility 3 可用的 `json.xz` 符号表。

```bash
scripts/import_symbols.sh
```

指定发行版流程：

```bash
scripts/import_symbols.sh --distro ubuntu22_24
scripts/import_symbols.sh --distro centos7
scripts/import_symbols.sh --distro debian13
```

通过 HTTP 代理下载调试包或 `dwarf2json`：

```bash
scripts/import_symbols.sh --distro centos8_proxy --proxy http://127.0.0.1:7890
```

Debian 13 流程支持指定内核版本：

```bash
scripts/import_symbols.sh --distro debian13 --kernel 6.12.96+deb13-amd64
```

在目标内核机器上运行 Debian 13 流程时不需要 `--kernel`，脚本会使用
`uname -r`。只有为其他机器或导入镜像生成时，才需要传完整 kernel release。

可用发行版参数：

```text
ubuntu22_24
debian13
debian_pre13_2
debian13_snapshot
centos6
centos7
centos8
centos8_proxy
```

生成后的符号表会写入 `symbols/` 根目录。Web 服务每次运行插件前都会重新扫描 `symbols/`，生成完成后无需重启。

符号管理页的“使用 gh-proxy.com 代理下载”选项只代理选中 ISF 文件的下载；仓库索引仍直接请求 GitHub API。该选项默认关闭，并保存在浏览器本地以便下次使用。

### 加载时检查 Linux 符号表

默认情况下，加载 `vol3` 镜像后会先按镜像绝对路径、大小和修改时间读取已持久化的内核版本。命中时跳过大镜像 banner 扫描，直接检查对应符号表；未命中时才流式扫描有效的 `Linux version ...` banner，并从全部候选中选择最可信的 kernel release，识别成功后写入 `.zero/images/kernel_versions.json`。该检测不启动 Volatility，也不会把镜像整体读入内存。检测完成后先检查本地符号表，已存在则直接使用；缺失时弹窗提供“直接下载”“gh-proxy 下载”和“暂不下载”。下载阶段会显示圆形进度。

- 只根据**完整 kernel release**匹配；候选过多时不会盲目批量下载。
- 本地精确 release 匹配发生在远程索引请求之前；远程索引复用磁盘缓存。
- 未发现 banner、没有匹配项、网络/下载失败都不会阻止镜像加载，状态会显示在消息栏。

可在 `zero/config.py` 调整：

```python
AUTO_DOWNLOAD_LINUX_SYMBOLS_ON_LOAD = True  # 设为 False 关闭加载后检查
AUTO_SYMBOL_SCAN_MAX_BYTES = 0              # 0=扫描完整镜像
IMAGE_KERNEL_CACHE_FILE = ".zero/images/kernel_versions.json"
AUTO_SYMBOL_SCAN_CHUNK_BYTES = 4 * 1024 * 1024
AUTO_SYMBOL_DOWNLOAD_MAX_CANDIDATES = 4
```

如果需要缩短超大镜像的加载后检查时间，可把 `AUTO_SYMBOL_SCAN_MAX_BYTES` 设为正整数（例如 `512 * 1024 * 1024`）以限制扫描范围。

### WebUI 系统运行设置

顶部工具栏的“系统”入口可以直接调整常用后端配置，保存后立即应用，并持久化到不会提交进 Git 的 `.zero/runtime_settings.json`：

- **运行与超时**：插件总超时、无响应超时、工作进程心跳、进度日志节流、停止宽限时间、子进程启动方式、日志级别。
- **符号表**：自动准备开关、banner 扫描上限/块大小、候选上限、远程索引刷新与陈旧缓存时间。
- **结果与缓存**：磁盘缓存开关、完整结果内存缓存、筛选/排序缓存数量与总行数、单次导出行数上限。

这些设置采用严格的字段白名单、范围校验和关联校验，不允许通过 Web 修改 API Token、GitHub Token、任意目录或其他敏感配置。AI 模型参数仍在“助手”设置中维护；插件的 PID、offset、dump_dir 等默认参数已合并到“系统 → 插件参数”。顶栏“取证”工作簿统一承载发现标记和分析记事，并可合并生成报告。

### AI 助手推荐配置

默认提供共享同一安全凭据的两个 DeepSeek Profile：

- **V4 Flash · 快速**：默认启用，适合普通问答和快速初筛。
- **V4 Pro · 深度分析**：手动切换，用于复杂证据综合和高强度推理。

在“助手 → 模型配置”中为任一 DeepSeek Profile 录入一次 API Key 即可共享使用；
密钥仅写入权限为 0600 的 `.zero/ai/credentials.json`。响应 token 建议保持
`Profile 推荐值（auto）`，只有明确需要超长输出时才切换 `max`。

## 常用命令

```bash
make                # 安装依赖并构建前端
make run            # 启动单端口 Web UI，不启动 Vite
make test-run       # 后端 reload + Vite 开发模式
make frontend-build # 重新构建前端
make clean          # 清理缓存和构建产物
```

## 目录结构

```text
zero/               Python 后端核心包
web/backend/        FastAPI API 与服务层
web/frontend/       Vue 前端
scripts/            运维与符号表生成脚本
dev/api.md          API 文档
plugins/            自定义 Volatility 3 插件目录
```

## API 文档

接口说明放在：

```text
dev/api.md
```

FastAPI 运行后也可以访问：

```text
http://localhost:8000/docs
```
