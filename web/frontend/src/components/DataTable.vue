<template>
  <div class="table-container" ref="containerRef">
    <!-- Loading -->
    <div v-if="store.pluginBusy && !store.hasData" class="plugin-loading">
      <!-- Animated scanner -->
      <div class="scanner-wrapper">
        <div class="scanner-grid">
          <div class="scanner-col" v-for="i in 12" :key="i" :style="{ animationDelay: `${(i - 1) * 0.07}s` }"></div>
        </div>
        <div class="scanner-beam"></div>
        <div class="scanner-glow"></div>
      </div>

      <!-- Text block -->
      <div class="loading-text-block">
        <div class="loading-title">
          <span class="loading-icon">SCAN</span>
          正在分析内存镜像
          <span class="loading-dots">
            <span></span><span></span><span></span>
          </span>
        </div>
        <div class="loading-plugin-name">{{ loadingPluginName }}</div>
        <div class="loading-hint">{{ currentHint }}</div>
      </div>
    </div>

    <!-- Empty state -->
    <div v-else-if="!store.hasData" class="table-empty">
      <div class="table-empty-card">
        <div class="table-empty-title">{{ emptyState.title }}</div>
        <div class="table-empty-hint">{{ emptyState.hint }}</div>
        <button
          v-if="emptyState.actionLabel"
          class="table-empty-btn"
          @click="handleEmptyAction"
        >
          {{ emptyState.actionLabel }}
        </button>
      </div>
    </div>

    <!-- Table (virtualized body for large page sizes) -->
    <table v-else class="data-table">
      <thead>
        <tr>
          <th
            v-for="col in store.columns"
            :key="col"
            :class="{ sorted: store.sortColumn === col }"
            @click="store.toggleSort(col)"
          >
            {{ col }}
            <span v-if="store.sortColumn === col" class="sort-arrow">
              <AppIcon :name="store.sortDesc ? 'chevron-down' : 'chevron-up'" :size="10" />
            </span>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="padTop > 0" class="virtual-pad-row" aria-hidden="true">
          <td
            :colspan="Math.max(store.columns.length, 1)"
            :style="{ height: padTop + 'px', padding: 0, border: 'none' }"
          ></td>
        </tr>
        <tr
          v-for="vr in virtualRows"
          :key="vr.key"
          :data-index="vr.index"
        >
          <td
            v-for="(cell, ci) in store.rows[vr.index]"
            :key="ci"
            :title="String(cell)"
            :class="{ 'cell-selected': selectedCell.row === vr.index && selectedCell.col === ci }"
            @click="setSelectedCell(vr.index, ci)"
            @dblclick="copyCellValue(cell)"
            @contextmenu.prevent.stop="openCellMenu($event, store.columns[ci], cell, vr.index, ci)"
          >
            {{ cell }}
          </td>
        </tr>
        <tr v-if="padBottom > 0" class="virtual-pad-row" aria-hidden="true">
          <td
            :colspan="Math.max(store.columns.length, 1)"
            :style="{ height: padBottom + 'px', padding: 0, border: 'none' }"
          ></td>
        </tr>
      </tbody>
    </table>

    <div
      v-if="menu.visible"
      class="column-context-menu"
      :style="{ left: `${menu.x}px`, top: `${menu.y}px` }"
      @click.stop
    >
      <div class="column-context-menu-header">
        <span class="column-context-col">{{ menu.column }}</span>
        <span class="column-context-value" :title="menu.valueText">{{ menu.valueText }}</span>
      </div>
      <button class="column-context-item" @click="copySelectedData">复制选中数据</button>
      <button class="column-context-item" @click="sendSelectedDataToAi">发送到 AI 窗口</button>
      <div class="column-context-divider"></div>
      <button
        v-for="op in operators"
        :key="op.value"
        class="column-context-item"
        @click="applyColumnFilter(op.value)"
      >
        {{ op.label }}
      </button>
      <div class="column-context-divider"></div>
      <button class="column-context-item" @click="closeMenu">取消</button>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useVirtualizer } from '@tanstack/vue-virtual'
import { useAppStore } from '../stores/app'
import AppIcon from './AppIcon.vue'

const store = useAppStore()
const containerRef = ref(null)

const ROW_HEIGHT = 28
const rowCount = computed(() => store.rows?.length || 0)

const rowVirtualizer = useVirtualizer(
  computed(() => ({
    count: rowCount.value,
    getScrollElement: () => containerRef.value,
    estimateSize: () => ROW_HEIGHT,
    overscan: 12,
  })),
)

const virtualRows = computed(() => rowVirtualizer.value.getVirtualItems())
const padTop = computed(() => {
  const items = virtualRows.value
  return items.length ? items[0].start : 0
})
const padBottom = computed(() => {
  const items = virtualRows.value
  if (!items.length) return 0
  const total = rowVirtualizer.value.getTotalSize()
  const last = items[items.length - 1]
  return Math.max(0, total - last.end)
})

