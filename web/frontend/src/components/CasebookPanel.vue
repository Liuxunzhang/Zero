<template>
  <Teleport to="body">
    <div v-if="show" class="casebook-overlay" @click.self="emit('close')">
      <section
        class="casebook-panel"
        role="dialog"
        aria-modal="true"
        aria-label="取证工作簿"
      >
        <header class="casebook-header">
          <div class="casebook-heading">
            <span class="casebook-heading-icon">
              <AppIcon name="notebook" :size="15" />
            </span>
            <div>
              <div class="casebook-title">取证工作簿</div>
              <div class="casebook-subtitle">集中整理证据标记与分析笔记</div>
            </div>
          </div>
          <div class="casebook-header-context">
            <span class="casebook-image" :title="findings.currentImage">
              {{ imageBasename || '未加载镜像' }}
            </span>
            <button class="casebook-close" title="关闭" @click="emit('close')">
              <AppIcon name="x" :size="14" />
            </button>
          </div>
        </header>

        <nav class="casebook-tabs" aria-label="工作簿分类">
          <button
            class="casebook-tab"
            :class="{ active: activeTab === 'findings' }"
            @click="activeTab = 'findings'"
          >
            <AppIcon name="star" :size="13" />
            <span>发现</span>
            <strong>{{ findings.items.length }}</strong>
          </button>
          <button
            class="casebook-tab"
            :class="{ active: activeTab === 'notes' }"
            @click="openNotes"
          >
            <AppIcon name="notebook" :size="13" />
            <span>记事</span>
            <strong>{{ noteText.length }}</strong>
          </button>
          <span class="casebook-tab-hint">
            {{ activeTab === 'findings' ? '右键结果行即可加入发现' : '内容自动保存在当前浏览器' }}
          </span>
        </nav>

        <main v-if="activeTab === 'findings'" class="casebook-body findings-body">
          <div v-if="!findings.items.length" class="casebook-empty">
            <span class="casebook-empty-icon">
              <AppIcon name="star" :size="18" />
            </span>
            <strong>尚未标记发现</strong>
            <p>在结果表格中右键任意行，选择「标记为发现」。</p>
          </div>

          <div v-else class="findings-list">
            <article v-for="(finding, index) in findings.items" :key="finding.id" class="finding-card">
              <div class="finding-index">{{ String(index + 1).padStart(2, '0') }}</div>
              <div class="finding-content">
                <div class="finding-head">
                  <code class="finding-plugin">{{ finding.plugin }}</code>
                  <span class="finding-ts">{{ formatTs(finding.ts) }}</span>
                  <button
                    class="finding-remove"
                    title="删除此发现"
                    @click="removeFinding(finding)"
                  >
                    <AppIcon name="trash" :size="12" />
                  </button>
                </div>
                <div v-if="finding.filterText" class="finding-filter">
                  过滤上下文 <code>{{ finding.filterText }}</code>
                </div>
                <div class="finding-row">
                  <div
                    v-for="(cell, cellIndex) in finding.row"
                    :key="cellIndex"
                    class="finding-cell"
                  >
                    <span class="finding-cell-key">
                      {{ finding.columns[cellIndex] || `#${cellIndex}` }}
                    </span>
                    <span class="finding-cell-val" :title="String(cell)">{{ cell }}</span>
                  </div>
                </div>
                <input
                  class="finding-note"
                  type="text"
                  placeholder="补充说明：为什么可疑、需要验证什么…"
                  :value="finding.note"
                  @change="findings.updateNote(finding.id, $event.target.value)"
                />
              </div>
            </article>
          </div>
        </main>

        <main v-else class="casebook-body notes-body">
          <div class="notes-editor-head">
            <div>
              <strong>分析笔记</strong>
              <span>记录镜像背景、偏移、命令、判断依据与下一步验证</span>
            </div>
            <span class="notes-autosave">自动保存</span>
          </div>
          <textarea
            ref="textareaRef"
            v-model="noteText"
            class="notes-textarea"
            placeholder="从这里开始记录取证思路…"
            spellcheck="false"
            @input="saveNote"
          ></textarea>
        </main>

        <footer class="casebook-footer">
          <template v-if="activeTab === 'findings'">
            <span class="casebook-footer-status">
              已记录 {{ findings.items.length }} 条发现
            </span>
            <div class="casebook-actions">
              <button
                class="casebook-action"
                :disabled="!findings.items.length"
                title="将全部发现发送到取证分析助手做关联分析"
                @click="sendToAi"
              >
                <AppIcon name="send" :size="12" /> 发送到 AI
              </button>
              <button
                class="casebook-action"
                :disabled="!findings.items.length"
                title="合并发现和记事，生成 Markdown 报告"
                @click="downloadReport"
              >
                <AppIcon name="file-text" :size="12" /> 生成报告
              </button>
              <button
                class="casebook-action danger"
                :disabled="!findings.items.length"
                @click="clearFindings"
              >
                <AppIcon name="trash" :size="12" /> 清空发现
              </button>
            </div>
          </template>
          <template v-else>
            <span class="casebook-footer-status">{{ noteStatus }}</span>
            <button
              class="casebook-action danger"
              :disabled="!noteText"
              @click="clearNote"
            >
              <AppIcon name="trash" :size="12" /> 清空记事
            </button>
          </template>
        </footer>
      </section>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import { useFindingsStore } from '../stores/findings'
