<template>
  <div class="app-layout">
    <AppSidebar
      :collapsed="pluginSidebarCollapsed"
      @update:collapsed="pluginSidebarCollapsed = $event"
    />
    <div class="main-panel" :class="{ 'plugin-sidebar-hidden': pluginSidebarCollapsed }">
      <!-- Top bar -->
      <div class="topbar">
        <div class="topbar-image-group">
          <span class="topbar-image-label">镜像路径</span>
          <div class="image-select-wrapper">
            <input
              class="topbar-image-input"
              type="text"
              v-model="localImagePath"
              placeholder="输入镜像文件路径..."
              @keydown.enter="doLoadImage"
              @focus="showDropdown = true"
            />
            <button
              class="image-dropdown-toggle"
              type="button"
              title="选择 dumps/ 目录中的镜像"
              @mousedown.prevent="showDropdown = !showDropdown"
            >
              <AppIcon name="chevron-down" :size="12" />
            </button>
            <!-- Dropdown for dumps/ files -->
            <div v-show="showDropdown" class="image-dropdown">
              <div class="image-dropdown-header">dumps/ 目录镜像文件</div>
              <button
                v-for="file in dumpFiles"
                :key="file.path"
                class="image-dropdown-item"
                @mousedown.prevent="selectImage(file)"
              >
                <span class="image-dropdown-name">{{ file.name }}</span>
                <span class="image-dropdown-size">{{ file.size }}</span>
              </button>
              <div v-if="!dumpFiles.length" class="image-dropdown-empty">
                dumps/ 目录中没有镜像文件
              </div>
            </div>
          </div>
          <button
            class="topbar-load-btn"
            :disabled="!localImagePath || store.pluginBusy || store.imageLoadBusy || store.symbolDownloadBusy"
            @click="doLoadImage"
          >{{ store.imageLoadBusy ? '加载中...' : '加载' }}</button>
          <div
            v-if="store.symbolDownloadBusy"
            class="symbol-download-progress"
            :title="symbolDownloadTitle"
            role="status"
            :aria-label="symbolDownloadTitle"
          >
            <svg
              class="symbol-progress-ring"
              :class="{ indeterminate: store.symbolDownloadProgress == null }"
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              <circle class="symbol-progress-track" cx="12" cy="12" r="9" />
              <circle
                class="symbol-progress-value"
                cx="12"
                cy="12"
                r="9"
                :style="{ strokeDashoffset: symbolProgressOffset }"
              />
            </svg>
            <span v-if="store.symbolDownloadProgress != null" class="symbol-progress-text">
              {{ Math.round(store.symbolDownloadProgress) }}%
            </span>
          </div>
        </div>
        <div class="topbar-runtime-title">
          <span class="topbar-system-name">{{ systemDisplayName }}</span>
          <span class="topbar-plugin-name" :class="{ muted: !store.currentPlugin }">
            {{ compactPluginName }}
          </span>
        </div>
        <div class="topbar-spacer"></div>
        <button
          class="ai-toggle-btn"
          :class="{ active: showSystemSettings }"
          @click="showSystemSettings = true"
          title="系统运行设置"
        >
          <AppIcon class="ai-toggle-icon" name="wrench" />
          <span class="ai-toggle-text">系统</span>
        </button>
        <button
          v-if="store.selectedEngine === 'yarax'"
          class="ai-toggle-btn"
          :class="{ active: showRuleCenter }"
          @click="showRuleCenter = true"
          title="YARA-X 规则中心"
        >
          <AppIcon class="ai-toggle-icon" name="package" />
          <span class="ai-toggle-text">规则</span>
        </button>
        <button
          v-if="store.selectedEngine === 'vol3'"
          class="ai-toggle-btn"
          @click="store.showArgsPanel = !store.showArgsPanel"
          title="参数配置"
        >
          <AppIcon class="ai-toggle-icon" name="settings" />
          <span class="ai-toggle-text">参数</span>
        </button>
        <button
          class="ai-toggle-btn"
          :class="{ active: showNotepad }"
          @click="showNotepad = true"
          title="取证记事本"
        >
          <AppIcon class="ai-toggle-icon" name="notebook" />
          <span class="ai-toggle-text">记事</span>
        </button>
        <button
          class="ai-toggle-btn"
          :class="{ active: showFindings }"
          @click="showFindings = true"
          title="取证发现（右键表格行标记）"
        >
          <AppIcon class="ai-toggle-icon" name="star" />
          <span class="ai-toggle-text">发现</span>
        </button>
        <button
          class="ai-toggle-btn"
          :class="{ active: showAiPanel }"
          @click="showAiPanel = !showAiPanel"
          title="取证分析助手"
        >
          <AppIcon class="ai-toggle-icon" name="bot" />
          <span class="ai-toggle-text">助手</span>
        </button>
        <button
          v-if="store.selectedEngine === 'vol3'"
          class="ai-toggle-btn"
          :class="{ active: showSymbolManager }"
          @click="showSymbolManager = true"
          title="符号表管理"
        >
          <AppIcon class="ai-toggle-icon" name="package" />
          <span class="ai-toggle-text">符号</span>
        </button>
        <button
          class="ai-toggle-btn topbar-log-btn"
          :class="{ active: showLogPanel }"
          @click="showLogPanel = true"
          title="查看消息日志"
        >
          <AppIcon class="ai-toggle-icon" name="file-text" />
          <span class="ai-toggle-text">日志</span>
          <span v-if="logErrorCount" class="topbar-log-error-badge">
            {{ logErrorCount > 99 ? '99+' : logErrorCount }}
          </span>
        </button>
        <div class="topbar-token-wrap" ref="tokenWrapRef">
          <button
            class="ai-toggle-btn"
            :class="{ active: showTokenMenu }"
            title="API Token（后端开启鉴权时使用）"
            @click="showTokenMenu = !showTokenMenu"
          >
            <AppIcon class="ai-toggle-icon" name="key" />
            <span class="ai-toggle-text">Token</span>
          </button>
          <div v-if="showTokenMenu" class="topbar-token-popover" @click.stop>
            <div class="topbar-token-title">API Token</div>
            <p class="topbar-token-hint">仅保存在本机 localStorage。后端未设置 API_TOKEN 时可留空。</p>
            <input
              v-model="localApiToken"
              class="topbar-token-input"
              type="password"
              autocomplete="off"
              placeholder="Bearer / X-API-Token"
              @keydown.enter="saveApiToken"
            />
            <div class="topbar-token-actions">
              <button class="topbar-load-btn" type="button" @click="saveApiToken">保存</button>
              <button class="page-btn" type="button" @click="clearApiToken">清除</button>
            </div>
          </div>
        </div>
        <button class="theme-toggle" @click="toggleTheme" :title="themeLabel">
          <AppIcon :name="themeIcon" :size="15" />
        </button>
      </div>

      <div
        v-if="store.initBusy || store.initError || !store.backendReady"
        class="startup-banner"
        :class="{ error: store.initError || !store.backendReady }"
      >
        <span class="startup-banner-dot" :class="{ busy: store.initBusy }"></span>
        <span>{{ startupStatusText }}</span>
      </div>

      <!-- Content + AI Panel (docked mode) -->
      <div class="content-with-ai">
        <div class="content-area">
          <FilterBar />
          <DataTable />
        </div>
        <template v-if="showAiPanel && !aiFloating">
          <div
            class="ai-resize-handle"
            title="拖动调整 AI 窗口宽度"
            @mousedown="startAiResize"
          />
          <AiPanel
            ref="aiPanelRef"
            :open="showAiPanel"
            :floating="false"
            :prefill-text="pendingAiText"
            :style="{ width: `${aiPanelWidth}px` }"
            @close="showAiPanel = false"
            @open-config="showAiConfig = true"
            @prefill-consumed="pendingAiText = ''"
            @toggle-pin="aiFloating = true"
          />
        </template>
      </div>

      <StatusBar
        :sidebar-collapsed="pluginSidebarCollapsed"
        @toggle-sidebar="pluginSidebarCollapsed = !pluginSidebarCollapsed"
        @open-log="showLogPanel = true"
      />
    </div>

    <!-- AI Panel floating window -->
    <div
      v-if="showAiPanel && aiFloating"
      class="ai-float-wrapper"
      :style="{ left: aiFloatX + 'px', top: aiFloatY + 'px', width: aiFloatWidth + 'px', height: aiFloatHeight + 'px' }"
    >
      <AiPanel
        ref="aiPanelRef"
        :open="showAiPanel"
        :floating="true"
        :prefill-text="pendingAiText"
        style="width: 100%; height: 100%;"
        @close="showAiPanel = false"
        @open-config="showAiConfig = true"
        @prefill-consumed="pendingAiText = ''"
        @toggle-pin="aiFloating = false"
        @drag-start="startAiFloat"
      />
      <!-- Resize handle (bottom-right corner) -->
      <div class="ai-float-resize-corner" @mousedown.stop="startFloatResize" />
    </div>

    <!-- AI Config Modal -->
    <AiConfigModal
      v-if="showAiConfig"
      @close="onConfigClose"
      @config-changed="onConfigChanged"
    />

    <SymbolManagerModal
      v-if="showSymbolManager"
      @close="showSymbolManager = false"
    />

    <LogPanel
      v-if="showLogPanel"
      :show="showLogPanel"
      @close="showLogPanel = false"
    />

    <SystemSettingsModal
      v-if="showSystemSettings"
      @close="showSystemSettings = false"
      @saved="onSystemSettingsSaved"
    />

    <RuleCenterModal
      v-if="showRuleCenter"
      @close="showRuleCenter = false"
      @packages-changed="store.fetchPlugins('all')"
    />

    <NotepadPanel
      :show="showNotepad"
      @close="showNotepad = false"
    />

    <FindingsPanel
      :show="showFindings"
      @close="showFindings = false"
    />

    <PluginParamsModal
      :show="store.pluginArgsModal.show"
      :plugin-name="store.pluginArgsModal.pluginName"
      :engine-id="store.pluginArgsModal.engineId"
      :args="store.pluginArgsModal.args"
      :global-args="store.pluginArgsModal.globalArgsCopy || store.globalArgs"
      @confirm="store.runPluginWithParams"
      @cancel="store.pluginArgsModal.show = false"
    />

    <ConfirmDialog />

    <!-- Global Args Panel -->
    <ArgsPanel
      :show="store.showArgsPanel"
      :engine-id="store.selectedEngine"
      :model-value="store.globalArgs"
      @update:model-value="store.saveGlobalArgs"
      @close="store.showArgsPanel = false"
    />
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, defineAsyncComponent } from 'vue'
import { useAppStore } from './stores/app'
import { listImages, getApiToken, setApiToken } from './api'
import AppIcon from './components/AppIcon.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'
import { useEscClose } from './composables/useEscClose'
import AppSidebar from './components/AppSidebar.vue'
import AiPanel from './components/AiPanel.vue'
import DataTable from './components/DataTable.vue'
import FilterBar from './components/FilterBar.vue'
import StatusBar from './components/StatusBar.vue'
import NotepadPanel from './components/NotepadPanel.vue'
import FindingsPanel from './components/FindingsPanel.vue'
import { useFindingsStore } from './stores/findings'
import PluginParamsModal from './components/PluginParamsModal.vue'
import ArgsPanel from './components/ArgsPanel.vue'

