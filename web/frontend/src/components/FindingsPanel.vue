<template>
  <Teleport to="body">
    <div v-if="show" class="findings-overlay" @click.self="$emit('close')">
      <div class="findings-panel">
        <div class="findings-header">
          <div class="findings-title">
            <span class="findings-icon"><AppIcon name="star" :size="13" /></span>
            <span>取证发现</span>
            <span class="findings-count">{{ findings.items.length }} 条</span>
          </div>
          <button class="findings-close-btn" @click="$emit('close')">关闭</button>
        </div>

        <div class="findings-body">
          <div v-if="!findings.items.length" class="findings-empty">
            暂无标记的发现。<br />
            在结果表格中右键任意行，选择「标记为发现」。
          </div>
          <div v-else class="findings-list">
            <div v-for="f in findings.items" :key="f.id" class="finding-card">
              <div class="finding-head">
                <code class="finding-plugin">{{ f.plugin }}</code>
                <span class="finding-ts">{{ formatTs(f.ts) }}</span>
                <button
                  class="finding-remove"
                  title="删除此发现"
                  @click="removeFinding(f)"
                ><AppIcon name="trash" :size="12" /></button>
              </div>
              <div v-if="f.filterText" class="finding-filter">
                过滤上下文: <code>{{ f.filterText }}</code>
              </div>
              <div class="finding-row">
                <div v-for="(cell, i) in f.row" :key="i" class="finding-cell">
                  <span class="finding-cell-key">{{ f.columns[i] || `#${i}` }}</span>
                  <span class="finding-cell-val" :title="String(cell)">{{ cell }}</span>
                </div>
              </div>
              <input
                class="finding-note"
                type="text"
                placeholder="备注（为什么可疑）..."
                :value="f.note"
                @change="findings.updateNote(f.id, $event.target.value)"
              />
            </div>
          </div>
        </div>

        <div class="findings-footer">
          <span class="findings-image" :title="findings.currentImage">
            {{ imageBasename || '未加载镜像' }}
          </span>
          <div class="findings-actions">
            <button
              class="findings-action-btn"
              :disabled="!findings.items.length"
              title="将全部发现发送到取证分析助手做关联分析"
              @click="sendToAi"
            ><AppIcon name="send" :size="12" /> 发送到 AI</button>
            <button
              class="findings-action-btn"
              :disabled="!findings.items.length"
              title="生成 Markdown 分析报告并下载"
              @click="downloadReport"
            ><AppIcon name="file-text" :size="12" /> 生成报告</button>
            <button
              class="findings-action-btn danger"
              :disabled="!findings.items.length"
              @click="clearAll"
            ><AppIcon name="trash" :size="12" /> 清空</button>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import { useFindingsStore } from '../stores/findings'
import { useAppStore } from '../stores/app'
import { confirmAction } from '../composables/confirm'
import { useEscClose } from '../composables/useEscClose'

const props = defineProps({
  show: Boolean,
})

const emit = defineEmits(['close'])

useEscClose(() => props.show, () => emit('close'))

const findings = useFindingsStore()
const store = useAppStore()

const imageBasename = computed(() => {
  const p = String(findings.currentImage || '')
  return p.split('/').filter(Boolean).pop() || ''
})

function formatTs(ts) {
  return new Date(ts).toLocaleString('zh-CN', { hour12: false })
}

async function removeFinding(f) {
  const ok = await confirmAction({
    title: '删除发现',
    message: `将删除来自 ${f.plugin} 的这条发现记录。`,
    confirmText: '删除',
  })
  if (ok) findings.remove(f.id)
}

async function clearAll() {
  const ok = await confirmAction({
    title: '清空全部发现',
    message: `将删除当前镜像的全部 ${findings.items.length} 条发现，无法恢复。`,
    confirmText: '清空',
  })
  if (ok) findings.clear()
}

