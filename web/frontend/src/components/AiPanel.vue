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
          >输出 {{ tokenIsMax ? 'max' : (tokenIsAuto ? '推荐' : '自定义') }}</button>
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
          <AppIcon name="settings" />
        </button>
        <div class="ai-header-dropdown">
          <button
            class="ai-header-btn ai-header-dropdown-trigger"
            :class="{ active: showHistory }"
            @click.stop="toggleHistory"
            title="历史对话"
          >
            <AppIcon name="history" />
          </button>
          <div v-if="showHistory" class="ai-header-dropdown-menu ai-header-dropdown-menu-history">
            <div class="ai-dropdown-head">
              <div>
                <div class="ai-dropdown-title">历史会话</div>
                <div class="ai-dropdown-subtitle">{{ conversations.length }} 条记录</div>
              </div>
              <button class="ai-dropdown-primary-btn" @click.stop="newConversation()" title="新建对话">新建</button>
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
                      @keydown.escape.prevent="cancelRename"
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
                  <button class="ai-history-action-btn" @click="startRename(conv)" title="重命名"><AppIcon name="pencil" :size="12" /></button>
                  <button class="ai-history-action-btn ai-history-delete-btn" @click="deleteConv(conv)" title="删除"><AppIcon name="trash" :size="12" /></button>
                </div>
              </div>
            </div>
          </div>
        </div>
        <button class="ai-header-btn" @click="clearChat" title="清空对话 / 新建" :disabled="streaming"><AppIcon name="trash" /></button>
        <div class="ai-header-dropdown">
          <button
            class="ai-header-btn ai-header-dropdown-trigger"
            :class="{ active: showMemoryPanel }"
            @click.stop="toggleMemoryPanel"
            title="压缩记忆"
          >
            <AppIcon name="brain" />
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
        ><AppIcon :name="floating ? 'pin' : 'float'" /></button>
        <button class="ai-header-btn" @click="$emit('close')" title="关闭面板">
          <AppIcon name="x" />
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
          智能体模式会直接调用可用插件，优先说明已取得的证据和风险。
        </div>
        <div class="ai-quick-actions">
          <button class="ai-quick-btn" @click="sendQuick('基于当前插件输出，按证据强度列出可疑进程、模块、网络连接或持久化痕迹，并说明依据。')" :disabled="streaming">
            分析可疑迹象
          </button>
          <button class="ai-quick-btn" @click="sendQuick('只基于当前插件输出，总结关键取证事实。按字段和值列出证据，不要复述完整表格，不要推测未出现的数据。')" :disabled="streaming">
            提取取证事实
          </button>
          <button class="ai-quick-btn" @click="sendQuick('进入智能体排查模式：先列出当前镜像可用插件，然后直接运行基础插件收集进程、网络、模块和持久化证据；不要输出下一步验证路径。')" :disabled="streaming">
            自动基础排查
          </button>
          <button class="ai-quick-btn" @click="sendQuick('基于已有发现继续深入：如果需要更多证据，直接调用合适的 Volatility 插件；最终只输出已执行插件、关键证据、可疑项和无法确认的点。')" :disabled="streaming">
            继续深入
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
          <div v-if="msg.role === 'assistant' && ['aborted', 'interrupted', 'error'].includes(msg.status)" class="ai-message-meta">
            {{ msg.status === 'aborted' ? '已由用户停止' : msg.status === 'interrupted' ? '连接或服务中断' : '生成出错' }}
          </div>
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
          <!-- Copy button for assistant messages -->
          <div v-if="msg.role === 'assistant' && msg.content" class="ai-msg-toolbar">
            <button
              class="ai-copy-btn"
              @click="copyMessage(idx, msg.content)"
              :title="copiedId === idx ? '已复制' : '复制内容'"
            >{{ copiedId === idx ? '已复制' : '复制' }}</button>
          </div>
          <!-- Agent tool call / result cards -->
          <div v-if="msg.role === 'tool'" class="ai-tool-card" :class="{ 'ai-tool-error': msg.toolError, 'ai-tool-running': msg.toolRunning, 'ai-tool-done': !msg.toolRunning && !msg.toolError }">
            <div class="ai-tool-header">
              <span class="ai-tool-icon"><AppIcon :name="msg.toolRunning ? 'wrench' : msg.toolError ? 'x-circle' : 'check-circle'" :size="13" /></span>
              <code class="ai-tool-name">{{ msg.toolName }}</code>
            </div>
            <div v-if="msg.toolArgs && Object.keys(msg.toolArgs).length" class="ai-tool-args">{{ formatToolArgs(msg.toolArgs) }}</div>
            <div v-if="msg.toolProgress?.message" class="ai-tool-summary">{{ msg.toolProgress.message }}</div>
            <div v-if="msg.toolProgress?.percent != null" class="ai-tool-summary">进度 {{ msg.toolProgress.percent }}%</div>
            <div v-if="msg.toolSummary" class="ai-tool-summary">{{ msg.toolSummary }}</div>
            <div v-if="msg.details?.result_id" class="ai-tool-args">
              result_id={{ msg.details.result_id }}
              <span v-if="msg.details.total != null"> · {{ msg.details.total }} 行</span>
              <button class="ai-copy-btn" @click="openToolResult(msg.details.result_id, 1)">查看结果</button>
            </div>
            <div v-if="msg.toolError" class="ai-tool-err">{{ msg.toolError }}</div>
          </div>
        </div>
      </div>

      <!-- Streaming indicator -->
      <div v-if="streaming" class="ai-message ai-message-assistant">
        <div class="ai-message-avatar">IR</div>
        <div class="ai-message-body">
          <div class="ai-message-role">取证分析</div>
          <div class="ai-message-content ai-markdown" v-html="streamHtml" />
          <div v-if="!streamBuffer" class="ai-thinking">
            <span class="ai-thinking-dot"></span>
            <span class="ai-thinking-dot"></span>
            <span class="ai-thinking-dot"></span>
          </div>
        </div>
      </div>

      <div v-if="memoryCompressing" class="ai-memory-status">正在更新取证记忆...</div>
      <div v-if="aiRun.current.runId" class="ai-memory-status">
        上下文 {{ runtimeContextLabel }}
        · Turn {{ aiRun.current.budget.turns_used || 0 }}/{{ aiRun.current.budget.max_turns || 12 }}
        · 工具 {{ aiRun.current.budget.tool_calls_used || 0 }}/{{ aiRun.current.budget.max_tool_calls || 20 }}
        <span v-if="aiRun.current.compaction"> · 已压缩</span>
        <span v-if="aiRun.current.retry"> · 重试 {{ aiRun.current.retry.attempt }}</span>
      </div>

      <!-- Error -->
      <div v-if="errorText" class="ai-error">
        <span>错误: {{ errorText }}</span>
      </div>

      <div v-if="toolResultView" class="ai-tool-card ai-tool-done">
        <div class="ai-tool-header">
          <code class="ai-tool-name">{{ toolResultView.result_id }}</code>
          <button class="ai-copy-btn" @click="toolResultView = null">关闭</button>
        </div>
        <div class="ai-tool-args">
          {{ toolResultView.total }} 行 · 第 {{ toolResultView.page }}/{{ toolResultView.total_pages }} 页
        </div>
        <pre class="ai-tool-summary">{{ JSON.stringify(toolResultView.rows, null, 2) }}</pre>
        <div class="ai-msg-toolbar">
          <button class="ai-copy-btn" :disabled="toolResultView.page <= 1" @click="openToolResult(toolResultView.result_id, toolResultView.page - 1)">上一页</button>
          <button class="ai-copy-btn" :disabled="toolResultView.page >= toolResultView.total_pages" @click="openToolResult(toolResultView.result_id, toolResultView.page + 1)">下一页</button>
        </div>
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

        <div class="ai-mode-switch-group">
          <button
            type="button"
            class="ai-mode-switch-btn"
            :class="{ active: aiMode === 'chat' }"
            @click="aiMode = 'chat'"
            title="对话模式：AI 不会自主执行取证工具"
          >
            对话
          </button>
          <button
            type="button"
            class="ai-mode-switch-btn"
            :class="{ active: aiMode === 'agent' }"
            @click="aiMode = 'agent'"
            title="智能体模式：AI 可以自主决定并运行取证工具"
          >
            智能体
          </button>
        </div>
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
import AppIcon from './AppIcon.vue'
import { confirmAction } from '../composables/confirm'
import {
  getAiConfig, clearAiHistory, clearAiMemory, getAiMemory,
  getAiPrompts, setActivePrompt, saveAiSettings,
  listConversations, createConversation, getConversation, deleteConversation,
  renameConversation, loadConversation, queryAiToolResult,
} from '../api'
import { useAppStore } from '../stores/app'
import { useAiRunStore } from '../stores/aiRuns'