// Only mounted behind an explicit user action (v-if below), so loading their
// code on demand keeps them out of the initial bundle.
const AiConfigModal = defineAsyncComponent(() => import('./components/AiConfigModal.vue'))
const SymbolManagerModal = defineAsyncComponent(() => import('./components/SymbolManagerModal.vue'))
const LogPanel = defineAsyncComponent(() => import('./components/LogPanel.vue'))
const SystemSettingsModal = defineAsyncComponent(
  () => import('./components/SystemSettingsModal.vue'),
)
const RuleCenterModal = defineAsyncComponent(() => import('./components/RuleCenterModal.vue'))

const store = useAppStore()
const findingsStore = useFindingsStore()
const localImagePath = ref('')

// Findings are stored per image; follow whatever image is active.
watch(
  () => [store.selectedEngine, store.imagePath],
  ([engine, path]) => findingsStore.setImage(path || '', engine),
  { immediate: true },
)
const currentTheme = ref('dark')
const showDropdown = ref(false)
const showAiPanel = ref(false)
const showAiConfig = ref(false)
const showSymbolManager = ref(false)
const showLogPanel = ref(false)
const showSystemSettings = ref(false)
const showRuleCenter = ref(false)
const showNotepad = ref(false)
const showFindings = ref(false)
const showTokenMenu = ref(false)
const localApiToken = ref(getApiToken())
const tokenWrapRef = ref(null)

