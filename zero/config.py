import sys

# 导出设置
EXPORT_DIR = "~/zero_exports"

# HTTP 服务绑定（局域网暴露请显式改为 0.0.0.0，并考虑设置 API_TOKEN）
BIND_HOST = "127.0.0.1"
BIND_PORT = 8000
# 非空时要求 Authorization: Bearer <token> 或 X-API-Token。必须是纯 ASCII：
# HTTP 头部按 latin-1 编码，非 ASCII token 无法通过请求头传递。
API_TOKEN = ""
# 若设置列表，image/load 路径必须落在这些根目录下；None 表示不限制（本地取证默认）
ALLOW_IMAGE_PATHS = None

# 日志设置（相对路径按项目根目录解析；置空则只输出到控制台）
LOG_FILE = "logs/zero.log"
LOG_LEVEL = "INFO"  # DEBUG、INFO、WARNING、ERROR
LOG_MAX_BYTES = 5 * 1024 * 1024  # 单个日志文件上限，超过后轮转
LOG_BACKUP_COUNT = 3  # 保留的轮转日志份数

# Volatility3 设置
VOLATILITY_PLUGINS_PATH = "plugins"  # 自定义插件目录，None 表示使用默认目录
SYMBOL_TABLE_RELATIVE_PATH = "symbols"  # 符号表相对项目根目录路径
# 远程预制符号表仓库索引（GitHub owner/name，首项为默认）
SYMBOL_REMOTE_REPOS = [
    "Abyss-W4tcher/volatility3-symbols",
    "Sunmedalia/volatility3-symbols",
]
# GitHub API token（可选）。未设置时匿名配额约 60 次/小时；设置后约 5000 次/小时。
# 也可用环境变量 GITHUB_TOKEN / ZERO_GITHUB_TOKEN / SYMBOL_GITHUB_TOKEN（勿把真实 token 提交进仓库）。
SYMBOL_GITHUB_TOKEN = ""
# 远程索引内存/网络刷新间隔（秒）；期内直接读缓存。磁盘索引落在 .zero/symbols/remote_index/
SYMBOL_INDEX_TTL_SECONDS = 6 * 3600
# 刷新失败时仍可继续使用磁盘陈旧索引的最长时间（秒）
SYMBOL_INDEX_STALE_SECONDS = 7 * 24 * 3600
# 加载 Vol3 镜像时扫描 ``Linux version`` banner、检查本地 ISF，并在缺失时
# 由 GUI 询问是否下载。关闭后仍可在“符号”面板手动下载。
AUTO_DOWNLOAD_LINUX_SYMBOLS_ON_LOAD = True
# 0 表示扫描完整镜像；正整数表示只扫描镜像前 N 字节。扫描所有候选可避免
# 把内存中的旧发行版 banner 误判成当前内核。
AUTO_SYMBOL_SCAN_MAX_BYTES = 0
# 检测器每次读取的块大小；始终是流式读取，不会把整个镜像载入内存。
AUTO_SYMBOL_SCAN_CHUNK_BYTES = 4 * 1024 * 1024
# 同一 release 的候选 ISF 超过该数量时不自动下载，避免下载不确定的符号表。
AUTO_SYMBOL_DOWNLOAD_MAX_CANDIDATES = 4
PLUGIN_TIMEOUT_SECONDS = 600  # 单个插件最大运行时长，超时后自动中断
# 工作进程连续无心跳/输出的超时；Volatility 正常的静默扫描不会再触发该限制。
# 设为 0 可关闭卡死检测，仍保留上面的总执行超时。
PLUGIN_STALL_TIMEOUT_SECONDS = 120
WORKER_HEARTBEAT_SECONDS = 15  # 插件子进程在无框架进度时发送内部心跳的间隔

# 结果持久化与缓存策略
RESULTS_CACHE_DIR = "saved_results"  # 结果缓存根目录（可用绝对路径或相对项目根目录）
ENABLE_DISK_CACHE = True  # 是否启用磁盘缓存（重启后可复用）
DISK_CACHE_FORMAT = "csv"  # 磁盘缓存格式，目前支持 csv
# 常驻内存的完整插件结果集条数上限（LRU）。被淘汰的条目下次访问从磁盘缓存复原，
# 因此这个值只影响内存占用，不影响结果可用性。
RESULTS_MEMORY_CACHE_MAX = 8

# 运行与日志节流
PROGRESS_LOG_THROTTLE_SECONDS = 0.5  # 进度日志最小输出间隔（秒）
TERMINATE_GRACE_SECONDS = 2.0  # 终止插件进程时，先 terminate 再 kill 的等待秒数
HARD_INTERRUPT_MODE = True  # 是否启用硬中断（插件在独立进程中执行）
WORKER_START_METHOD = "spawn" if sys.platform == "darwin" else "fork"  # 插件进程启动方式：fork / spawn / forkserver

# 性能设置
# 单次导出的行数软上限（表格本身始终服务端分页）。设为 0 或 None 表示不限制。
MAX_TABLE_ROWS = 10000
# 服务端 get_results 对 filter+sort 结果集的 LRU 条数（按 filter/sort 计，不含 page）
RESULTS_QUERY_CACHE_MAX = 64
# 同一 LRU 的总行数预算：条数上限不足以约束内存（64 条 × 百万行结果各占一份行列表）
RESULTS_QUERY_CACHE_MAX_ROWS = 2_000_000

# 取证助手设置
AI_PROVIDER = "baishanyun"                          # siliconflow / openai / deepseek / ollama
AI_API_KEY = ""                                    # API Key（Ollama 不需要；不要提交真实密钥）
AI_BASE_URL = "https://api.deepseek.com"          # API 地址，留空则按 provider 自动推断
AI_MODEL = "deepseek-v4-pro"                      # 模型名称，留空则按 provider 自动推断
AI_MAX_TOKENS = 4096                              # 最大响应 token 数
AI_TEMPERATURE = 0.1                              # 温度（取证分析建议低温度）
AI_CONTEXT_MAX_ROWS = 500                         # 传给 AI 的最大数据行数
AI_MAX_HISTORY = 12                               # 最大对话历史轮数

# AI 多模型配置（前端添加的配置会自动回写到此列表）
# 每个配置项: {"id": "唯一ID", "name": "显示名称", "base_url": "API地址", "api_key": "", "model": "模型名"}
# 真实 API Key 只应保存在 .zero/ai/profiles.json 等被 .gitignore 忽略的运行时文件中。
AI_PROFILES = [
    {"id": "e4e6f40e", "name": "deepseek-v4-pro", "base_url": "https://api.deepseek.com", "api_key": "", "model": "deepseek-v4-pro"},
]
