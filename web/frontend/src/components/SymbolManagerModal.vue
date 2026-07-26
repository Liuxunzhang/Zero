<template>
  <div class="modal-overlay" @click.self="$emit('close')">
    <div class="modal-container symbol-modal">
      <div class="modal-tabs">
        <button class="modal-tab" :class="{ active: tab === 'index' }" @click="tab = 'index'">仓库索引</button>
        <button class="modal-tab" :class="{ active: tab === 'script' }" @click="tab = 'script'">生成脚本</button>
        <button class="modal-tab" :class="{ active: tab === 'downloads' }" @click="tab = 'downloads'">下载记录</button>
        <button class="modal-close-btn" @click="$emit('close')">关闭</button>
      </div>

      <!-- 第一页：远程仓库索引 + 本地符号表 -->
      <div v-if="tab === 'index'" class="modal-body symbol-modal-body">
        <div class="symbol-panel">
          <div class="symbol-panel-title">预制符号表</div>
          <div class="symbol-panel-subtitle">从 GitHub 仓库索引浏览并下载 ISF 符号表</div>

          <div class="symbol-toolbar">
            <select v-model="selectedRepo" class="symbol-select symbol-repo-select" @change="onRepoChange">
              <option v-for="r in availableRepos" :key="r" :value="r">{{ r }}</option>
            </select>
          </div>

          <div v-if="remoteStatus.error" class="symbol-remote-status error">
            索引更新失败，当前显示缓存数据：{{ remoteStatus.error }}
          </div>
          <div v-else class="symbol-remote-status ok">
            {{ statusSummary }}
          </div>

          <div class="symbol-toolbar">
            <input
              v-model="query"
              class="symbol-input"
              placeholder="搜索符号文件名或路径..."
              @keydown.enter="loadRemote(1)"
            />
            <select v-model="osFilter" class="symbol-select" @change="loadRemote(1)">
              <option value="">全部</option>
              <option value="linux">Linux</option>
              <option value="mac">macOS</option>
              <option value="windows">Windows</option>
            </select>
            <button class="add-btn" @click="loadRemote(1)" :disabled="remoteLoading">搜索</button>
            <button class="form-cancel-btn" @click="loadRemote(1, true)" :disabled="remoteLoading" title="强制从 GitHub 刷新索引（消耗 API 配额）">
              刷新索引
            </button>
          </div>

          <div class="symbol-list" v-if="remoteRows.length">
            <label v-for="item in remoteRows" :key="`${item.repo || selectedRepo}:${item.path}`" class="symbol-item">
              <input type="checkbox" :value="item.path" v-model="selectedPaths" />
              <div class="symbol-item-main">
                <div class="symbol-item-name">{{ item.name }}</div>
                <div class="symbol-item-path">{{ item.path }}</div>
              </div>
              <div class="symbol-item-meta">{{ formatSize(item.size) }}</div>
            </label>
          </div>
          <div v-else class="symbol-empty">{{ remoteLoading ? '加载中...' : '暂无结果' }}</div>

          <div class="symbol-actions">
            <button class="add-btn" :disabled="!selectedPaths.length || downloading" @click="downloadSelected">
              {{ downloading ? '下载中...' : `下载选中项 (${selectedPaths.length})` }}
            </button>
            <button class="form-cancel-btn" @click="selectedPaths = []" :disabled="!selectedPaths.length">清空选择</button>
          </div>

          <div class="symbol-page" v-if="remoteTotalPages > 1">
            <button class="page-btn" :disabled="remotePage <= 1 || remoteLoading" @click="loadRemote(remotePage - 1)">上一页</button>
            <span>第 {{ remotePage }} / {{ remoteTotalPages }} 页 · 共 {{ remoteTotal }} 项</span>
            <button class="page-btn" :disabled="remotePage >= remoteTotalPages || remoteLoading" @click="loadRemote(remotePage + 1)">下一页</button>
          </div>
        </div>

        <div class="symbol-panel">
          <div class="symbol-panel-title">本地符号表</div>
          <div class="symbol-panel-subtitle">{{ localRoot || '-' }}</div>
          <button class="form-cancel-btn symbol-refresh" @click="loadLocal" :disabled="localLoading">刷新</button>

          <div class="symbol-list" v-if="localRows.length">
            <div v-for="item in localRows" :key="item.path" class="symbol-item local">
              <div class="symbol-item-main">
                <div class="symbol-item-name">{{ item.path }}</div>
              </div>
              <div class="symbol-item-meta">{{ formatSize(item.size) }}</div>
            </div>
          </div>
          <div v-else class="symbol-empty">{{ localLoading ? '加载中...' : '本地暂无符号表' }}</div>
        </div>
      </div>

      <!-- 第二页：符号表生成脚本 -->
      <div v-else-if="tab === 'script'" class="modal-body symbol-script-tab">
        <div class="symbol-panel symbol-panel-full">
          <div class="symbol-panel-title">生成脚本</div>
          <div class="symbol-panel-subtitle">
            统一脚本位于 scripts/import_symbols.sh，推荐在目标发行版环境中生成匹配内核的符号表
          </div>
          <div class="symbol-script-help">
            <div class="symbol-script-command">scripts/import_symbols.sh --distro ubuntu22_24</div>
            <div class="symbol-script-command">scripts/import_symbols.sh --distro centos7</div>
            <div class="symbol-script-command">scripts/import_symbols.sh --distro debian13</div>
            <div class="symbol-script-command">scripts/import_symbols.sh --distro centos8_proxy --proxy http://127.0.0.1:7890</div>
            <div class="symbol-script-command">scripts/import_symbols.sh --distro debian13 --kernel 6.12.86+deb13</div>
            <div class="symbol-script-note">
              单脚本会按发行版流程安装或下载内核调试包，准备 dwarf2json，并从 vmlinux / System.map
              生成符号表到 symbols/。生成后 Web 服务会在下次运行插件时自动扫描，无需重启。
            </div>
            <div class="symbol-script-note">
              支持发行版：ubuntu22_24、debian13、centos6、centos7、centos8、centos8_proxy。
              预制仓库索引仅覆盖部分常见内核；精确匹配时优先使用本机生成。
            </div>
          </div>
        </div>
      </div>

      <!-- 第三页：下载记录 -->
      <div v-else class="modal-body symbol-download-tab">
        <div class="symbol-download-manager">
          <div class="symbol-manager-header">
            <div class="symbol-panel-title">下载记录</div>
            <div class="symbol-manager-status" :class="{ busy: downloading }">
              {{ downloading ? '下载任务进行中' : '空闲' }}
            </div>
          </div>

          <div v-if="downloadHistory.length" class="symbol-manager-content">
            <div class="symbol-history-list">
              <button
                v-for="record in downloadHistory"
                :key="record.id"
                class="symbol-history-item"
                :class="{ active: activeHistoryId === record.id }"
                @click="activeHistoryId = record.id"
              >
                <div class="symbol-history-time">{{ formatDateTime(record.created_at) }}</div>
                <div class="symbol-history-summary">
                  {{ record.repo ? `${record.repo} · ` : '' }}请求 {{ record.requested }} | 成功 {{ record.downloaded.length }} | 跳过 {{ record.skipped.length }} | 失败 {{ record.failed.length }}
                </div>
              </button>
            </div>

            <div v-if="activeRecord" class="symbol-history-detail">
              <div class="symbol-history-actions">
                <button
                  class="add-btn"
                  @click="retryFailed"
                  :disabled="downloading || !activeRecord.failed.length"
                >重试失败项 ({{ activeRecord.failed.length }})</button>
                <button class="form-cancel-btn" @click="clearHistory">清空记录</button>
              </div>

              <div class="symbol-history-group">
                <div class="symbol-history-group-title">成功下载 ({{ activeRecord.downloaded.length }})</div>
                <div v-if="activeRecord.downloaded.length" class="symbol-history-group-list">
                  <div v-for="item in activeRecord.downloaded" :key="`ok-${item.path}`" class="symbol-history-line ok">{{ item.path }}</div>
                </div>
                <div v-else class="symbol-history-empty">无</div>
              </div>

              <div class="symbol-history-group">
                <div class="symbol-history-group-title">已跳过 ({{ activeRecord.skipped.length }})</div>
                <div v-if="activeRecord.skipped.length" class="symbol-history-group-list">
                  <div v-for="item in activeRecord.skipped" :key="`skip-${item.path}`" class="symbol-history-line skip">{{ item.path }} ({{ item.reason }})</div>
                </div>
                <div v-else class="symbol-history-empty">无</div>
              </div>

              <div class="symbol-history-group">
                <div class="symbol-history-group-title">失败项 ({{ activeRecord.failed.length }})</div>
                <div v-if="activeRecord.failed.length" class="symbol-history-group-list">
                  <div v-for="item in activeRecord.failed" :key="`fail-${item.path}`" class="symbol-history-line fail">{{ item.path }} ({{ item.reason }})</div>
                </div>
                <div v-else class="symbol-history-empty">无</div>
              </div>
            </div>
          </div>
          <div v-else class="symbol-empty manager-empty">暂无下载记录</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { getRemoteSymbols, getLocalSymbols, downloadSymbols } from '../api'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const STORAGE_KEY = 'zero-symbol-download-history'