function saveApiToken() {
  setApiToken(localApiToken.value)
  localApiToken.value = getApiToken()
  showTokenMenu.value = false
  store.pushMessage(localApiToken.value ? 'API Token 已保存到本机' : 'API Token 已清空', 'success')
}

function clearApiToken() {
  setApiToken('')
  localApiToken.value = ''
  store.pushMessage('API Token 已清除', 'info')
}

useEscClose(() => showTokenMenu.value, () => { showTokenMenu.value = false })
useEscClose(() => showDropdown.value, () => { showDropdown.value = false })

function onGlobalKeydown(e) {
  // Ctrl/Cmd+K works even while typing (standard command-palette behavior).
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    window.dispatchEvent(new CustomEvent('zero:focus-plugin-search'))
    return
  }
  const t = e.target
  const inField = t && (
    t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable
  )
  if (inField) return
  if (e.key === '/') {
    e.preventDefault()
    window.dispatchEvent(new CustomEvent('zero:focus-filter'))
  }
}

function onDocClickToken(e) {
  if (!showTokenMenu.value) return
  if (tokenWrapRef.value && !tokenWrapRef.value.contains(e.target)) {
    showTokenMenu.value = false
  }
}
const aiPanelRef = ref(null)
const dumpFiles = ref([])
const aiPanelWidth = ref(420)
const pendingAiText = ref('')
const PLUGIN_SIDEBAR_KEY = 'zero-sidebar-collapsed'
const pluginSidebarCollapsed = ref(localStorage.getItem(PLUGIN_SIDEBAR_KEY) === 'true')

