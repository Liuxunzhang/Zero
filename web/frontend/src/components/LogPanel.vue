<template>
  <Teleport to="body">
    <div v-if="show" class="log-viewer-overlay" @click.self="emit('close')">
      <section class="log-viewer" role="dialog" aria-modal="true" aria-label="消息日志">
        <header class="log-viewer-header">
          <div class="log-viewer-title">
            <span class="log-viewer-title-icon"><AppIcon name="file-text" :size="14" /></span>
            <span>消息日志</span>
            <span class="log-viewer-count">{{ store.messages.length }} 条</span>
          </div>
          <div class="log-viewer-actions">
            <button
              class="log-action-btn"
              :disabled="!filteredMessages.length"
              title="复制当前筛选结果"
              @click="copyVisibleLogs"
            ><AppIcon name="copy" :size="12" /> 复制</button>
            <button
              class="log-action-btn danger"
              :disabled="!store.messages.length"
              @click="clearLogs"
            ><AppIcon name="trash" :size="12" /> 清空</button>
            <button class="log-close-btn" title="关闭" @click="emit('close')">
              <AppIcon name="x" :size="14" />
            </button>
          </div>
        </header>

        <div class="log-viewer-toolbar">
          <div class="log-search-wrap">
            <AppIcon name="search" :size="13" />
            <input
              ref="searchRef"
              v-model="query"
              class="log-search-input"
              type="search"
              placeholder="搜索日志内容..."
            />
            <button v-if="query" class="log-search-clear" title="清空搜索" @click="query = ''">
              <AppIcon name="x" :size="11" />
            </button>
          </div>

          <div class="log-severity-tabs" aria-label="日志级别筛选">
            <button
              v-for="option in severityOptions"
              :key="option.value"
              class="log-severity-tab"
              :class="[{ active: severity === option.value }, `level-${option.value}`]"
              @click="severity = option.value"
            >
              {{ option.label }}
              <span>{{ severityCounts[option.value] }}</span>
            </button>
          </div>
        </div>

        <div class="log-viewer-meta">
          <span>按最新时间排序</span>
          <span>当前显示 {{ filteredMessages.length }} / {{ store.messages.length }} 条</span>
        </div>

        <div class="log-viewer-body">
          <div
            v-for="entry in filteredMessages"
            :key="entry.key"
            class="log-entry"
            :class="`level-${entry.message.severity || 'info'}`"
          >
            <span class="log-entry-dot"></span>
            <time class="log-entry-time">{{ entry.message.ts }}</time>
            <span class="log-entry-level">{{ severityLabel(entry.message.severity) }}</span>
            <span class="log-entry-text">{{ entry.message.text }}</span>
          </div>

          <div v-if="!filteredMessages.length" class="log-viewer-empty">
            <AppIcon name="file-text" :size="24" />
            <span>{{ store.messages.length ? '没有符合筛选条件的日志' : '暂无日志' }}</span>
          </div>
        </div>

        <footer class="log-viewer-footer">
          <span>保留最近 50 条运行消息</span>
          <span>Esc 关闭</span>
        </footer>
      </section>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import { useAppStore } from '../stores/app'
import { confirmAction } from '../composables/confirm'
import { useEscClose } from '../composables/useEscClose'

const props = defineProps({
  show: Boolean,
})

const emit = defineEmits(['close'])
const store = useAppStore()
const query = ref('')
const severity = ref('all')
const searchRef = ref(null)

const severityOptions = [
  { value: 'all', label: '全部' },
  { value: 'error', label: '错误' },
  { value: 'warning', label: '警告' },
  { value: 'success', label: '成功' },
  { value: 'info', label: '信息' },
]

const severityCounts = computed(() => {
  const counts = { all: store.messages.length, error: 0, warning: 0, success: 0, info: 0 }
  for (const message of store.messages) {
    const level = message.severity || 'info'
    if (level in counts) counts[level] += 1
  }
  return counts
})

const filteredMessages = computed(() => {
  const needle = query.value.trim().toLowerCase()
  return store.messages
    .map((message, index) => ({ message, key: `${message.ts}-${index}` }))
    .filter(({ message }) => {
      const level = message.severity || 'info'
      if (severity.value !== 'all' && level !== severity.value) return false
      if (!needle) return true
      const label = severityLabel(level)
      return `${message.ts} ${level} ${label} ${message.text}`.toLowerCase().includes(needle)
    })
    .reverse()
})

useEscClose(() => props.show, () => emit('close'))

watch(() => props.show, async (visible) => {
  if (!visible) return
  await nextTick()
  searchRef.value?.focus()
})

function severityLabel(level) {
  return {
    error: '错误',
    warning: '警告',
    success: '成功',
    info: '信息',
  }[level] || '信息'
}

function visibleLogsAsText() {
  return [...filteredMessages.value]
    .reverse()
    .map(({ message }) => `[${message.ts}] [${severityLabel(message.severity)}] ${message.text}`)
    .join('\n')
}

async function copyVisibleLogs() {
  const text = visibleLogsAsText()
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()
    document.execCommand('copy')
    textarea.remove()
  }
  store.pushMessage(`已复制 ${filteredMessages.value.length} 条日志`, 'success')
}

async function clearLogs() {
  if (!store.messages.length) return
  const ok = await confirmAction({
    title: '清空消息日志',
    message: `将清空当前保存的 ${store.messages.length} 条运行消息。`,
    confirmText: '清空',
  })
  if (!ok) return
  store.clearMessages()
  query.value = ''
  severity.value = 'all'
}
</script>