function findingAsText(f, index) {
  const fields = f.row
    .map((cell, i) => `${f.columns[i] || '#' + i}: ${cell}`)
    .join(' | ')
  const parts = [`${index + 1}. [${f.plugin}] ${fields}`]
  if (f.filterText) parts.push(`   过滤上下文: ${f.filterText}`)
  if (f.note) parts.push(`   备注: ${f.note}`)
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
  const now = new Date().toLocaleString('zh-CN', { hour12: false })
  const lines = [
    '# Zero 内存取证分析报告',
    '',
    `- **镜像**: ${findings.currentImage || '(未记录)'}`,
    `- **引擎**: Volatility 3`,
    `- **生成时间**: ${now}`,
    `- **发现数量**: ${findings.items.length}`,
    '',
    '## 可疑发现',
    '',
  ]
  findings.items.forEach((f, idx) => {
    lines.push(`### ${idx + 1}. ${f.plugin}`)
    lines.push('')
    lines.push(`- 标记时间: ${formatTs(f.ts)}`)
    if (f.filterText) lines.push(`- 过滤上下文: \`${f.filterText}\``)
    if (f.note) lines.push(`- 备注: ${f.note}`)
    lines.push('')
    const esc = (v) => String(v).replace(/\|/g, '\\|')
    lines.push(`| ${f.columns.map(esc).join(' | ')} |`)
    lines.push(`| ${f.columns.map(() => '---').join(' | ')} |`)
    lines.push(`| ${f.row.map(esc).join(' | ')} |`)
    lines.push('')
  })

  let note = ''
  try {
    note = localStorage.getItem('zero-notepad') || ''
  } catch {}
  if (note.trim()) {
    lines.push('## 分析笔记', '', note.trim(), '')
  }
  return lines.join('\n')
}

function downloadReport() {
  const stamp = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  const name = `zero-report-${stamp.getFullYear()}${pad(stamp.getMonth() + 1)}${pad(stamp.getDate())}-${pad(stamp.getHours())}${pad(stamp.getMinutes())}.md`
  const blob = new Blob([buildReport()], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
  store.pushMessage(`分析报告已下载: ${name}`, 'success')
}
</script>

<style scoped>
.findings-overlay {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: color-mix(in srgb, var(--bg-primary) 52%, transparent);
  z-index: 1000;
}

.findings-panel {
  width: min(760px, 96vw);
  height: min(640px, 88vh);
  display: flex;
  flex-direction: column;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-xl);
  overflow: hidden;
}

.findings-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 48px;
  padding: 0 16px;
  border-bottom: 1px solid var(--border-subtle);
  flex-shrink: 0;
}

.findings-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.findings-icon {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-warning);
}

.findings-count {
  font-size: 11px;
  font-weight: 400;
  color: var(--text-muted);
}

.findings-close-btn {
  height: 28px;
  padding: 0 12px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.findings-close-btn:hover {
  color: var(--text-primary);
  border-color: var(--border-focus);
}

.findings-body {
  flex: 1;
  overflow-y: auto;
  padding: 12px 16px;
}

.findings-empty {
  padding: 48px 16px;
  text-align: center;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.8;
}

.findings-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.finding-card {
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--bg-tertiary);
  padding: 10px 12px;
}

.finding-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.finding-plugin {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--accent-bright);
}

.finding-ts {
  font-size: 10px;
  color: var(--text-muted);
}

.finding-remove {
  margin-left: auto;
  display: flex;
  padding: 3px;
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
}

.finding-remove:hover {
  color: var(--text-error);
}

.finding-filter {
  margin-top: 4px;
  font-size: 11px;
  color: var(--text-muted);
}

.finding-filter code {
  font-family: var(--font-mono);
  color: var(--text-secondary);
}

.finding-row {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  margin-top: 8px;
}

.finding-cell {
  display: flex;
  gap: 5px;
  font-size: 11px;
  max-width: 100%;
}

.finding-cell-key {
  color: var(--text-muted);
  flex-shrink: 0;
}

.finding-cell-val {
  font-family: var(--font-mono);
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 260px;
}

.finding-note {
  width: 100%;
  margin-top: 8px;
  height: 26px;
  padding: 0 8px;
  background: var(--bg-elevated);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font-size: 11px;
  outline: none;
}

.finding-note:focus {
  border-color: var(--border-focus);
}

.findings-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 44px;
  padding: 0 16px;
  border-top: 1px solid var(--border-subtle);
  flex-shrink: 0;
}

.findings-image {
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.findings-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.findings-action-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  height: 28px;
  padding: 0 12px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.findings-action-btn:hover:not(:disabled) {
  color: var(--accent-bright);
  border-color: var(--accent-dim);
  background: var(--accent-glow);
}

.findings-action-btn.danger:hover:not(:disabled) {
  color: var(--text-error);
  border-color: var(--text-error);
  background: color-mix(in srgb, var(--text-error) 10%, transparent);
}

.findings-action-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
</style>