watch(pluginSidebarCollapsed, (collapsed) => {
  localStorage.setItem(PLUGIN_SIDEBAR_KEY, String(collapsed))
})

// ── Floating AI window state ──────────────────────────────────────
const AI_FLOAT_KEY = 'zero-ai-float'
const aiFloating = ref(false)
const aiFloatX = ref(100)
const aiFloatY = ref(80)
const aiFloatWidth = ref(460)
const aiFloatHeight = ref(620)

// Restore float state from localStorage
;(function restoreFloatState() {
  try {
    const saved = JSON.parse(localStorage.getItem(AI_FLOAT_KEY) || '{}')
    if (saved.floating != null) aiFloating.value = !!saved.floating
    if (saved.x != null) aiFloatX.value = saved.x
    if (saved.y != null) aiFloatY.value = saved.y
    if (saved.w != null) aiFloatWidth.value = saved.w
    if (saved.h != null) aiFloatHeight.value = saved.h
  } catch {}
})()

function _saveFloatState() {
  localStorage.setItem(AI_FLOAT_KEY, JSON.stringify({
    floating: aiFloating.value,
    x: aiFloatX.value, y: aiFloatY.value,
    w: aiFloatWidth.value, h: aiFloatHeight.value,
  }))
}

watch(aiFloating, _saveFloatState)

// Drag the floating window
let _floatDragging = false
let _floatDragStartX = 0, _floatDragStartY = 0
let _floatOriginX = 0, _floatOriginY = 0

function startAiFloat(e) {
  if (!aiFloating.value) return
  _floatDragging = true
  _floatDragStartX = e.clientX
  _floatDragStartY = e.clientY
  _floatOriginX = aiFloatX.value
  _floatOriginY = aiFloatY.value
  document.body.classList.add('ai-float-dragging')
  document.addEventListener('mousemove', _onFloatDragMove)
  document.addEventListener('mouseup', _stopFloatDrag)
  e.preventDefault()
}

function _onFloatDragMove(e) {
  if (!_floatDragging) return
  aiFloatX.value = Math.max(0, _floatOriginX + e.clientX - _floatDragStartX)
  aiFloatY.value = Math.max(0, _floatOriginY + e.clientY - _floatDragStartY)
}

function _stopFloatDrag() {
  _floatDragging = false
  document.body.classList.remove('ai-float-dragging')
  document.removeEventListener('mousemove', _onFloatDragMove)
  document.removeEventListener('mouseup', _stopFloatDrag)
  _saveFloatState()
}

// Resize the floating window (bottom-right corner)
let _floatResizing = false
let _floatResizeStartX = 0, _floatResizeStartY = 0
let _floatResizeOriginW = 0, _floatResizeOriginH = 0

function startFloatResize(e) {
  _floatResizing = true
  _floatResizeStartX = e.clientX
  _floatResizeStartY = e.clientY
  _floatResizeOriginW = aiFloatWidth.value
  _floatResizeOriginH = aiFloatHeight.value
  document.body.classList.add('ai-float-dragging')
  document.addEventListener('mousemove', _onFloatResizeMove)
  document.addEventListener('mouseup', _stopFloatResize)
  e.preventDefault()
}