import { useAppStore } from '../stores/app'
import { confirmAction } from '../composables/confirm'
import { useEscClose } from '../composables/useEscClose'

const props = defineProps({
  show: Boolean,
  storageKey: { type: String, default: 'zero-notepad' },
})

const emit = defineEmits(['close'])
const findings = useFindingsStore()
const store = useAppStore()
const activeTab = ref('findings')
const noteText = ref('')
const textareaRef = ref(null)
const savedAt = ref('')

useEscClose(() => props.show, () => emit('close'))

const imageBasename = computed(() => {
  const path = String(findings.currentImage || '')
  return path.split('/').filter(Boolean).pop() || ''
})

const noteStatus = computed(() => {
  const count = noteText.value.length
  return savedAt.value ? `${count} 字 · 已保存 ${savedAt.value}` : `${count} 字`
})

watch(
  () => props.show,
  async (visible) => {
    if (!visible) return
    loadNote()
    if (!findings.items.length && !noteText.value) activeTab.value = 'findings'
    if (activeTab.value === 'notes') {
      await nextTick()
      textareaRef.value?.focus()
    }
  },
)

watch(activeTab, (tab) => {
  try {
    localStorage.setItem('zero-casebook-tab', tab)
  } catch {}
})

try {
  const savedTab = localStorage.getItem('zero-casebook-tab')
  if (savedTab === 'findings' || savedTab === 'notes') activeTab.value = savedTab
} catch {}

async function openNotes() {
  activeTab.value = 'notes'
  await nextTick()
  textareaRef.value?.focus()
}

function loadNote() {
  try {
    noteText.value = localStorage.getItem(props.storageKey) || ''
  } catch {
    noteText.value = ''
  }
}

function saveNote() {
  try {
    localStorage.setItem(props.storageKey, noteText.value)
    savedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  } catch {
    savedAt.value = '失败'
  }
}

function formatTs(timestamp) {
  return new Date(timestamp).toLocaleString('zh-CN', { hour12: false })
}

async function removeFinding(finding) {
  const confirmed = await confirmAction({
    title: '删除发现',
    message: `将删除来自 ${finding.plugin} 的这条发现记录。`,
    confirmText: '删除',
  })
  if (confirmed) findings.remove(finding.id)
}

async function clearFindings() {
  const confirmed = await confirmAction({
    title: '清空全部发现',
    message: `将删除当前镜像的全部 ${findings.items.length} 条发现，无法恢复。`,
    confirmText: '清空',
  })
  if (confirmed) findings.clear()
}

async function clearNote() {
  const confirmed = await confirmAction({
    title: '清空记事本',
    message: '将删除全部手写取证笔记，无法恢复。',
    confirmText: '清空',
  })
  if (!confirmed) return
  noteText.value = ''
  saveNote()
}

function findingAsText(finding, index) {
  const fields = finding.row
    .map((cell, cellIndex) => `${finding.columns[cellIndex] || '#' + cellIndex}: ${cell}`)
    .join(' | ')
  const parts = [`${index + 1}. [${finding.plugin}] ${fields}`]
  if (finding.filterText) parts.push(`   过滤上下文: ${finding.filterText}`)
  if (finding.note) parts.push(`   备注: ${finding.note}`)
  return parts.join('\n')
}

function sendToAi() {
  const body = findings.items.map(findingAsText).join('\n')
  const text =
    `以下是我在本镜像中标记的 ${findings.items.length} 条可疑发现，请关联分析它们之间的关系、` +
    `评估威胁等级，并给出下一步取证建议：\n\n${body}`
  window.dispatchEvent(new CustomEvent('zero:send-to-ai', { detail: { text } }))
  store.pushMessage(`已将 ${findings.items.length} 条发现发送到取证分析助手`, 'success')
  emit('close')
}