const emit = defineEmits(['close', 'open-config', 'prefill-consumed', 'toggle-pin', 'drag-start'])

const props = defineProps({
  open: { type: Boolean, default: false },
  prefillText: { type: String, default: '' },
  floating: { type: Boolean, default: false },
})

const store = useAppStore()
const aiRun = useAiRunStore()

// State
const chatMessages = ref([])
const inputText = ref('')
const streaming = ref(false)
const streamBuffer = ref('')
const errorText = ref('')
const includeContext = ref(true)
const aiMode = ref('agent')
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
const toolCallIndex = ref({})   // tool_call_id → chatMessages index
const toolResultView = ref(null)
const copiedId = ref(null)      // index of the last-copied message (for "已复制" feedback)
const tokenIsMax = ref(false)
const tokenIsAuto = ref(true)
const rowsIsMax = ref(false)
const quickUpdating = ref(false)
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

function sanitizeHtml(html) {
  const parser = new DOMParser()
  const doc = parser.parseFromString(html, 'text/html')
  const allowedTags = new Set([
    'a', 'b', 'blockquote', 'br', 'code', 'del', 'em', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'hr', 'i', 'img', 'li', 'ol', 'p', 'pre', 'strong', 'table', 'tbody', 'td', 'th', 'thead',
    'tr', 'ul', 'span', 'div', 'details', 'summary'
  ])
  const elements = doc.body.querySelectorAll('*')
  for (const el of elements) {
    if (!allowedTags.has(el.tagName.toLowerCase())) {
      el.remove()
      continue
    }
    const attrs = Array.from(el.attributes)
    for (const attr of attrs) {
      const name = attr.name.toLowerCase()
      const val = attr.value.toLowerCase()
      const allowedAttrs = ['href', 'src', 'alt', 'title', 'class', 'id', 'target', 'rel']
      if (!allowedAttrs.includes(name) || name.startsWith('on') || val.includes('javascript:') || val.includes('data:')) {
        el.removeAttribute(attr.name)
      }
    }
  }
  return doc.body.innerHTML
}

