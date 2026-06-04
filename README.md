# Zero

Zero 是一个面向内存取证的 Volatility 3 Web UI。当前版本只保留 Web 工作流，不包含 Volatility 2、TUI 和测试目录。

## 功能

- 加载本地内存镜像并运行 Volatility 3 插件
- 按 Linux / Windows 分类浏览和搜索插件
- 表格分页、排序、过滤和结果导出
- 插件参数默认值和导出目录管理
- AI 取证分析助手，支持当前插件结果上下文
- 本地符号表列表、远程符号表浏览和按需下载

## 环境要求

- Linux 或 macOS
- Python 3.8+
- Node.js 18+
- curl
- npm

## 安装

默认安装命令会自动下载 uv、创建 `.venv`、使用阿里云 PyPI 源安装 Python 依赖、安装前端依赖并构建静态前端。

```bash
make
```

默认 PyPI 源：

```text
https://mirrors.aliyun.com/pypi/simple
```

如需覆盖：

```bash
make PYPI_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
```

## 启动

生产式启动 Web UI：

```bash
make run
```

访问：

```text
http://localhost:8000
```

`make run` 会启动 FastAPI，并直接服务 `web/frontend/dist` 中的构建产物，不会启动 Vite。

## 开发启动

需要 Vite 热更新时使用：

```bash
make test-run
```

访问：

```text
http://localhost:5173
```

## 使用流程

1. 将内存镜像放入 `dumps/`，或在顶部输入镜像绝对路径。
2. 点击“加载”。
3. 在左侧选择 Linux 或 Windows 插件分类。
4. 搜索并运行 Volatility 3 插件。
5. 在结果表格中排序、过滤、导出，或发送给取证分析助手继续分析。

## 符号表

仓库不迁移符号表数据。需要符号表时可以用脚本按需导入：

```bash
scripts/import_symbols.sh
```

通过代理下载：

```bash
scripts/import_symbols.sh --proxy http://127.0.0.1:7890
```

指定内核版本：

```bash
scripts/import_symbols.sh --kernel 6.8.0-100-generic --query ubuntu
```

脚本支持 CentOS/RHEL-like、Ubuntu、Debian，会从 `Liuxunzhang/volatility3-symbols` 下载匹配当前内核的符号表到 `symbols/`。

## 常用命令

```bash
make                # 安装依赖并构建前端
make run            # 启动单端口 Web UI，不启动 Vite
make test-run       # 后端 reload + Vite 开发模式
make frontend-build # 重新构建前端
make clean          # 清理缓存和构建产物
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