function buildReport() {
  const lines = [
    '# Zero 内存取证分析报告',
    '',
    `- **镜像**: ${findings.currentImage || '(未记录)'}`,
    '- **引擎**: Volatility 3',
    `- **生成时间**: ${new Date().toLocaleString('zh-CN', { hour12: false })}`,
    `- **发现数量**: ${findings.items.length}`,
    '',
    '## 可疑发现',
    '',
  ]
  findings.items.forEach((finding, index) => {
    const escapeCell = (value) => String(value).replace(/\|/g, '\\|')
    lines.push(`### ${index + 1}. ${finding.plugin}`, '')
    lines.push(`- 标记时间: ${formatTs(finding.ts)}`)
    if (finding.filterText) lines.push(`- 过滤上下文: \`${finding.filterText}\``)
    if (finding.note) lines.push(`- 备注: ${finding.note}`)
    lines.push('')
    lines.push(`| ${finding.columns.map(escapeCell).join(' | ')} |`)
    lines.push(`| ${finding.columns.map(() => '---').join(' | ')} |`)
    lines.push(`| ${finding.row.map(escapeCell).join(' | ')} |`, '')
  })
  if (noteText.value.trim()) {
    lines.push('## 分析笔记', '', noteText.value.trim(), '')
  }
  return lines.join('\n')
}

function downloadReport() {
  const stamp = new Date()
  const pad = (value) => String(value).padStart(2, '0')
  const name =
    `zero-report-${stamp.getFullYear()}${pad(stamp.getMonth() + 1)}${pad(stamp.getDate())}-` +
    `${pad(stamp.getHours())}${pad(stamp.getMinutes())}.md`
  const blob = new Blob([buildReport()], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  anchor.click()
  URL.revokeObjectURL(url)
  store.pushMessage(`分析报告已下载: ${name}`, 'success')
}
</script>

<style scoped>
.casebook-overlay {
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

.casebook-panel {
  width: min(860px, 96vw);
  height: min(700px, 88vh);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg-secondary);
  box-shadow: var(--shadow-xl);
}

.casebook-header {
  min-height: 62px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 0 18px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-tertiary);
}

.casebook-heading,
.casebook-header-context,
.casebook-actions {
  display: flex;
  align-items: center;
}

.casebook-heading {
  gap: 10px;
}

.casebook-heading-icon {
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--accent-bright);
  border: 1px solid var(--accent-dim);
  border-radius: var(--radius-sm);
  background: var(--accent-glow);
}

.casebook-title {
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 700;
}

.casebook-subtitle {
  margin-top: 2px;
  color: var(--text-muted);
  font-size: 10px;
}

.casebook-header-context {
  min-width: 0;
  gap: 10px;
}

.casebook-image {
  max-width: 240px;
  overflow: hidden;
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.casebook-close {
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
  cursor: pointer;
}

.casebook-close:hover {
  color: var(--text-primary);
  border-color: var(--accent-dim);
}

.casebook-tabs {
  min-height: 46px;
  display: flex;
  align-items: stretch;
  gap: 2px;
  padding: 0 18px;
  border-bottom: 1px solid var(--border-subtle);
  background: var(--bg-primary);
}

.casebook-tab {
  position: relative;
  min-width: 108px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  color: var(--text-muted);
  border: none;
  background: transparent;
  font-size: 12px;
  cursor: pointer;
}

.casebook-tab::after {
  content: '';
  position: absolute;
  right: 8px;
  bottom: 0;
  left: 8px;
  height: 2px;
  background: transparent;
}

.casebook-tab:hover {
  color: var(--text-primary);
}

.casebook-tab.active {
  color: var(--accent-bright);
}

.casebook-tab.active::after {
  background: var(--accent);
  box-shadow: 0 0 9px var(--accent-glow);
}

.casebook-tab strong {
  min-width: 18px;
  padding: 2px 5px;
  border-radius: 999px;
  background: var(--bg-elevated);
  color: var(--text-secondary);
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 500;
}

.casebook-tab-hint {
  align-self: center;
  margin-left: auto;
  color: var(--text-muted);
  font-size: 10px;
}

.casebook-body {
  flex: 1;
  min-height: 0;
}

.findings-body {
  overflow-y: auto;
  padding: 14px 18px;
}

.casebook-empty {
  height: 100%;
  min-height: 260px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  text-align: center;
}

.casebook-empty-icon {
  width: 42px;
  height: 42px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 12px;
  color: var(--text-warning);
  border: 1px solid var(--border);
  border-radius: 50%;
  background: var(--bg-elevated);
}

.casebook-empty strong {
  color: var(--text-secondary);
  font-size: 13px;
}

.casebook-empty p {
  margin: 7px 0 0;
  font-size: 11px;
}

.findings-list {
  display: flex;
  flex-direction: column;
  gap: 9px;
}

.finding-card {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--bg-primary);
  overflow: hidden;
}