const DEFAULT_REPOS = [
  'Abyss-W4tcher/volatility3-symbols',
  'Sunmedalia/volatility3-symbols',
]

const tab = ref('index')

const query = ref('')
const osFilter = ref('')
const availableRepos = ref([...DEFAULT_REPOS])
const selectedRepo = ref(DEFAULT_REPOS[0])
const remoteRows = ref([])
const remoteLoading = ref(false)
const remotePage = ref(1)
const remoteTotalPages = ref(1)
const remoteTotal = ref(0)

const localRows = ref([])
const localLoading = ref(false)
const localRoot = ref('')

const selectedPaths = ref([])
const downloading = ref(false)
const remoteStatus = ref({
  ok: true,
  error: '',
  last_updated_at: 0,
  cached: false,
  source: '',
  github_auth: false,
  rate_limit: null,
})
const downloadHistory = ref([])
const activeHistoryId = ref('')

const activeRecord = computed(() => downloadHistory.value.find((x) => x.id === activeHistoryId.value) || null)

const statusSummary = computed(() => {
  const s = remoteStatus.value || {}
  const parts = []
  const sourceMap = {
    network: '网络',
    disk: '磁盘缓存',
    'disk-stale': '陈旧磁盘缓存',
    memory: '内存缓存',
    stale: '陈旧缓存',
  }
  if (s.source) parts.push(`来源 ${sourceMap[s.source] || s.source}`)
  if (s.last_updated_at) parts.push(`更新 ${formatTime(s.last_updated_at)}`)
  if (s.github_auth) {
    parts.push('已配置 GitHub Token')
  } else {
    parts.push('匿名 API（约 60 次/小时）')
  }
  const rl = s.rate_limit
  if (rl && typeof rl.remaining === 'number' && rl.remaining >= 0) {
    const limit = rl.limit || (s.github_auth ? 5000 : 60)
    parts.push(`配额剩余 ${rl.remaining}/${limit}`)
  }
  return parts.length ? parts.join(' · ') : '索引状态正常'
})