// Parsing + sanitizing is the most expensive thing this component does, and the
// message list re-renders on every store change. Memoize by content so settled
// messages are converted once, with a cap so long sessions cannot grow forever.
const MARKDOWN_CACHE_MAX = 200
const markdownCache = new Map()

function renderMarkdown(text) {
  if (!text) return ''
  const hit = markdownCache.get(text)
  if (hit !== undefined) return hit
  let html
  try {
    html = sanitizeHtml(marked.parse(text))
  }
  catch { html = text }
  if (markdownCache.size >= MARKDOWN_CACHE_MAX) {
    markdownCache.delete(markdownCache.keys().next().value)
  }
  markdownCache.set(text, html)
  return html
}

// Re-rendering the whole buffer on every streamed token makes long answers crawl.
// Render at most once per interval instead; the settled message is pushed to
// chatMessages when the stream ends, so nothing is ever lost.
const STREAM_RENDER_INTERVAL_MS = 80
const streamRenderBuffer = ref('')
let streamRenderTimer = null

function cancelStreamRender() {
  if (streamRenderTimer) {
    clearTimeout(streamRenderTimer)
    streamRenderTimer = null
  }
}

watch(streamBuffer, (text) => {
  if (!text) {
    // Reset at once so a new stream never flashes the previous answer.
    cancelStreamRender()
    streamRenderBuffer.value = ''
    return
  }
  if (streamRenderTimer) return
  streamRenderTimer = setTimeout(() => {
    streamRenderTimer = null
    streamRenderBuffer.value = streamBuffer.value
  }, STREAM_RENDER_INTERVAL_MS)
})