.finding-card:focus-within {
  border-color: var(--accent-dim);
}

.finding-index {
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding-top: 12px;
  color: var(--text-muted);
  border-right: 1px solid var(--border-subtle);
  background: var(--bg-tertiary);
  font-family: var(--font-mono);
  font-size: 9px;
}

.finding-content {
  min-width: 0;
  padding: 10px 12px 11px;
}

.finding-head,
.finding-row,
.finding-cell {
  display: flex;
  align-items: center;
}

.finding-head {
  gap: 8px;
}

.finding-plugin {
  color: var(--accent-bright);
  font-family: var(--font-mono);
  font-size: 12px;
}

.finding-ts {
  color: var(--text-muted);
  font-size: 9px;
}

.finding-remove {
  margin-left: auto;
  display: inline-flex;
  padding: 3px;
  color: var(--text-muted);
  border: none;
  background: none;
  cursor: pointer;
}

.finding-remove:hover {
  color: var(--text-error);
}

.finding-filter {
  margin-top: 5px;
  color: var(--text-muted);
  font-size: 10px;
}

.finding-filter code {
  color: var(--text-secondary);
  font-family: var(--font-mono);
}

.finding-row {
  flex-wrap: wrap;
  gap: 5px 15px;
  margin-top: 8px;
}

.finding-cell {
  max-width: 100%;
  gap: 5px;
  font-size: 11px;
}

.finding-cell-key {
  flex-shrink: 0;
  color: var(--text-muted);
}

.finding-cell-val {
  max-width: 280px;
  overflow: hidden;
  color: var(--text-secondary);
  font-family: var(--font-mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.finding-note {
  width: 100%;
  height: 28px;
  margin-top: 9px;
  padding: 0 8px;
  color: var(--text-primary);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  outline: none;
  background: var(--bg-elevated);
  font-size: 11px;
}

.finding-note:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-glow);
}

.notes-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px 18px;
}

.notes-editor-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.notes-editor-head strong,
.notes-editor-head span {
  display: block;
}

.notes-editor-head strong {
  color: var(--text-primary);
  font-size: 12px;
}

.notes-editor-head span {
  margin-top: 3px;
  color: var(--text-muted);
  font-size: 10px;
}

.notes-editor-head .notes-autosave {
  flex-shrink: 0;
  margin: 0;
  padding: 3px 7px;
  color: var(--text-success);
  border: 1px solid color-mix(in srgb, var(--text-success) 28%, var(--border));
  border-radius: 999px;
  background: color-mix(in srgb, var(--text-success) 8%, transparent);
  font-size: 9px;
}

.notes-textarea {
  flex: 1;
  width: 100%;
  min-height: 0;
  resize: none;
  padding: 15px 15px 15px 49px;
  color: var(--text-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  outline: none;
  background:
    linear-gradient(to right, transparent 39px, var(--border-subtle) 40px, transparent 41px),
    repeating-linear-gradient(
      to bottom,
      transparent 0,
      transparent 25px,
      color-mix(in srgb, var(--border-subtle) 70%, transparent) 26px
    ),
    var(--bg-primary);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 26px;
}

.notes-textarea:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-glow);
}

.notes-textarea::placeholder {
  color: var(--text-muted);
}

.casebook-footer {
  min-height: 54px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 18px;
  border-top: 1px solid var(--border);
  background: var(--bg-tertiary);
}

.casebook-footer-status {
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 10px;
}

.casebook-actions {
  gap: 8px;
}

.casebook-action {
  min-height: 30px;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 0 11px;
  color: var(--text-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
  font-size: 11px;
  cursor: pointer;
}

.casebook-action:hover:not(:disabled) {
  color: var(--accent-bright);
  border-color: var(--accent-dim);
  background: var(--accent-glow);
}

.casebook-action.danger:hover:not(:disabled) {
  color: var(--text-error);
  border-color: var(--text-error);
  background: color-mix(in srgb, var(--text-error) 10%, transparent);
}

.casebook-action:disabled {
  opacity: 0.42;
  cursor: not-allowed;
}

@media (max-width: 680px) {
  .casebook-overlay {
    padding: 8px;
  }

  .casebook-panel {
    width: 100%;
    height: 96vh;
  }

  .casebook-header {
    padding: 0 12px;
  }

  .casebook-image,
  .casebook-tab-hint {
    display: none;
  }

  .casebook-tabs,
  .findings-body,
  .notes-body {
    padding-right: 12px;
    padding-left: 12px;
  }

  .casebook-footer {
    min-height: 62px;
    padding: 7px 12px;
  }

  .casebook-footer-status {
    display: none;
  }

  .casebook-actions {
    width: 100%;
    justify-content: flex-end;
  }
}
</style>
