<template>
  <div class="ai-panel" :class="{ collapsed: !open, floating: floating }">
    <!-- Header — also serves as drag handle when floating -->
    <div
      class="ai-panel-header"
      :class="{ 'ai-drag-handle': floating }"
      @mousedown="floating ? $emit('drag-start', $event) : undefined"
    >
      <div class="ai-panel-title">
        <span class="ai-panel-icon">IR</span>
        <span>取证分析助手</span>
        <span class="ai-panel-model" v-if="modelLabel">{{ modelLabel }}</span>
        <div class="ai-quick-max-group">
          <button
            class="ai-quick-max-btn"
            :class="{ active: tokenIsMax }"
            :disabled="quickUpdating"
            @click="toggleTokenMax"
            title="最大响应 Token 设为 max"
          >输出 {{ tokenIsMax ? 'max' : '默认' }}</button>
          <button
            class="ai-quick-max-btn"
            :class="{ active: rowsIsMax }"
            :disabled="quickUpdating"
            @click="toggleRowsMax"
            title="最大数据行数设为 max"
          >上下文 {{ rowsIsMax ? 'max' : '默认' }}</button>
        </div>
      </div>
      <div class="ai-panel-actions" ref="actionsEl">
        <button class="ai-header-btn" @click="$emit('open-config')" title="取证助手设置">
          设置
        </button>
        <div class="ai-header-dropdown">
          <button
            class="ai-header-btn ai-header-dropdown-trigger"
            :class="{ active: showHistory }"
            @click.stop="toggleHistory"
            title="历史对话"
          >
            历史
          </button>
          <div v-if="showHistory" class="ai-header-dropdown-menu ai-header-dropdown-menu-history">
            <div class="ai-dropdown-head">
              <div>
                <div class="ai-dropdown-title">历史会话</div>
                <div class="ai-dropdown-subtitle">{{ conversations.length }} 条记录</div>
              </div>
              <button class="ai-dropdown-primary-btn" @click.stop="newConversation" title="新建对话">新建</button>
            </div>
            <div v-if="historyLoading" class="ai-dropdown-empty">加载中...</div>
            <div v-else-if="!conversations.length" class="ai-dropdown-empty">暂无历史对话</div>
            <div v-else class="ai-history-list">
              <div
                v-for="conv in conversations"
                :key="conv.id"
                class="ai-history-item"
                :class="{ active: conv.id === conversationId }"
                @click="selectConversation(conv)"
              >
                <div class="ai-history-item-main">
                  <template v-if="renamingId === conv.id">
                    <input
                      class="ai-history-rename-input"
                      v-model="renameText"
                      @click.stop
                      @keydown.enter.prevent="confirmRename(conv)"
                      @keydown.escape="cancelRename"
                      @blur="confirmRename(conv)"
                      autofocus
                    />
                  </template>
                  <template v-else>
                    <span class="ai-history-item-title" :title="conv.title">{{ conv.title }}</span>
                    <span class="ai-history-item-date">{{ formatDate(conv.updated_at) }}</span>
                  </template>
                </div>
                <div class="ai-history-item-actions" @click.stop>
                  <button class="ai-history-action-btn" @click="startRename(conv)" title="重命名">改</button>
                  <button class="ai-history-action-btn ai-history-delete-btn" @click="deleteConv(conv)" title="删除">删</button>
                </div>
              </div>
            </div>
          </div>
        </div>
        <button class="ai-header-btn" @click="clearChat" title="清空对话 / 新建" :disabled="streaming">清空</button>
        <div class="ai-header-dropdown">
          <button
            class="ai-header-btn ai-header-dropdown-trigger"
            :class="{ active: showMemoryPanel }"
            @click.stop="toggleMemoryPanel"
            title="压缩记忆"
          >
            记忆
          </button>
          <div v-if="showMemoryPanel" class="ai-header-dropdown-menu ai-header-dropdown-menu-memory">
            <div class="ai-dropdown-head">
              <div>
                <div class="ai-dropdown-title">压缩记忆</div>
                <div class="ai-dropdown-subtitle">与当前主题同步的临时摘要区</div>
              </div>
              <div class="ai-dropdown-head-actions">
                <button class="ai-dropdown-ghost-btn" @click.stop="loadMemory" title="刷新">刷新</button>
                <button class="ai-dropdown-ghost-btn danger" @click.stop="clearMemory" title="清空压缩记忆" :disabled="streaming">清空</button>
              </div>
            </div>
            <div v-if="memoryLoading" class="ai-dropdown-empty">加载中...</div>
            <div v-else-if="memoryError" class="ai-dropdown-empty">{{ memoryError }}</div>
            <pre v-else-if="memoryText" class="ai-memory-text">{{ memoryText }}</pre>
            <div v-else class="ai-dropdown-empty">暂无压缩记忆</div>
          </div>
        </div>
        <!-- Pin/Float toggle -->
        <button
          class="ai-header-btn"
          @click.stop="$emit('toggle-pin')"
          :title="floating ? '固定到侧边栏' : '悬浮窗口'"
        >{{ floating ? '固定' : '浮动' }}</button>
        <button class="ai-header-btn" @click="$emit('close')" title="关闭面板">
          关闭
        </button>
      </div>
    </div>

    <!-- Prompt selector bar -->
    <div class="ai-prompt-bar">
      <span class="ai-prompt-bar-label">提示词:</span>
      <select class="ai-prompt-select" v-model="activePromptId" @change="onPromptChange">
        <option v-for="p in prompts" :key="p.id" :value="p.id">
          {{ p.name }}{{ p.builtin ? '' : ' (自定义)' }}
        </option>
      </select>
    </div>
    <div class="ai-agent-strip">
      <span
        v-for="item in agentContextItems"
        :key="item.label"
        class="ai-agent-pill"
        :class="{ muted: item.muted }"
      >
        <span class="ai-agent-pill-label">{{ item.label }}</span>
        <span class="ai-agent-pill-value">{{ item.value }}</span>
      </span>
    </div>

    <!-- Messages -->
    <div class="ai-messages" ref="messagesEl">
      <!-- Welcome -->
      <div v-if="!chatMessages.length" class="ai-welcome">
        <div class="ai-welcome-icon">IR</div>
        <div class="ai-welcome-title">内存取证分析助手</div>
        <div class="ai-welcome-text">
          面向进程、网络、注册表、模块和可疑行为证据链。<br/>
          默认引用当前插件输出，优先说明证据、风险和下一步插件。
        </div>
        <div class="ai-quick-actions">
          <button class="ai-quick-btn" @click="sendQuick('基于当前插件输出，按证据强度列出可疑进程、模块、网络连接或持久化痕迹，并说明依据。')" :disabled="streaming">
            分析可疑迹象
          </button>
          <button class="ai-quick-btn" @click="sendQuick('只基于当前插件输出，总结关键取证事实。按字段和值列出证据，不要复述完整表格，不要推测未出现的数据。')" :disabled="streaming">
            提取取证事实
          </button>
          <button class="ai-quick-btn" @click="sendQuick('根据当前结果推荐下一步 Volatility 插件和验证顺序。每一步只写要验证的假设、期望证据和排除条件。')" :disabled="streaming">
            推荐验证路径
          </button>
          <button class="ai-quick-btn" @click="sendQuick('像内存取证智能体一样规划分析：先列已知证据，再给出最小下一步动作，不输出泛泛安全建议。')" :disabled="streaming">
            规划下一步
          </button>
          <button class="ai-quick-btn" @click="sendQuick('根据当前数据生成 Zero 过滤规则，筛选可疑 PID、路径、连接或注册表项，并用 ```filter 代码块输出。')" :disabled="streaming">
            生成过滤规则
          </button>
        </div>
      </div>

      <!-- Chat messages -->
      <div
        v-for="(msg, idx) in chatMessages"
        :key="idx"
        class="ai-message"
        :class="'ai-message-' + msg.role"
      >
        <div class="ai-message-avatar">
          {{ msg.role === 'user' ? '你' : 'IR' }}
        </div>
        <div class="ai-message-body">
          <div class="ai-message-role">{{ msg.role === 'user' ? '你' : '取证分析' }}</div>
          <div
            v-if="msg.role === 'assistant'"
            class="ai-message-content ai-markdown"
            v-html="renderMarkdown(msg.content)"
          />
          <div v-else class="ai-message-content">{{ msg.content }}</div>
          <div v-if="msg.role === 'user'" class="ai-message-meta">
            {{ formatContextMeta(msg.context) }}
          </div>
          <!-- Filter rules extracted from assistant messages -->
          <div v-if="msg.role === 'assistant' && extractFilters(msg.content).length" class="ai-filter-actions">
            <div
              v-for="(rule, ri) in extractFilters(msg.content)"
              :key="ri"
              class="ai-filter-rule"
            >
              <code class="ai-filter-rule-text">{{ rule }}</code>
              <button class="ai-filter-apply-btn" @click="applyFilter(rule)" title="应用此过滤规则">
                应用
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Streaming indicator -->
      <div v-if="streaming" class="ai-message ai-message-assistant">
        <div class="ai-message-avatar">IR</div>
        <div class="ai-message-body">
          <div class="ai-message-role">取证分析</div>
          <div class="ai-message-content ai-markdown" v-html="renderMarkdown(streamBuffer)" />
          <div v-if="!streamBuffer" class="ai-thinking">
            <span class="ai-thinking-dot"></span>
            <span class="ai-thinking-dot"></span>
            <span class="ai-thinking-dot"></span>
          </div>
        </div>
      </div>

      <div v-if="memoryCompressing" class="ai-memory-status">正在更新取证记忆...</div>

      <!-- Error -->
      <div v-if="errorText" class="ai-error">
        <span>错误: {{ errorText }}</span>
      </div>
    </div>

    <!-- Input -->
    <div class="ai-input-area">
      <div class="ai-input-row">
        <label class="ai-context-toggle" title="附带当前插件数据">
          <input type="checkbox" v-model="includeContext" />
          <span class="ai-context-label">附带插件数据</span>
        </label>
        <span class="ai-context-current">{{ liveContextLabel }}</span>
      </div>
      <div class="ai-input-row">
        <textarea
          ref="inputEl"
          class="ai-input"
          v-model="inputText"
          placeholder="输入分析问题..."
          @keydown.enter.exact.prevent="sendMessage"
          rows="1"
          :disabled="streaming"
        />
        <button
          class="ai-send-btn"
          @click="streaming ? abortStream() : sendMessage()"
          :class="{ 'ai-stop-btn': streaming }"
        >
          {{ streaming ? '停止' : '发送' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, watch, onMounted, onBeforeUnmount } from 'vue'
import { marked } from 'marked'
import {
  streamAiChat, getAiConfig, clearAiHistory, clearAiMemory, getAiMemory,
  getAiPrompts, setActivePrompt, saveAiSettings,
  listConversations, getConversation, deleteConversation,
  renameConversation, loadConversation,
} from '../api'
import { useAppStore } from '../stores/app'

const emit = defineEmits(['close', 'open-config', 'prefill-consumed', 'toggle-pin', 'drag-start'])

const props = defineProps({
  open: { type: Boolean, default: false },
  prefillText: { type: String, default: '' },
  floating: { type: Boolean, default: false },
})

const store = useAppStore()

// State
const chatMessages = ref([])
const inputText = ref('')
const streaming = ref(false)
const streamBuffer = ref('')
const errorText = ref('')
const includeContext = ref(true)
const modelName = ref('')
const apiName = ref('')
const messagesEl = ref(null)
const inputEl = ref(null)

// Prompt selector
const prompts = ref([])
const activePromptId = ref('default')
const activeDropdown = ref('')
const actionsEl = ref(null)
const memoryText = ref('')
const memoryLoading = ref(false)
const memoryError = ref('')
const memoryCompressing = ref(false)
const tokenIsMax = ref(false)
const rowsIsMax = ref(false)
const quickUpdating = ref(false)
const tokenDefaultValue = ref(4096)
const rowsDefaultValue = ref(500)

// Conversation history
const conversationId = ref(null)        // current conversation id
const conversations = ref([])
const historyLoading = ref(false)
const renamingId = ref(null)
const renameText = ref('')
const showMemoryPanel = computed(() => activeDropdown.value === 'memory')
const showHistory = computed(() => activeDropdown.value === 'history')

let currentAbort = null

// Configure marked
marked.setOptions({ breaks: true, gfm: true })

function renderMarkdown(text) {
  if (!text) return ''
  try { return marked.parse(text) }
  catch { return text }
}

function resolveApiName(cfg) {
  if (cfg.provider && cfg.provider !== 'config.py') return cfg.provider
  try {
    if (cfg.base_url) return new URL(cfg.base_url).host
  } catch {
    // ignore malformed URL
  }
  return cfg.provider || 'API'
}

function syncQuickMaxState(settings) {
  const tokenMax = settings?.ai_max_tokens === 'max'
  const rowsMax = settings?.ai_context_max_rows === 'max'
  if (!tokenMax) {
    const n = Number(settings?.ai_max_tokens)
    if (Number.isFinite(n) && n > 0) tokenDefaultValue.value = Math.trunc(n)
  }
  if (!rowsMax) {
    const n = Number(settings?.ai_context_max_rows)
    if (Number.isFinite(n) && n > 0) rowsDefaultValue.value = Math.trunc(n)
  }
  tokenIsMax.value = tokenMax
  rowsIsMax.value = rowsMax
}

const modelLabel = computed(() => {
  const left = apiName.value
  const right = modelName.value
  if (left && right) return `${left} / ${right}`
  return right || left
})

const liveContextLabel = computed(() => {
  if (!includeContext.value) return '不附带插件数据'
  if (!store.currentPlugin) return '将附带插件数据: 无'
  return `将附带插件数据: ${store.currentPlugin}`
})

const activePromptName = computed(() => {
  const prompt = prompts.value.find(p => p.id === activePromptId.value)
  return prompt?.name || '默认取证分析'
})

const agentContextItems = computed(() => {
  const totalRows = Number(store.totalRows || 0)
  const visibleRows = Number(store.visibleRows || 0)
  const imageName = store.imagePath ? String(store.imagePath).split(/[\\/]/).pop() : '未加载镜像'
  return [
    { label: '模式', value: activePromptName.value },
    { label: '引擎', value: store.selectedEngine || 'vol3' },
    { label: '镜像', value: imageName, muted: !store.imagePath },
    { label: '插件', value: store.currentPlugin || '未选择', muted: !store.currentPlugin },
    { label: '上下文', value: totalRows ? `${visibleRows}/${totalRows} 行` : '暂无结果', muted: !totalRows },
  ]
})

/**
 * Extract filter rules from ```filter ... ``` blocks in AI response.
 */
function extractFilters(text) {
  if (!text) return []
  const rules = []
  const regex = /```filter\s*\n([\s\S]*?)```/g
  let m
  while ((m = regex.exec(text)) !== null) {
    const rule = m[1].trim()
    if (rule) rules.push(rule)
  }
  return rules
}

function applyFilter(rule) {
  store.setFilter(rule)
  store.pushMessage(`过滤规则已应用: ${rule}`, 'success')
}

function scrollToBottom() {
  nextTick(() => {
    if (messagesEl.value) {
      messagesEl.value.scrollTop = messagesEl.value.scrollHeight
    }
  })
}

function sendQuick(text) {
  inputText.value = text
  sendMessage()
}

function sendMessage() {
  const msg = inputText.value.trim()
  if (!msg || streaming.value) return

  errorText.value = ''
  const contextMeta = {
    include: includeContext.value,
    plugin: includeContext.value ? (store.currentPlugin || '') : '',
  }
  chatMessages.value.push({ role: 'user', content: msg, context: contextMeta })
  inputText.value = ''
  streaming.value = true
  streamBuffer.value = ''
  memoryCompressing.value = false
  scrollToBottom()

  currentAbort = streamAiChat(
    msg,
    includeContext.value,
    (chunk) => {
      streamBuffer.value += chunk
      scrollToBottom()
    },
    (returnedConvId) => {
      chatMessages.value.push({ role: 'assistant', content: streamBuffer.value })
      if (returnedConvId && !conversationId.value) {
        conversationId.value = returnedConvId
      }
      streamBuffer.value = ''
      streaming.value = false
      memoryCompressing.value = false
      currentAbort = null
      // Refresh sidebar if open
      if (showHistory.value) loadConversations()
      scrollToBottom()
    },
    (err) => {
      if (streamBuffer.value) {
        chatMessages.value.push({ role: 'assistant', content: streamBuffer.value })
      }
      errorText.value = err
      streamBuffer.value = ''
      streaming.value = false
      memoryCompressing.value = false
      currentAbort = null
      scrollToBottom()
    },
    (status) => {
      if (status === 'compressing') {
        memoryCompressing.value = true
      } else if (status === 'done') {
        memoryCompressing.value = false
        if (showMemoryPanel.value) loadMemory()
      } else if (status === 'failed') {
        memoryCompressing.value = false
      }
      scrollToBottom()
    },
    store.selectedEngine || 'vol3',
    conversationId.value,
  )
}

function formatContextMeta(context) {
  if (!context || !context.include) return '附带数据: 关闭'
  return `附带数据: ${context.plugin || '无'}`
}

function abortStream() {
  if (currentAbort) {
    currentAbort.abort()
    if (streamBuffer.value) {
      chatMessages.value.push({ role: 'assistant', content: streamBuffer.value + '\n\n*(已中断)*' })
    }
    streamBuffer.value = ''
    streaming.value = false
    memoryCompressing.value = false
    currentAbort = null
  }
}

async function clearChat() {
  chatMessages.value = []
  errorText.value = ''
  streamBuffer.value = ''
  memoryCompressing.value = false
  conversationId.value = null
  try { await clearAiHistory() } catch { /* ignore */ }
}

async function clearMemory() {
  try {
    await clearAiMemory()
    memoryText.value = ''
    memoryError.value = ''
    store.pushMessage('AI 压缩记忆已清空', 'success')
  } catch {
    store.pushMessage('清空 AI 压缩记忆失败', 'error')
  }
}

async function loadMemory() {
  memoryLoading.value = true
  memoryError.value = ''
  try {
    const data = await getAiMemory()
    memoryText.value = data.compressed_memory || ''
  } catch (e) {
    memoryError.value = e.message || '加载压缩记忆失败'
  } finally {
    memoryLoading.value = false
  }
}

function toggleMemoryPanel() {
  activeDropdown.value = activeDropdown.value === 'memory' ? '' : 'memory'
  if (showMemoryPanel.value) {
    loadMemory()
  }
}

async function onPromptChange() {
  try {
    await setActivePrompt(activePromptId.value)
  } catch (e) {
    console.error('Failed to set prompt:', e)
  }
}

async function loadConfig() {
  try {
    const [cfg, promptData] = await Promise.all([
      getAiConfig(),
      getAiPrompts(),
    ])
    modelName.value = cfg.model || ''
    apiName.value = resolveApiName(cfg)
    activePromptId.value = cfg.active_prompt_id || 'default'
    prompts.value = promptData.prompts || []
    syncQuickMaxState(cfg.ai_settings || {})
  } catch { /* ignore */ }
}

async function toggleTokenMax() {
  if (quickUpdating.value) return
  quickUpdating.value = true
  const payload = {
    ai_max_tokens: tokenIsMax.value ? tokenDefaultValue.value : 'max',
  }
  try {
    const data = await saveAiSettings(payload)
    syncQuickMaxState(data.settings || {})
  } catch {
    store.pushMessage('切换 Max 模式失败', 'error')
  } finally {
    quickUpdating.value = false
  }
}

async function toggleRowsMax() {
  if (quickUpdating.value) return
  quickUpdating.value = true
  const payload = {
    ai_context_max_rows: rowsIsMax.value ? rowsDefaultValue.value : 'max',
  }
  try {
    const data = await saveAiSettings(payload)
    syncQuickMaxState(data.settings || {})
  } catch {
    store.pushMessage('切换 Max 模式失败', 'error')
  } finally {
    quickUpdating.value = false
  }
}

function applyPrefillText(text) {
  if (!text) return
  inputText.value = text
  emit('prefill-consumed')
  nextTick(() => inputEl.value?.focus())
}

// ── Conversation history ────────────────────────────

async function toggleHistory() {
  activeDropdown.value = activeDropdown.value === 'history' ? '' : 'history'
  if (showHistory.value) loadConversations()
}

async function loadConversations() {
  historyLoading.value = true
  try {
    const data = await listConversations()
    conversations.value = data.conversations || []
  } catch { /* ignore */ }
  finally { historyLoading.value = false }
}

async function selectConversation(conv) {
  if (streaming.value) return
  try {
    await loadConversation(conv.id)
    const data = await getConversation(conv.id)
    conversationId.value = conv.id
    chatMessages.value = (data.messages || []).map(m => ({
      role: m.role,
      content: m.content,
    }))
    errorText.value = ''
    activeDropdown.value = ''
    scrollToBottom()
  } catch {
    store.pushMessage('加载对话失败', 'error')
  }
}

function newConversation() {
  chatMessages.value = []
  errorText.value = ''
  streamBuffer.value = ''
  conversationId.value = null
  activeDropdown.value = ''
  clearAiHistory().catch(() => {})
}

function startRename(conv) {
  renamingId.value = conv.id
  renameText.value = conv.title
}

function cancelRename() {
  renamingId.value = null
  renameText.value = ''
}

async function confirmRename(conv) {
  const title = renameText.value.trim()
  if (!title) { cancelRename(); return }
  try {
    await renameConversation(conv.id, title)
    conv.title = title
  } catch { /* ignore */ }
  cancelRename()
}

async function deleteConv(conv) {
  try {
    await deleteConversation(conv.id)
    conversations.value = conversations.value.filter(c => c.id !== conv.id)
    if (conversationId.value === conv.id) newConversation()
  } catch { /* ignore */ }
}

function formatDate(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const diff = Date.now() - d
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`
  return d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}

function handleDocumentClick(event) {
  if (!activeDropdown.value) return
  if (actionsEl.value?.contains(event.target)) return
  activeDropdown.value = ''
  cancelRename()
}

// Public method for parent to refresh after config change
defineExpose({ loadConfig })
onMounted(() => {
  loadConfig()
  applyPrefillText(props.prefillText)
  document.addEventListener('pointerdown', handleDocumentClick)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', handleDocumentClick)
})

watch(() => props.prefillText, (text) => {
  applyPrefillText(text)
})

watch(() => props.open, (val) => {
  if (val) {
    applyPrefillText(props.prefillText)
    nextTick(() => inputEl.value?.focus())
    loadConfig()
  } else {
    activeDropdown.value = ''
    cancelRename()
  }
})
</script>

<style scoped>
/* ── Header Dropdowns ──────────────────────────────── */
.ai-header-btn.active {
  color: var(--accent-bright, #93c5fd);
  border-color: var(--accent-dim, #3b82f6);
  background: var(--accent-glow, rgba(96, 165, 250, 0.12));
}

.ai-header-dropdown {
  position: relative;
}

.ai-header-dropdown-menu {
  position: absolute;
  top: calc(100% + 10px);
  right: 0;
  z-index: 30;
  width: min(360px, calc(100vw - 40px));
  border: 1px solid var(--border, #1e3a5f);
  border-radius: 14px;
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--bg-elevated) 94%, white 6%), var(--bg-secondary));
  box-shadow:
    0 18px 40px rgba(0, 0, 0, 0.28),
    0 0 0 1px color-mix(in srgb, var(--accent-glow, rgba(96, 165, 250, 0.12)) 70%, transparent);
  backdrop-filter: blur(16px);
  overflow: hidden;
  animation: ai-dropdown-in 0.18s ease-out;
}

.ai-header-dropdown-menu-history {
  width: min(360px, calc(100vw - 40px));
}

.ai-header-dropdown-menu-memory {
  width: min(420px, calc(100vw - 40px));
}

@keyframes ai-dropdown-in {
  from {
    opacity: 0;
    transform: translateY(-6px) scale(0.98);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.ai-dropdown-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--border-subtle, #172544);
  background: color-mix(in srgb, var(--bg-tertiary) 84%, transparent);
}

.ai-dropdown-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary, #e5e7eb);
}

.ai-dropdown-subtitle {
  margin-top: 2px;
  font-size: 10px;
  color: var(--text-muted, #64748b);
}

.ai-dropdown-head-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.ai-dropdown-primary-btn,
.ai-dropdown-ghost-btn {
  height: 28px;
  padding: 0 10px;
  border-radius: 999px;
  border: 1px solid var(--border, #1e3a5f);
  cursor: pointer;
  font-size: 11px;
  transition: all 0.18s ease;
}

.ai-dropdown-primary-btn {
  background: linear-gradient(135deg, var(--accent-dim, #3b82f6), var(--accent, #60a5fa));
  color: var(--load-btn-text, #fff);
  border-color: transparent;
}

.ai-dropdown-primary-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 8px 18px var(--accent-glow, rgba(96, 165, 250, 0.12));
}

.ai-dropdown-ghost-btn {
  background: var(--bg-elevated);
  color: var(--text-secondary, #cbd5e1);
}

.ai-dropdown-ghost-btn:hover {
  border-color: var(--accent-dim, #3b82f6);
  color: var(--text-primary, #fff);
}

.ai-dropdown-ghost-btn.danger:hover {
  color: var(--text-error, #f87171);
  border-color: color-mix(in srgb, var(--text-error, #f87171) 40%, var(--border, #1e3a5f));
}

.ai-dropdown-empty {
  padding: 18px 16px;
  font-size: 12px;
  color: var(--text-muted, #64748b);
  text-align: center;
}

.ai-agent-strip {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border-subtle, #172544);
  background: color-mix(in srgb, var(--bg-secondary) 92%, var(--accent-glow, rgba(96, 165, 250, 0.12)));
  overflow-x: auto;
  scrollbar-width: thin;
}

.ai-agent-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 190px;
  height: 24px;
  padding: 0 9px;
  border: 1px solid var(--border-subtle, #172544);
  border-radius: 999px;
  background: var(--bg-elevated);
  color: var(--text-secondary, #cbd5e1);
  font-size: 11px;
  line-height: 1;
  white-space: nowrap;
}

.ai-agent-pill.muted {
  color: var(--text-muted, #64748b);
  background: color-mix(in srgb, var(--bg-tertiary) 70%, transparent);
}

.ai-agent-pill-label {
  color: var(--text-muted, #64748b);
}

.ai-agent-pill-value {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ── History Dropdown ──────────────────────────────── */
.ai-history-list {
  max-height: min(48vh, 380px);
  overflow-y: auto;
  padding: 8px;
}

.ai-history-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 6px;
  padding: 10px 10px 10px 12px;
  border: 1px solid transparent;
  border-radius: 12px;
  cursor: pointer;
  background: color-mix(in srgb, var(--bg-elevated) 82%, transparent);
  transition: all 0.18s ease;
}

.ai-history-item:last-child {
  margin-bottom: 0;
}

.ai-history-item:hover {
  background: color-mix(in srgb, var(--bg-hover, #1f2937) 88%, transparent);
  border-color: var(--border-subtle, #172544);
  transform: translateY(-1px);
}

.ai-history-item.active {
  background: linear-gradient(135deg, color-mix(in srgb, var(--accent-glow, rgba(96, 165, 250, 0.12)) 92%, transparent), transparent);
  border-color: color-mix(in srgb, var(--accent-dim, #3b82f6) 52%, var(--border, #1e3a5f));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent-dim, #3b82f6) 18%, transparent);
}

.ai-history-item-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.ai-history-item-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary, #e5e7eb);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ai-history-item-date {
  font-size: 10px;
  color: var(--text-muted, #64748b);
  text-transform: uppercase;
}

.ai-history-rename-input {
  font-size: 12px;
  width: 100%;
  padding: 7px 9px;
  border: 1px solid var(--accent-dim, #3b82f6);
  border-radius: 9px;
  background: var(--bg-primary);
  color: var(--text-primary, #e5e7eb);
  outline: none;
}

.ai-history-item-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
  opacity: 0.4;
  transition: opacity 0.15s ease;
}

.ai-history-item:hover .ai-history-item-actions,
.ai-history-item.active .ai-history-item-actions {
  opacity: 1;
}

.ai-history-action-btn {
  width: 26px;
  height: 26px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--text-muted, #64748b);
  cursor: pointer;
  font-size: 12px;
  line-height: 1;
  transition: all 0.18s ease;
}

.ai-history-action-btn:hover {
  color: var(--text-primary, #e5e7eb);
  background: var(--bg-hover, #1f2937);
  border-color: var(--border-subtle, #172544);
}

.ai-history-delete-btn:hover {
  color: var(--text-error, #f87171);
}

/* ── Memory Dropdown ───────────────────────────────── */
.ai-memory-text {
  margin: 0;
  padding: 14px 16px 16px;
  max-height: min(48vh, 380px);
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.65;
  color: var(--text-secondary, #cbd5e1);
  font-family: var(--font-mono, monospace);
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--bg-primary) 90%, transparent), color-mix(in srgb, var(--bg-elevated) 88%, transparent));
}

@media (max-width: 720px) {
  .ai-header-dropdown-menu,
  .ai-header-dropdown-menu-history,
  .ai-header-dropdown-menu-memory {
    position: fixed;
    top: 68px;
    right: 12px;
    left: 12px;
    width: auto;
    max-width: none;
  }

  .ai-dropdown-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .ai-dropdown-head-actions {
    width: 100%;
    justify-content: flex-end;
  }

  .ai-history-list,
  .ai-memory-text {
    max-height: 42vh;
  }
}

@media (prefers-reduced-motion: reduce) {
  .ai-header-dropdown-menu,
  .ai-history-item,
  .ai-dropdown-primary-btn,
  .ai-dropdown-ghost-btn {
    animation: none;
    transition: none;
  }
}
</style>