function _onFloatResizeMove(e) {
  if (!_floatResizing) return
  aiFloatWidth.value = Math.max(320, _floatResizeOriginW + e.clientX - _floatResizeStartX)
  aiFloatHeight.value = Math.max(300, _floatResizeOriginH + e.clientY - _floatResizeStartY)
}

function _stopFloatResize() {
  _floatResizing = false
  document.body.classList.remove('ai-float-dragging')
  document.removeEventListener('mousemove', _onFloatResizeMove)
  document.removeEventListener('mouseup', _stopFloatResize)
  _saveFloatState()
}

const AI_PANEL_WIDTH_KEY = 'zero-ai-panel-width'
const AI_PANEL_MIN_WIDTH = 320
const AI_PANEL_MAX_WIDTH = 800

let resizingAiPanel = false
let resizeStartX = 0
let resizeStartWidth = 420
let onWindowResize = null

const themes = ['dark', 'light', 'auto']
const themeLabels = {
  dark: '当前：深色主题，点击切换到浅色',
  light: '当前：浅色主题，点击切换到跟随系统',
  auto: '当前：跟随系统，点击切换到深色',
}
const themeIcons = {
  dark: 'moon',
  light: 'sun',
  auto: 'monitor',
}
// Only meaningful while currentTheme === 'auto'.
const prefersLightQuery = window.matchMedia('(prefers-color-scheme: light)')

const themeLabel = computed(() => themeLabels[currentTheme.value])
const themeIcon = computed(() => themeIcons[currentTheme.value])
const logErrorCount = computed(
  () => store.messages.filter((message) => message.severity === 'error').length,
)
const symbolProgressOffset = computed(() => {
  const progress = Math.min(100, Math.max(0, Number(store.symbolDownloadProgress) || 0))
  return 56.55 * (1 - progress / 100)
})
const symbolDownloadTitle = computed(() => {
  if (store.symbolDownloadStage === 'detecting') return '正在识别 Linux 内核'
  if (store.symbolDownloadStage === 'matching') return '正在匹配内核符号表'
  if (store.symbolDownloadProgress != null) {
    return `正在下载符号表：${Math.round(store.symbolDownloadProgress)}%`
  }
  return '正在下载符号表'
})
const activeEngine = computed(() => {
  return store.availableEngines.find(engine => engine.engine_id === store.selectedEngine)
})
const systemDisplayName = computed(() => {
  const osFamily = store.engineStates?.[store.selectedEngine]?.osFamily
  if (osFamily) return String(osFamily).toUpperCase()
  return activeEngine.value?.display_name || store.selectedEngine || 'ZERO'
})
const compactPluginName = computed(() => {
  const plugin = String(store.currentPlugin || '').trim()
  if (!plugin) return '未选择插件'
  const lastPart = plugin.split('.').filter(Boolean).pop() || plugin
  return lastPart.toLowerCase()
})
const startupStatusText = computed(() => {
  if (store.initBusy) return '正在连接后端并加载插件目录...'
  if (store.initError) return `初始化失败: ${store.initError}`
  if (!store.backendReady) return '后端未连接，请确认 FastAPI 服务已启动'
  return ''
})

function applyTheme() {
  // The data-theme attribute always carries a *resolved* value; 'auto' lives
  // only in state/localStorage (the index.html anti-flash script mirrors this).
  const resolved = currentTheme.value === 'auto'
    ? (prefersLightQuery.matches ? 'light' : 'dark')
    : currentTheme.value
  document.documentElement.setAttribute('data-theme', resolved)
}

function onSystemThemeChange() {
  if (currentTheme.value === 'auto') applyTheme()
}

function toggleTheme() {
  const idx = themes.indexOf(currentTheme.value)
  currentTheme.value = themes[(idx + 1) % themes.length]
  applyTheme()
  localStorage.setItem('zero-theme', currentTheme.value)
}

function getAiPanelMaxWidth() {
  const containerWidth = document.querySelector('.content-with-ai')?.clientWidth || window.innerWidth
  const ratioCap = Math.floor(containerWidth * 0.7)
  return Math.max(AI_PANEL_MIN_WIDTH, Math.min(AI_PANEL_MAX_WIDTH, ratioCap))
}