watch(rowCount, () => {
  // Reset scroll when result set is replaced (new plugin / filter page).
  nextTick(() => {
    try {
      rowVirtualizer.value.scrollToOffset(0)
    } catch {
      /* ignore */
    }
  })
})

// ── Loading hints ────────────────────────────────────
const HINTS = [
  '正在遍历进程链表...',
  '解析内核数据结构中...',
  '重建虚拟地址空间...',
  '扫描物理内存页...',
  '提取符号表映射...',
  '关联模块依赖关系...',
  '检测隐藏进程痕迹...',
  '遍历句柄表...',
  '重建网络连接状态...',
  '解析注册表 Hive...',
]
const hintIndex = ref(0)
const currentHint = computed(() => HINTS[hintIndex.value % HINTS.length])
const loadingPluginName = computed(() => store.runningPlugin || store.currentPlugin || 'unknown')
let hintTimer = null
const emptyState = computed(() => {
  if (!store.imageLoaded) {
    return {
      title: '先加载内存镜像',
      hint: '可在顶部输入路径，或直接从 dumps/ 下拉列表选择镜像文件。',
      actionLabel: '',
    }
  }
  if (store.hasFilter) {
    return {
      title: '当前过滤条件没有命中结果',
      hint: '可以清空过滤条件，或调整列名与运算符后重新查询。',
      actionLabel: '清空过滤',
    }
  }
  if (store.currentPlugin) {
    return {
      title: '当前插件没有返回可展示结果',
      hint: '可以切换插件、调整筛选条件，或重新运行当前插件。',
      actionLabel: '刷新视图',
    }
  }
  return {
    title: '选择左侧插件开始分析',
    hint: '支持搜索插件、设置参数，并将结果发送到取证分析助手继续分析。',
    actionLabel: '',
  }
})

const operators = [
  { label: '等于 (=)', value: 'eq' },
  { label: '不等于 (!=)', value: 'ne' },
  { label: '大于 (>)', value: 'gt' },
  { label: '小于 (<)', value: 'lt' },
  { label: '大于等于 (>=)', value: 'ge' },
  { label: '小于等于 (<=)', value: 'le' },
  { label: '包含', value: 'contain' },
  { label: '不包含', value: 'notcontain' },
  { label: '前缀匹配', value: 'startswith' },
  { label: '后缀匹配', value: 'endswith' },
]

const menu = reactive({
  visible: false,
  x: 0,
  y: 0,
  column: '',
  value: '',
  valueText: '',
})

const selectedCell = reactive({
  row: -1,
  col: -1,
})

function closeMenu() {
  menu.visible = false
}

function setSelectedCell(row, col) {
  selectedCell.row = row
  selectedCell.col = col
}

function clearSelectedCell() {
  selectedCell.row = -1
  selectedCell.col = -1
}

function openCellMenu(event, col, cell, row, colIndex) {
  if (store.pluginBusy || !store.hasData) return
  if (!col) return
  setSelectedCell(row, colIndex)
  menu.visible = true
  menu.column = col
  menu.value = cell
  menu.valueText = String(cell ?? '(empty)')
  menu.x = event.clientX
  menu.y = event.clientY

  nextTick(() => {
    const menuEl = containerRef.value?.querySelector('.column-context-menu')
    if (!menuEl) return
    const rect = menuEl.getBoundingClientRect()
    const maxX = window.innerWidth - rect.width - 8
    const maxY = window.innerHeight - rect.height - 8
    menu.x = Math.max(8, Math.min(menu.x, maxX))
    menu.y = Math.max(8, Math.min(menu.y, maxY))
  })
}

function applyColumnFilter(op) {
  if (!menu.column) return
  store.appendFilterCondition(menu.column, op, menu.value)
  closeMenu()
}

function buildSelectedDataText() {
  const plugin = store.currentPlugin || 'unknown'
  const col = menu.column || 'unknown'
  const value = String(menu.value ?? '')
  const rowIndex = selectedCell.row >= 0 ? selectedCell.row + 1 : '?'
  return `插件: ${plugin}\n行: ${rowIndex}\n列: ${col}\n值: ${value}`
}

async function copyCellValue(cell) {
  try {
    await navigator.clipboard.writeText(String(cell ?? ''))
    store.pushMessage('已复制单元格内容', 'success')
  } catch {
    store.pushMessage('复制失败：浏览器未授予剪贴板权限', 'error')
  }
}

async function copySelectedData() {
  try {
    await navigator.clipboard.writeText(buildSelectedDataText())
    store.pushMessage('已复制选中数据', 'success')
  } catch {
    store.pushMessage('复制失败：浏览器未授予剪贴板权限', 'error')
  }
  closeMenu()
}

function handleEmptyAction() {
  if (store.hasFilter) {
    store.setFilter('')
    return
  }
  if (store.currentPlugin) {
    store.fetchResults()
  }
}

function sendSelectedDataToAi() {
  const col = menu.column || 'unknown'
  const value = String(menu.value ?? '')
  const payload = `${col}: ${value}`

  window.dispatchEvent(new CustomEvent('zero:send-to-ai', {
    detail: { text: payload },
  }))
  store.pushMessage('已将选中数据发送到取证分析助手', 'success')
  closeMenu()
}