<style scoped>
.log-viewer-overlay {
  position: fixed;
  inset: 0;
  z-index: 9000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: var(--overlay-backdrop);
  backdrop-filter: blur(4px);
}

.log-viewer {
  width: min(980px, 96vw);
  height: min(720px, 86vh);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-xl);
}

.log-viewer-header {
  min-height: 52px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 0 16px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-tertiary);
  flex-shrink: 0;
}

.log-viewer-title,
.log-viewer-actions,
.log-action-btn,
.log-close-btn {
  display: inline-flex;
  align-items: center;
}

.log-viewer-title {
  gap: 9px;
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 700;
}

.log-viewer-title-icon {
  width: 26px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--accent-bright);
  background: var(--accent-glow);
  border: 1px solid var(--accent-dim);
  border-radius: var(--radius-sm);
}

.log-viewer-count {
  padding: 2px 7px;
  border-radius: 999px;
  color: var(--text-muted);
  background: var(--bg-elevated);
  border: 1px solid var(--border-subtle);
  font-size: 10px;
  font-weight: 500;
}

.log-viewer-actions {
  gap: 7px;
}

.log-action-btn,
.log-close-btn {
  height: 28px;
  justify-content: center;
  gap: 5px;
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  background: var(--bg-elevated);
  font-size: 11px;
  cursor: pointer;
}

.log-close-btn {
  width: 28px;
  padding: 0;
}

.log-action-btn:hover:not(:disabled),
.log-close-btn:hover {
  color: var(--text-primary);
  border-color: var(--accent-dim);
  background: var(--bg-hover);
}

.log-action-btn.danger:hover:not(:disabled) {
  color: var(--text-error);
  border-color: color-mix(in srgb, var(--text-error) 45%, var(--border));
  background: color-mix(in srgb, var(--text-error) 10%, var(--bg-elevated));
}

.log-action-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.log-viewer-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border-subtle);
  flex-shrink: 0;
}

.log-search-wrap {
  width: min(320px, 36vw);
  height: 32px;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 0 9px;
  color: var(--text-muted);
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}

.log-search-wrap:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-glow);
}

.log-search-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text-primary);
  font-size: 12px;
}

.log-search-clear {
  display: flex;
  padding: 2px;
  border: none;
  color: var(--text-muted);
  background: none;
  cursor: pointer;
}

.log-severity-tabs {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
}

.log-severity-tab {
  height: 28px;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 0 9px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  background: var(--bg-elevated);
  font-size: 11px;
  cursor: pointer;
  white-space: nowrap;
}

.log-severity-tab span {
  opacity: 0.7;
  font-family: var(--font-mono);
  font-size: 10px;
}

.log-severity-tab:hover,
.log-severity-tab.active {
  color: var(--text-primary);
  border-color: var(--accent-dim);
  background: var(--accent-glow);
}

.log-severity-tab.active.level-error { color: var(--text-error); }
.log-severity-tab.active.level-warning { color: var(--text-warning); }
.log-severity-tab.active.level-success { color: var(--text-success); }

.log-viewer-meta,
.log-viewer-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--text-muted);
  font-size: 10px;
  flex-shrink: 0;
}

.log-viewer-meta {
  padding: 7px 16px;
  border-bottom: 1px solid var(--border-subtle);
}

.log-viewer-footer {
  min-height: 34px;
  padding: 0 16px;
  border-top: 1px solid var(--border-subtle);
  background: var(--bg-tertiary);
}

.log-viewer-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 6px 0;
  background: var(--bg-primary);
}

.log-entry {
  display: grid;
  grid-template-columns: 8px 70px 40px minmax(0, 1fr);
  align-items: start;
  gap: 9px;
  padding: 7px 16px;
  color: var(--text-secondary);
  border-left: 2px solid transparent;
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.5;
}

.log-entry:hover {
  background: var(--bg-hover);
}

.log-entry.level-error {
  color: var(--text-error);
  border-left-color: var(--text-error);
  background: color-mix(in srgb, var(--text-error) 5%, transparent);
}

.log-entry.level-warning { color: var(--text-warning); }
.log-entry.level-success { color: var(--text-success); }

.log-entry-dot {
  width: 6px;
  height: 6px;
  margin-top: 5px;
  border-radius: 50%;
  background: currentColor;
  opacity: 0.8;
}

.log-entry-time {
  color: var(--text-muted);
  white-space: nowrap;
}

.log-entry-level {
  font-family: var(--font-sans);
  font-size: 10px;
  font-weight: 600;
  white-space: nowrap;
}

.log-entry-text {
  min-width: 0;
  color: inherit;
  word-break: break-word;
  white-space: pre-wrap;
}

.log-viewer-empty {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: var(--text-muted);
  font-size: 12px;
}

@media (max-width: 760px) {
  .log-viewer-overlay { padding: 10px; }
  .log-viewer { width: 100%; height: 92vh; }
  .log-viewer-toolbar { align-items: stretch; flex-direction: column; }
  .log-search-wrap { width: 100%; }
  .log-severity-tabs { overflow-x: auto; padding-bottom: 2px; }
  .log-entry { grid-template-columns: 8px 64px minmax(0, 1fr); }
  .log-entry-level { display: none; }
  .log-action-btn { width: 28px; padding: 0; font-size: 0; }
}
</style>