function clampAiPanelWidth(width) {
  const maxWidth = getAiPanelMaxWidth()
  return Math.min(Math.max(Math.trunc(width), AI_PANEL_MIN_WIDTH), maxWidth)
}

function stopAiResize() {
  if (!resizingAiPanel) return
  resizingAiPanel = false
  document.body.classList.remove('ai-resizing-ai')
  document.removeEventListener('mousemove', onAiResizeMove)
  document.removeEventListener('mouseup', stopAiResize)
  localStorage.setItem(AI_PANEL_WIDTH_KEY, String(aiPanelWidth.value))
}

function onAiResizeMove(e) {
  if (!resizingAiPanel) return
  const delta = resizeStartX - e.clientX
  aiPanelWidth.value = clampAiPanelWidth(resizeStartWidth + delta)
}

function startAiResize(e) {
  if (window.matchMedia('(max-width: 960px)').matches) return
  if (!showAiPanel.value) return
  resizingAiPanel = true
  resizeStartX = e.clientX
  resizeStartWidth = aiPanelWidth.value
  document.body.classList.add('ai-resizing-ai')
  document.addEventListener('mousemove', onAiResizeMove)
  document.addEventListener('mouseup', stopAiResize)
  e.preventDefault()
}

function selectImage(file) {
  localImagePath.value = file.path
  showDropdown.value = false
}

function doLoadImage() {
  showDropdown.value = false
  if (localImagePath.value.trim()) {
    store.loadImage(localImagePath.value.trim())
  }
}

async function fetchDumpFiles() {
  try {
    const data = await listImages()
    dumpFiles.value = data.files || []
  } catch (e) {
    console.error('Failed to list dump files:', e)
    store.pushMessage('获取 dumps/ 镜像列表失败: ' + (e?.message || e), 'warning')
  }
}

function onConfigClose() {
  showAiConfig.value = false
}

function onConfigChanged() {
  // Refresh AI panel config display
  if (aiPanelRef.value) {
    aiPanelRef.value.loadConfig()
  }
}

function onSystemSettingsSaved() {
  store.pushMessage('系统运行设置已保存并立即应用', 'success')
}

function handleSendToAiEvent(event) {
  pendingAiText.value = event?.detail?.text || ''
  if (!showAiPanel.value) {
    showAiPanel.value = true
  }
}

function handleClickOutside(e) {
  if (!e.target.closest('.image-select-wrapper')) {
    showDropdown.value = false
  }
}

function openRuleCenter() {
  showRuleCenter.value = true
}

watch(() => store.imagePath, (path) => {
  localImagePath.value = path || ''
})

onMounted(async () => {
  // Restore saved theme (the index.html inline script already applied a
  // resolved value pre-mount; this syncs component state and re-applies).
  const saved = localStorage.getItem('zero-theme')
  if (saved && themes.includes(saved)) {
    currentTheme.value = saved
  }
  applyTheme()
  prefersLightQuery.addEventListener('change', onSystemThemeChange)

  document.addEventListener('click', handleClickOutside)
  document.addEventListener('click', onDocClickToken)
  document.addEventListener('keydown', onGlobalKeydown)
  window.addEventListener('zero:send-to-ai', handleSendToAiEvent)
  window.addEventListener('zero:open-rule-center', openRuleCenter)

  const savedWidth = Number(localStorage.getItem(AI_PANEL_WIDTH_KEY))
  if (Number.isFinite(savedWidth) && savedWidth > 0) {
    aiPanelWidth.value = clampAiPanelWidth(savedWidth)
  }

  aiPanelWidth.value = clampAiPanelWidth(aiPanelWidth.value)

  onWindowResize = () => {
    aiPanelWidth.value = clampAiPanelWidth(aiPanelWidth.value)
  }
  window.addEventListener('resize', onWindowResize)

  await Promise.all([
    store.init(),
    fetchDumpFiles(),
  ])

  if (store.imagePath) {
    localImagePath.value = store.imagePath
  }
})

onBeforeUnmount(() => {
  prefersLightQuery.removeEventListener('change', onSystemThemeChange)
  document.removeEventListener('click', handleClickOutside)
  document.removeEventListener('click', onDocClickToken)
  document.removeEventListener('keydown', onGlobalKeydown)
  window.removeEventListener('zero:send-to-ai', handleSendToAiEvent)
  window.removeEventListener('zero:open-rule-center', openRuleCenter)
  stopAiResize()
  _stopFloatDrag()
  _stopFloatResize()
  if (onWindowResize) {
    window.removeEventListener('resize', onWindowResize)
  }
})
</script>

