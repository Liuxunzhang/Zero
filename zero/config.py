import sys

# 导出设置
EXPORT_DIR = "~/zero_exports"
DEFAULT_EXPORT_FORMAT = "csv"  # csv、json、txt

# 日志设置
LOG_FILE = "logs/zero.log"
LOG_LEVEL = "INFO"  # DEBUG、INFO、WARNING、ERROR

# Volatility3 设置
VOLATILITY_PLUGINS_PATH = "plugins"  # 自定义插件目录，None 表示使用默认目录
VOLATILITY_SYMBOLS_PATH = None  # 自定义符号目录，None 表示使用默认目录
SYMBOL_TABLE_RELATIVE_PATH = "symbols"  # 符号表相对项目根目录路径
PLUGIN_TIMEOUT_SECONDS = 600  # 单个插件最大运行时长，超时后自动中断
PLUGIN_STALL_TIMEOUT_SECONDS = 120  # 无进度超时时长，超时后自动中断

# 结果持久化与缓存策略
RESULTS_CACHE_DIR = "saved_results"  # 结果缓存根目录（可用绝对路径或相对项目根目录）
ENABLE_DISK_CACHE = True  # 是否启用磁盘缓存（重启后可复用）
DISK_CACHE_FORMAT = "csv"  # 磁盘缓存格式，目前支持 csv

# 运行与日志节流
PROGRESS_LOG_THROTTLE_SECONDS = 0.5  # 进度日志最小输出间隔（秒）
TERMINATE_GRACE_SECONDS = 2.0  # 终止插件进程时，先 terminate 再 kill 的等待秒数
HARD_INTERRUPT_MODE = True  # 是否启用硬中断（插件在独立进程中执行）
WORKER_START_METHOD = "spawn" if sys.platform == "darwin" else "fork"  # 插件进程启动方式：fork / spawn / forkserver

# 性能设置
MAX_TABLE_ROWS = 10000  # 表格最大显示行数
ENABLE_PAGINATION = False  # 大结果集是否启用分页

# 功能开关
ENABLE_EXPORT = True
ENABLE_FILTER = True
ENABLE_SORT = True
ENABLE_AUTO_REFRESH = False
AUTO_REFRESH_INTERVAL = 60  # 秒

# 取证助手设置
AI_PROVIDER = "baishanyun"                          # siliconflow / openai / deepseek / ollama
AI_API_KEY = ""                                    # API Key（Ollama 不需要；不要提交真实密钥）
AI_BASE_URL = "https://api.deepseek.com"          # API 地址，留空则按 provider 自动推断
AI_MODEL = "deepseek-v4-pro"                      # 模型名称，留空则按 provider 自动推断
AI_MAX_TOKENS = 4096                              # 最大响应 token 数
AI_TEMPERATURE = 0.1                              # 温度（取证分析建议低温度）
AI_CONTEXT_MAX_ROWS = 500                         # 传给 AI 的最大数据行数
AI_MAX_HISTORY = 12                               # 最大对话历史轮数
AI_SYSTEM_PROMPT = ""                                 # 自定义系统提示词，留空使用内置默认

# AI 多模型配置（前端添加的配置会自动回写到此列表）
# 每个配置项: {"id": "唯一ID", "name": "显示名称", "base_url": "API地址", "api_key": "", "model": "模型名"}
# 真实 API Key 只应保存在 .zero/ai/profiles.json 等被 .gitignore 忽略的运行时文件中。
AI_PROFILES = [
    {"id": "e4e6f40e", "name": "deepseek-v4-pro", "base_url": "https://api.deepseek.com", "api_key": "", "model": "deepseek-v4-pro"},
]
