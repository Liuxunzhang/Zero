# Zero

Zero 是一个面向内存取证的 Volatility 3 Web 工作台。它把镜像加载、插件运行、结果过滤、符号表管理和 AI 辅助分析放在同一个浏览器界面里，目标是让一次内存镜像排查从“能跑插件”变成“能持续分析”。

当前版本专注于 Web UI 和 Volatility 3，不包含 Volatility 2、TUI 和测试目录。

## 适合什么场景

- 快速浏览 Linux / Windows 内存镜像中的进程、网络、模块、文件和注册表痕迹
- 对大结果集做列过滤、排序、分页和导出
- 使用 AI 助手基于当前插件结果提炼证据、生成过滤规则和规划下一步插件
- 按需生成 Volatility 3 Linux 符号表，不把符号表数据塞进业务仓库
- 在服务器上单端口启动 Web UI，直接从浏览器访问

## 功能概览

- Volatility 3 插件分类、搜索、运行和取消
- 内存镜像路径输入与 `dumps/` 目录下拉选择
- 表格过滤、列名补全、操作符补全、排序、分页和导出
- 插件参数弹窗，自动识别必填参数
- Linux 符号表本地列表和发行版生成脚本
- AI 取证分析助手，支持当前插件上下文和过滤规则输出
- 白天 / 黑夜双主题

## 环境要求

- Linux 或 macOS
- Python 3.8+
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
scripts/import_symbols.sh --distro debian13 --kernel 6.12.86+deb13
```

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