<style scoped>
.startup-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 34px;
  padding: 0 16px;
  border-bottom: 1px solid var(--border-subtle);
  background: color-mix(in srgb, var(--accent-glow) 82%, var(--bg-secondary));
  color: var(--text-secondary);
  font-size: 12px;
  flex-shrink: 0;
}

.topbar-log-btn {
  position: relative;
}

.symbol-download-progress {
  height: 28px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  color: var(--accent-bright);
  font-family: var(--font-mono);
  font-size: 9px;
}

.symbol-progress-ring {
  width: 22px;
  height: 22px;
  overflow: visible;
  transform: rotate(-90deg);
}

.symbol-progress-track,
.symbol-progress-value {
  fill: none;
  stroke-width: 2.5;
}

.symbol-progress-track {
  stroke: var(--border);
}

.symbol-progress-value {
  stroke: var(--accent-bright);
  stroke-linecap: round;
  stroke-dasharray: 56.55;
  transition: stroke-dashoffset 180ms ease;
}

.symbol-progress-ring.indeterminate {
  animation: symbol-ring-spin 900ms linear infinite;
}

.symbol-progress-ring.indeterminate .symbol-progress-value {
  stroke-dasharray: 15 41.55;
  stroke-dashoffset: 0 !important;
}

.symbol-progress-text {
  min-width: 25px;
}

@keyframes symbol-ring-spin {
  to { transform: rotate(270deg); }
}

@media (prefers-reduced-motion: reduce) {
  .symbol-progress-ring.indeterminate {
    animation-duration: 1.8s;
  }
}

.topbar-log-error-badge {
  position: absolute;
  top: 1px;
  right: 1px;
  min-width: 14px;
  height: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 3px;
  border: 1px solid var(--bg-secondary);
  border-radius: 999px;
  color: white;
  background: var(--text-error);
  font-family: var(--font-mono);
  font-size: 8px;
  line-height: 1;
}

.startup-banner.error {
  background: color-mix(in srgb, var(--text-error) 12%, var(--bg-secondary));
  color: var(--text-error);
}

.startup-banner-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: currentColor;
  opacity: 0.75;
}

.startup-banner-dot.busy {
  animation: startup-pulse 1s ease-in-out infinite;
}

@keyframes startup-pulse {
  0%, 100% { opacity: 0.35; transform: scale(0.9); }
  50% { opacity: 1; transform: scale(1.1); }
}

.ai-resize-handle {
  width: 8px;
  flex-shrink: 0;
  cursor: col-resize;
  background: linear-gradient(to right, transparent 0, var(--border) 50%, transparent 100%);
  transition: background var(--transition-fast);
}

.ai-resize-handle:hover {
  background: linear-gradient(to right, transparent 0, var(--accent-dim) 50%, transparent 100%);
}

@media (max-width: 960px) {
  .ai-resize-handle {
    display: none;
  }
}

.image-select-wrapper {
  position: relative;
  display: flex;
  align-items: center;
}

/* Chevron inside the input's right edge — makes the dumps/ list discoverable. */
.image-dropdown-toggle {
  position: absolute;
  right: 4px;
  width: 22px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  cursor: pointer;
  transition: color var(--transition-fast), background var(--transition-fast);
}

.image-dropdown-toggle:hover {
  color: var(--text-primary);
  background: var(--bg-hover);
}

.image-dropdown {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  margin-top: 4px;
  min-width: 350px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow-sm);
  z-index: 200;
  overflow: hidden;
}

.image-dropdown-header {
  padding: 8px 12px;
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--text-muted);
  border-bottom: 1px solid var(--border-subtle);
}

.image-dropdown-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 8px 12px;
  background: none;
  border: none;
  color: var(--text-primary);
  font-size: 12px;
  font-family: var(--font-mono);
  cursor: pointer;
  transition: background var(--transition-fast);
  text-align: left;
}

.image-dropdown-item:hover {
  background: var(--accent-glow);
}

.image-dropdown-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.image-dropdown-size {
  flex-shrink: 0;
  margin-left: 12px;
  color: var(--text-muted);
  font-size: 11px;
}

.image-dropdown-empty {
  padding: 12px;
  text-align: center;
  color: var(--text-muted);
  font-size: 12px;
}
</style>