const streamHtml = computed(() => renderMarkdown(streamRenderBuffer.value))

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
  const tokenAuto = settings?.ai_max_tokens === 'auto' || settings?.ai_max_tokens == null
  const rowsMax = settings?.ai_context_max_rows === 'max'
  if (!rowsMax) {
    const n = Number(settings?.ai_context_max_rows)
    if (Number.isFinite(n) && n > 0) rowsDefaultValue.value = Math.trunc(n)
  }
  tokenIsMax.value = tokenMax
  tokenIsAuto.value = tokenAuto
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

const runtimeContextLabel = computed(() => {
  const context = aiRun.current.context || {}
  const used = Number(context.estimated_tokens || context.usage?.input_tokens || 0)
  const window = Number(context.context_window || 0)
  return window ? `${used.toLocaleString()}/${window.toLocaleString()} token` : `${used.toLocaleString()} token`
})

/**
 * Extract filter rules from ```filter ... ``` blocks in AI response.
 */
// Memoized like renderMarkdown: the template asks twice per assistant message
// (once for v-if, once for v-for) on every re-render.
const filtersCache = new Map()
const EMPTY_FILTERS = []

function extractFilters(text) {
  if (!text) return EMPTY_FILTERS
  const hit = filtersCache.get(text)
  if (hit !== undefined) return hit
  const rules = []
  const regex = /```filter\s*\n([\s\S]*?)```/g
  let m
  while ((m = regex.exec(text)) !== null) {
    const rule = m[1].trim()
    if (rule) rules.push(rule)
  }
  if (filtersCache.size >= MARKDOWN_CACHE_MAX) {
    filtersCache.delete(filtersCache.keys().next().value)
  }
  filtersCache.set(text, rules)
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

function handleRunEvent(event) {
  const payload = event.data || {}
  if (event.type === 'text_delta') {
    streamBuffer.value += payload.text || ''
    scrollToBottom()
  } else if (event.type === 'tool_start') {
    const idx = chatMessages.value.length
    chatMessages.value.push({
      role: 'tool',
      toolName: payload.tool_name,
      toolArgs: payload.arguments || {},
      toolRunning: true,
      toolProgress: {},
      toolSummary: '',
      toolError: '',
    })
    toolCallIndex.value[payload.tool_call_id] = idx
    scrollToBottom()
  } else if (event.type === 'tool_progress') {
    const idx = toolCallIndex.value[payload.tool_call_id]
    if (idx !== undefined && chatMessages.value[idx]) {
      chatMessages.value[idx].toolProgress = payload
    }
  } else if (event.type === 'tool_end') {
    const idx = toolCallIndex.value[payload.tool_call_id]
    if (idx !== undefined && chatMessages.value[idx]) {
      const message = chatMessages.value[idx]
      message.toolRunning = false
      message.details = payload.details || {}
      if (payload.is_error) message.toolError = payload.content || 'Unknown error'
      else message.toolSummary = payload.content || ''
    }
    scrollToBottom()
  }
}

async function sendMessage() {
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

  try {
    if (!conversationId.value) {
      const created = await createConversation('', store.selectedEngine || 'vol3')
      conversationId.value = created.conversation.id
    }
    currentAbort = { abort: () => aiRun.cancel() }
    await aiRun.start(conversationId.value, {
      message: msg,
      engine_id: store.selectedEngine || 'vol3',
      mode: aiMode.value,
      include_context: includeContext.value,
    }, handleRunEvent)
    if (streamBuffer.value) {
      chatMessages.value.push({
        role: 'assistant',
        content: streamBuffer.value,
        status: aiRun.current.status,
      })
    }
  } catch (err) {
    if (streamBuffer.value) {
      chatMessages.value.push({
        role: 'assistant',
        content: streamBuffer.value,
        status: aiRun.current.status === 'cancelled' ? 'aborted' : 'interrupted',
      })
    }
    if (err?.name !== 'AbortError') errorText.value = err?.message || String(err)
  } finally {
    streamBuffer.value = ''
    streaming.value = false
    memoryCompressing.value = false
    currentAbort = null
    if (showHistory.value) loadConversations()
    scrollToBottom()
  }
}

function formatContextMeta(context) {
  if (!context || !context.include) return '附带数据: 关闭'
  return `附带数据: ${context.plugin || '无'}`
}

function formatToolArgs(args) {
  if (!args) return ''
  const parts = []
  for (const [k, v] of Object.entries(args)) {
    if (v !== null && v !== undefined) {
      parts.push(`${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`)
    }
  }
  return parts.join(', ')
}

async function openToolResult(resultId, page = 1) {
  if (!conversationId.value) return
  try {
    toolResultView.value = await queryAiToolResult(resultId, {
      conversation_id: conversationId.value,
      page,
      page_size: 50,
    })
  } catch (error) {
    errorText.value = error?.message || '加载工具结果失败'
  }
}

async function copyMessage(idx, text) {
  try {
    await navigator.clipboard.writeText(text)
    copiedId.value = idx
    setTimeout(() => { if (copiedId.value === idx) copiedId.value = null }, 2000)
  } catch {
    // Fallback for older browsers / non-HTTPS
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.left = '-9999px'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
    copiedId.value = idx
    setTimeout(() => { if (copiedId.value === idx) copiedId.value = null }, 2000)
  }
}

async function abortStream() {
  if (currentAbort) {
    // The server must terminate the SDK request and Volatility worker first.
    await aiRun.cancel().catch(() => false)
    if (streamBuffer.value) {
      chatMessages.value.push({
        role: 'assistant',
        content: streamBuffer.value + '\n\n*(已中断)*',
        status: 'aborted',
      })
    }
    streamBuffer.value = ''
    streaming.value = false
    memoryCompressing.value = false
    currentAbort = null
  }
}

async function clearChat() {
  if (chatMessages.value.length) {
    const ok = await confirmAction({
      title: '清空对话',
      message: '将删除当前会话的全部消息（含服务端历史），无法恢复。',
      confirmText: '清空',
    })
    if (!ok) return
  }
  chatMessages.value = []
  errorText.value = ''
  streamBuffer.value = ''
  memoryCompressing.value = false
  conversationId.value = null
  toolCallIndex.value = {}
  try { await clearAiHistory() } catch { /* ignore */ }
}

async function clearMemory() {
  const ok = await confirmAction({
    title: '清空压缩记忆',
    message: '将删除 AI 的压缩记忆摘要，影响后续分析的上下文连续性。',
    confirmText: '清空',
  })
  if (!ok) return
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
    ai_max_tokens: tokenIsMax.value ? 'auto' : 'max',
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
      toolName: m.toolName,
      toolArgs: m.toolArgs,
      toolRunning: m.toolRunning,
      toolSummary: m.toolSummary,
      toolError: m.toolError,
    }))
    toolCallIndex.value = {}
    errorText.value = ''
    activeDropdown.value = ''
    scrollToBottom()
  } catch {
    store.pushMessage('加载对话失败', 'error')
  }
}