function formatSize(size) {
  const n = Number(size || 0)
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

function formatTime(ts) {
  if (!ts) return '-'
  try {
    return new Date(Number(ts) * 1000).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return '-'
  }
}

function formatDateTime(ts) {
  try {
    return new Date(ts).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return '-'
  }
}

function pushDownloadRecord(data, requestedCount, repo) {
  const record = {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    created_at: Date.now(),
    requested: requestedCount,
    repo: repo || data.repo || selectedRepo.value,
    downloaded: data.downloaded || [],
    skipped: data.skipped || [],
    failed: data.failed || [],
  }
  downloadHistory.value.unshift(record)
  downloadHistory.value = downloadHistory.value.slice(0, 20)
  activeHistoryId.value = record.id
  localStorage.setItem(STORAGE_KEY, JSON.stringify(downloadHistory.value))
}

function restoreHistory() {
  const raw = localStorage.getItem(STORAGE_KEY)
  if (!raw) return
  try {
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return
    downloadHistory.value = parsed.slice(0, 20)
    activeHistoryId.value = downloadHistory.value[0]?.id || ''
  } catch {
    downloadHistory.value = []
    activeHistoryId.value = ''
  }
}

function onRepoChange() {
  selectedPaths.value = []
  loadRemote(1)
}

async function loadRemote(page = 1, forceRefresh = false) {
  remoteLoading.value = true
  try {
    const data = await getRemoteSymbols({
      query: query.value.trim() || undefined,
      os: osFilter.value || undefined,
      page,
      page_size: 100,
      repo: selectedRepo.value || undefined,
      force_refresh: forceRefresh || undefined,
    })
    remoteRows.value = data.items || []
    remotePage.value = data.page || 1
    remoteTotalPages.value = data.total_pages || 1
    remoteTotal.value = data.total || 0
    if (Array.isArray(data.repos) && data.repos.length) {
      availableRepos.value = data.repos
    }
    if (data.repo) {
      selectedRepo.value = data.repo
    }
    remoteStatus.value = data.status || {
      ok: true,
      error: '',
      last_updated_at: 0,
      cached: false,
      source: '',
      github_auth: false,
      rate_limit: null,
    }
    if (forceRefresh && remoteStatus.value.error) {
      store.pushMessage(`索引刷新失败，已保留缓存: ${remoteStatus.value.error}`, 'warning')
    }
  } catch (e) {
    remoteRows.value = []
    remoteTotal.value = 0
    remoteStatus.value = {
      ok: false,
      error: e.message,
      last_updated_at: 0,
      cached: false,
      source: '',
      github_auth: false,
      rate_limit: null,
    }
    store.pushMessage(`加载符号表索引失败: ${e.message}`, 'error')
  } finally {
    remoteLoading.value = false
  }
}

async function loadLocal() {
  localLoading.value = true
  try {
    const data = await getLocalSymbols()
    localRows.value = data.items || []
    localRoot.value = data.root || ''
  } catch (e) {
    localRows.value = []
    store.pushMessage(`加载本地符号表失败: ${e.message}`, 'error')
  } finally {
    localLoading.value = false
  }
}

async function downloadSelected() {
  if (!selectedPaths.value.length) return
  downloading.value = true
  const requestedCount = selectedPaths.value.length
  const repo = selectedRepo.value
  try {
    const data = await downloadSymbols(selectedPaths.value, repo)
    pushDownloadRecord(data, requestedCount, repo)
    tab.value = 'downloads'
    const downloaded = (data.downloaded || []).length
    const skipped = (data.skipped || []).length
    const failed = (data.failed || []).length
    store.pushMessage(`符号表下载完成: 成功 ${downloaded}，跳过 ${skipped}，失败 ${failed}`, failed ? 'warning' : 'success')
    if (downloaded > 0) {
      await loadLocal()
    }
  } catch (e) {
    store.pushMessage(`下载符号表失败: ${e.message}`, 'error')
  } finally {
    downloading.value = false
  }
}

async function retryFailed() {
  if (!activeRecord.value || !activeRecord.value.failed.length) return
  downloading.value = true
  const repo = activeRecord.value.repo || selectedRepo.value
  try {
    const paths = activeRecord.value.failed.map((x) => x.path)
    const data = await downloadSymbols(paths, repo)
    pushDownloadRecord(data, paths.length, repo)
    tab.value = 'downloads'
    const downloaded = (data.downloaded || []).length
    const skipped = (data.skipped || []).length
    const failed = (data.failed || []).length
    store.pushMessage(`重试完成: 成功 ${downloaded}，跳过 ${skipped}，失败 ${failed}`, failed ? 'warning' : 'success')
    if (downloaded > 0) {
      await loadLocal()
    }
  } catch (e) {
    store.pushMessage(`重试失败项失败: ${e.message}`, 'error')
  } finally {
    downloading.value = false
  }
}

function clearHistory() {
  downloadHistory.value = []
  activeHistoryId.value = ''
  localStorage.removeItem(STORAGE_KEY)
}

onMounted(async () => {
  restoreHistory()
  await Promise.all([loadRemote(1), loadLocal()])
})
</script>