function onWindowClick(event) {
  const target = event?.target
  if (menu.visible) closeMenu()
  if (!target || !containerRef.value?.contains(target)) return
  if (target.closest('td')) return
  clearSelectedCell()
}

function onWindowKeydown(e) {
  if (e.key === 'Escape' && menu.visible) closeMenu()
}

onMounted(() => {
  window.addEventListener('click', onWindowClick)
  window.addEventListener('keydown', onWindowKeydown)
})

onUnmounted(() => {
  window.removeEventListener('click', onWindowClick)
  window.removeEventListener('keydown', onWindowKeydown)
  clearInterval(hintTimer)
})

watch(() => store.pluginBusy, (busy) => {
  clearInterval(hintTimer)
  if (busy) {
    hintIndex.value = Math.floor(Math.random() * HINTS.length)
    hintTimer = setInterval(() => { hintIndex.value++ }, 2200)
  }
}, { immediate: true })
</script>

<style scoped>
/* ── Plugin loading overlay ──────────────────────────── */
.plugin-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 28px;
  height: 100%;
  min-height: 260px;
  padding: 40px;
  user-select: none;
}

/* ── Scanner visual ────────────────────────────────── */
.scanner-wrapper {
  position: relative;
  width: 180px;
  height: 90px;
}

.scanner-grid {
  display: flex;
  align-items: flex-end;
  gap: 6px;
  width: 100%;
  height: 100%;
}

.scanner-col {
  flex: 1;
  border-radius: 3px 3px 0 0;
  background: linear-gradient(to top, var(--accent-dim), var(--accent-bright));
  opacity: 0.15;
  animation: bar-pulse 1.4s ease-in-out infinite;
}

@keyframes bar-pulse {
  0%, 100% { height: 20%; opacity: 0.15; }
  50%       { height: 85%; opacity: 0.70; }
}

/* Horizontal scan beam */
.scanner-beam {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: linear-gradient(90deg, transparent, var(--accent), transparent);
  border-radius: 1px;
  animation: beam-sweep 1.8s ease-in-out infinite;
  box-shadow: 0 0 8px 2px var(--accent-glow);
}

@keyframes beam-sweep {
  0%   { top: 0%;   opacity: 0; }
  10%  { opacity: 1; }
  90%  { opacity: 1; }
  100% { top: 100%; opacity: 0; }
}

/* Bottom glow */
.scanner-glow {
  position: absolute;
  bottom: 0;
  left: 10%;
  right: 10%;
  height: 12px;
  background: radial-gradient(ellipse, var(--accent-glow) 0%, transparent 70%);
  filter: blur(3px);
  animation: glow-pulse 1.8s ease-in-out infinite;
}

@keyframes glow-pulse {
  0%, 100% { opacity: 0.4; transform: scaleX(0.8); }
  50%       { opacity: 1;   transform: scaleX(1.1); }
}

/* ── Text block ────────────────────────────────────── */
.loading-text-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  text-align: center;
}

.loading-title {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: 0.2px;
}

.loading-icon {
  color: var(--accent);
  font-size: 16px;
  animation: icon-spin 3s linear infinite;
  display: inline-block;
}

@keyframes icon-spin {
  0%   { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

/* Three bouncing dots */
.loading-dots {
  display: inline-flex;
  gap: 3px;
  align-items: flex-end;
  height: 14px;
}

.loading-dots span {
  display: inline-block;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent);
  animation: dot-bounce 1.1s ease-in-out infinite;
}

.loading-dots span:nth-child(2) { animation-delay: 0.18s; }
.loading-dots span:nth-child(3) { animation-delay: 0.36s; }

@keyframes dot-bounce {
  0%, 80%, 100% { transform: translateY(0);    opacity: 0.45; }
  40%            { transform: translateY(-6px); opacity: 1; }
}

.loading-plugin-name {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--accent-bright);
  background: var(--accent-glow);
  padding: 3px 12px;
  border-radius: 20px;
  letter-spacing: 0.3px;
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  border: 1px solid color-mix(in srgb, var(--accent-dim) 30%, transparent);
}

.loading-hint {
  font-size: 11px;
  color: var(--text-muted);
  font-family: var(--font-mono);
  animation: hint-fade 0.5s ease-in-out;
}

.table-empty-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  max-width: 460px;
  padding: 28px 24px;
  background: color-mix(in srgb, var(--bg-elevated) 88%, transparent);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  text-align: center;
}

.table-empty-title {
  color: var(--text-primary);
  font-size: 15px;
  font-weight: 600;
}

.table-empty-hint {
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.table-empty-btn {
  height: 32px;
  padding: 0 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
  color: var(--text-primary);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.table-empty-btn:hover {
  border-color: var(--accent-dim);
  background: var(--accent-glow);
}

@keyframes hint-fade {
  from { opacity: 0; transform: translateY(4px); }
  to   { opacity: 1; transform: translateY(0); }
}
</style>