async function newConversation(skipConfirm = false) {
  if (!skipConfirm && chatMessages.value.length) {
    const ok = await confirmAction({
      title: '新建对话',
      message: '当前对话尚未关闭，新建会清空这些消息（含服务端历史）。',
      confirmText: '新建',
    })
    if (!ok) return
  }
  chatMessages.value = []
  errorText.value = ''
  streamBuffer.value = ''
  conversationId.value = null
  activeDropdown.value = ''
  toolCallIndex.value = {}
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
  const ok = await confirmAction({
    title: '删除会话',
    message: `将永久删除会话「${conv.title || conv.id}」。`,
    confirmText: '删除',
  })
  if (!ok) return
  try {
    await deleteConversation(conv.id)
    conversations.value = conversations.value.filter(c => c.id !== conv.id)
    if (conversationId.value === conv.id) newConversation(true)
  } catch (e) {
    store.pushMessage('删除会话失败: ' + (e?.message || e), 'error')
  }
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
  streaming.value = true
  aiRun.reconnect(handleRunEvent).then((recovered) => {
    if (!recovered) {
      streaming.value = false
      return
    }
    conversationId.value = aiRun.current.conversationId || conversationId.value
    if (aiRun.current.text && !streamBuffer.value) streamBuffer.value = aiRun.current.text
    if (['completed', 'failed', 'cancelled', 'interrupted'].includes(aiRun.current.status)) {
      if (streamBuffer.value) {
        chatMessages.value.push({
          role: 'assistant',
          content: streamBuffer.value,
          status: aiRun.current.status === 'cancelled' ? 'aborted' : aiRun.current.status,
        })
        streamBuffer.value = ''
      }
      streaming.value = false
    }
  }).catch((error) => {
    errorText.value = error?.message || String(error)
    streaming.value = false
  })
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', handleDocumentClick)
  cancelStreamRender()
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
    var(--shadow-lg),
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

/* ── Agent tool call cards ─────────────────────────── */
.ai-tool-card {
  margin-top: 8px;
  padding: 10px 12px;
  border: 1px solid var(--border-subtle, #172544);
  border-radius: 10px;
  background: color-mix(in srgb, var(--bg-tertiary) 80%, transparent);
  font-size: 12px;
  transition: border-color 0.2s ease, background 0.2s ease;
}

.ai-tool-card.ai-tool-running {
  border-color: color-mix(in srgb, var(--accent-dim, #3b82f6) 50%, var(--border, #1e3a5f));
  background: color-mix(in srgb, var(--accent-glow, rgba(96, 165, 250, 0.12)) 30%, var(--bg-tertiary));
}

.ai-tool-card.ai-tool-done {
  border-color: color-mix(in srgb, var(--text-success) 40%, var(--border, #1e3a5f));
  background: color-mix(in srgb, var(--text-success) 6%, var(--bg-elevated));
}

.ai-tool-card.ai-tool-error {
  border-color: color-mix(in srgb, var(--text-error, #f87171) 40%, var(--border, #1e3a5f));
  background: color-mix(in srgb, var(--text-error, #f87171) 6%, var(--bg-elevated));
}

.ai-tool-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}

.ai-tool-icon {
  display: flex;
  flex-shrink: 0;
  color: var(--text-muted);
}

/* The old emoji carried its own colors; the SVG takes the card state's. */
.ai-tool-card.ai-tool-running .ai-tool-icon {
  color: var(--accent, #3b82f6);
}

.ai-tool-card.ai-tool-done .ai-tool-icon {
  color: var(--text-success, #22c55e);
}

.ai-tool-card.ai-tool-error .ai-tool-icon {
  color: var(--text-error, #f87171);
}

.ai-tool-name {
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary, #e5e7eb);
}

.ai-tool-args {
  margin-top: 4px;
  padding-left: 22px;
  font-family: var(--font-mono, monospace);
  font-size: 11px;
  color: var(--text-muted, #64748b);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ai-tool-summary {
  margin-top: 4px;
  padding-left: 22px;
  font-size: 11px;
  color: var(--text-secondary, #cbd5e1);
  line-height: 1.5;
  word-break: break-word;
}

.ai-tool-err {
  margin-top: 4px;
  padding-left: 22px;
  font-size: 11px;
  color: var(--text-error, #f87171);
}

/* ── Message toolbar (copy button) ────────────────── */
.ai-msg-toolbar {
  display: flex;
  justify-content: flex-end;
  margin-top: 6px;
}

.ai-copy-btn {
  height: 26px;
  padding: 0 10px;
  border: 1px solid var(--border-subtle, #172544);
  border-radius: 999px;
  background: var(--bg-elevated);
  color: var(--text-muted, #64748b);
  cursor: pointer;
  font-size: 11px;
  transition: all 0.18s ease;
}

.ai-copy-btn:hover {
  color: var(--text-primary, #e5e7eb);
  border-color: var(--accent-dim, #3b82f6);
}

.ai-mode-switch-group {
  display: inline-flex;
  background: var(--bg-secondary, #0b132b);
  border: 1px solid var(--border-subtle, #1e3a5f);
  border-radius: 20px;
  padding: 2px;
  margin-left: auto;
  user-select: none;
}

.ai-mode-switch-btn {
  background: transparent;
  border: none;
  border-radius: 18px;
  color: var(--text-muted, #64748b);
  padding: 4px 12px;
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
  outline: none;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}

.ai-mode-switch-btn:hover {
  color: var(--text-primary, #e5e7eb);
}

.ai-mode-switch-btn.active {
  background: var(--accent-dim, #3b82f6);
  color: var(--load-btn-text);
  box-shadow: 0 2px 8px var(--accent-glow);
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
