<template>
  <div class="ai-panel" :class="{ collapsed: !open, floating: floating }">
    <!-- Header — also serves as drag handle when floating -->
    <div
      class="ai-panel-header"
      :class="{ 'ai-drag-handle': floating }"
      @mousedown="floating ? $emit('drag-start', $event) : undefined"
    >
      <div class="ai-panel-title">
        <span class="ai-panel-icon">Z/IR</span>
        <span class="ai-panel-heading">
          <b>证据研判台</b>
          <small>AI FORENSIC AGENT</small>
        </span>
        <span class="ai-panel-model" v-if="modelLabel">{{ modelLabel }}</span>
      </div>
      <div class="ai-panel-actions" ref="actionsEl">
        <button
          class="ai-header-btn"
          :class="{ active: focusMode }"
          @click.stop="toggleFocusMode"
          :title="focusMode ? '展开控制区和证据链' : '专注聊天：收起控制区和证据链'"
        >
          <AppIcon :name="focusMode ? 'chevron-down' : 'chevron-up'" />
        </button>
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
            :class="{ active: showRunPanel }"
            @click.stop="toggleRunPanel"
            title="运行详情"
          >
            <AppIcon name="brain" />
          </button>
          <div v-if="showRunPanel" class="ai-header-dropdown-menu ai-header-dropdown-menu-memory">
            <div class="ai-dropdown-head">
              <div>
                <div class="ai-dropdown-title">运行观测</div>
                <div class="ai-dropdown-subtitle">真实上下文、预算与完成状态</div>
              </div>
            </div>
            <div class="ai-run-inspector">
              <div><span>运行状态</span><b>{{ runStatusLabel }}</b></div>
              <div><span>上下文占用</span><b>{{ runtimeContextLabel }}</b></div>
              <div><span>模型轮次</span><b>{{ aiRun.current.budget.turns_used || 0 }} / {{ aiRun.current.budget.max_turns || 12 }}</b></div>
              <div><span>工具调用</span><b>{{ aiRun.current.budget.tool_calls_used || 0 }} / {{ aiRun.current.budget.max_tool_calls || 20 }}</b></div>
              <div><span>自动续写</span><b>{{ aiRun.current.continuations || 0 }} 次</b></div>
              <div><span>结束原因</span><b>{{ aiRun.current.stopReason || '—' }}</b></div>
            </div>
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

    <div class="ai-control-deck" :class="{ collapsed: contextDeckCollapsed }">
      <button
        v-if="contextDeckCollapsed"
        type="button"
        class="ai-control-deck-compact"
        title="展开分析上下文"
        @click="contextDeckCollapsed = false"
      >
        <span class="compact-deck-label">分析上下文</span>
        <strong>{{ activePromptName }}</strong>
        <span>{{ modelName || '未配置模型' }}</span>
        <span>{{ store.currentPlugin || '未选择插件' }}</span>
        <AppIcon name="chevron-down" :size="12" />
      </button>
      <template v-else>
        <div class="ai-prompt-bar">
          <span class="ai-prompt-bar-label">分析策略</span>
          <select class="ai-prompt-select" v-model="activePromptId" @change="onPromptChange">
            <option v-for="p in prompts" :key="p.id" :value="p.id">
              {{ p.name }}{{ p.builtin ? '' : ' (自定义)' }}
            </option>
          </select>
          <button
            class="ai-quick-max-btn"
            :class="{ active: tokenIsMax }"
            :disabled="quickUpdating"
            @click="toggleTokenMax"
            title="切换响应输出上限"
          >输出 {{ tokenIsMax ? 'MAX' : (tokenIsAuto ? 'AUTO' : '自定义') }}</button>
          <button
            class="ai-quick-max-btn"
            :class="{ active: rowsIsMax }"
            :disabled="quickUpdating"
            @click="toggleRowsMax"
            title="切换插件上下文上限"
          >证据 {{ rowsIsMax ? 'MAX' : '默认' }}</button>
          <button
            type="button"
            class="ai-deck-collapse-btn"
            title="收起分析上下文"
            @click="contextDeckCollapsed = true"
          ><AppIcon name="chevron-up" :size="12" /></button>
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
      </template>
    </div>
    <AiRunRail
      :run="aiRun.current"
      :streaming="streaming"
      v-model:collapsed="evidenceRailCollapsed"
    />

    <!-- Messages -->
    <div class="ai-messages" ref="messagesEl">
      <!-- Welcome -->
      <div v-if="!chatMessages.length" class="ai-welcome">
        <div class="ai-welcome-overline">EVIDENCE-FIRST ANALYSIS</div>
        <div class="ai-welcome-icon">01</div>
        <div class="ai-welcome-title">从证据出发，不替异常下定义</div>
        <div class="ai-welcome-text">
          当前镜像、插件结果和工具回执组成同一条证据链。<br/>
          Agent 会核验插件目录并自动执行补证；只有工具不可用、失败或预算耗尽时才保留未决项。
        </div>
        <div class="ai-quick-actions">
          <button class="ai-quick-btn" @click="sendQuick('严格基于当前插件输出做低误报研判。先列已确认事实；只有同一对象具备至少两个独立异常证据时才列为可疑，否则明确说明证据不足。')" :disabled="streaming">
            <span class="ai-quick-index">01</span>
            <span><b>快速研判</b><small>低误报提取事实与异常</small></span>
          </button>
          <button class="ai-quick-btn" @click="sendQuick('进入证据链排查：先调用 list_plugins 核验可用插件名，再直接运行必要插件交叉验证当前异常。最终只报告已执行动作和可复核证据。')" :disabled="streaming">
            <span class="ai-quick-index">02</span>
            <span><b>证据链排查</b><small>自动核验目录并执行补证</small></span>
          </button>
          <button class="ai-quick-btn" @click="sendQuick('根据当前数据生成 Zero 过滤规则，筛选可疑 PID、路径、连接或注册表项，并用 ```filter 代码块输出。')" :disabled="streaming">
            <span class="ai-quick-index">03</span>
            <span><b>生成过滤规则</b><small>把已确认特征转为可执行筛选</small></span>
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
          {{ msg.role === 'user' ? 'Q' : msg.role === 'tool' ? 'T' : 'A' }}
        </div>
        <div class="ai-message-body">
          <div class="ai-message-role">
            <span>{{ msg.role === 'user' ? '分析请求' : msg.role === 'tool' ? '证据动作' : '研判结论' }}</span>
            <span v-if="msg.role === 'assistant' && msg.stopReason" class="ai-message-state">{{ formatStopReason(msg.stopReason) }}</span>
          </div>
          <template v-if="msg.role === 'assistant'">
            <div class="ai-message-content ai-markdown" v-html="renderMarkdown(msg.content)" />
            <div v-if="msg.stopReason === 'length'" class="ai-incomplete-note">
              本条输出达到上限，内容可能不完整。可发送“从中断处继续”补全。
            </div>
            <div v-if="['aborted', 'interrupted', 'error'].includes(msg.status)" class="ai-message-meta">
              {{ msg.status === 'aborted' ? '已由用户停止' : msg.status === 'interrupted' ? '连接或服务中断' : '生成出错' }}
            </div>
          </template>
          <div v-else-if="msg.role === 'user'" class="ai-message-content">{{ msg.content }}</div>
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
          <div
            v-if="msg.role === 'tool'"
            class="ai-tool-card"
            :class="{
              'ai-tool-error': msg.toolError,
              'ai-tool-running': msg.toolRunning,
              'ai-tool-done': !msg.toolRunning && !msg.toolError,
              collapsed: !msg.toolExpanded,
            }"
          >
            <button type="button" class="ai-tool-header ai-tool-toggle" @click="msg.toolExpanded = !msg.toolExpanded">
              <span class="ai-tool-icon"><AppIcon :name="msg.toolRunning ? 'wrench' : msg.toolError ? 'x-circle' : 'check-circle'" :size="13" /></span>
              <code class="ai-tool-name">{{ msg.toolName }}</code>
              <span class="ai-tool-compact-summary">{{ toolCompactSummary(msg) }}</span>
              <AppIcon :name="msg.toolExpanded ? 'chevron-up' : 'chevron-down'" :size="12" />
            </button>
            <div v-if="msg.toolExpanded" class="ai-tool-details">
              <div v-if="msg.toolArgs && Object.keys(msg.toolArgs).length" class="ai-tool-args">{{ formatToolArgs(msg.toolArgs) }}</div>
              <div v-if="msg.toolProgress?.message" class="ai-tool-summary">{{ msg.toolProgress.message }}</div>
              <div v-if="msg.toolProgress?.percent != null" class="ai-tool-summary">进度 {{ msg.toolProgress.percent }}%</div>
              <div v-if="msg.toolSummary" class="ai-tool-summary">{{ msg.toolSummary }}</div>
              <div v-if="msg.details?.result_id" class="ai-tool-args">
                result_id={{ msg.details.result_id }}
                <span v-if="msg.details.total != null"> · {{ msg.details.total }} 行</span>
                <button class="ai-copy-btn" @click.stop="openToolResult(msg.details.result_id, 1)">查看结果</button>
              </div>
              <div v-if="msg.toolError" class="ai-tool-err">{{ msg.toolError }}</div>
            </div>
          </div>
        </div>
      </div>

      <!-- Streaming indicator -->
      <div v-if="streaming" class="ai-message ai-message-assistant">
        <div class="ai-message-avatar">A</div>
        <div class="ai-message-body">
          <div class="ai-message-role">
            <span>实时研判</span>
            <span class="ai-live-label"><i></i>{{ streamingStageLabel }}</span>
          </div>
          <details v-if="aiRun.current.thinkingSummary" class="ai-thinking-summary">
            <summary>查看推理摘要</summary>
            <p>{{ aiRun.current.thinkingSummary }}</p>
          </details>
          <div class="ai-message-content ai-markdown" v-html="streamHtml" />
          <div v-if="!streamBuffer" class="ai-thinking">
            <span class="ai-thinking-dot"></span>
            <span class="ai-thinking-dot"></span>
            <span class="ai-thinking-dot"></span>
          </div>
        </div>
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
      <div class="ai-composer">
        <textarea
          ref="inputEl"
          class="ai-input"
          v-model="inputText"
          placeholder="描述需要核验的对象、异常或证据范围…"
          @keydown.enter.exact.prevent="sendMessage"
          rows="1"
          :disabled="streaming"
        />
        <button
          class="ai-send-btn"
          @click="streaming ? abortStream() : sendMessage()"
          :class="{ 'ai-stop-btn': streaming }"
        >
          {{ streaming ? '停止运行' : (aiMode === 'agent' ? '开始研判' : '发送') }}
        </button>
      </div>
      <div class="ai-composer-foot">
        <div class="ai-composer-bottom-controls">
          <div class="ai-mode-switch-group" aria-label="运行模式">
            <button
              type="button"
              class="ai-mode-switch-btn"
              :class="{ active: aiMode === 'chat' }"
              @click="aiMode = 'chat'"
              title="对话模式：AI 不会自主执行取证工具"
            >Chat</button>
            <button
              type="button"
              class="ai-mode-switch-btn"
              :class="{ active: aiMode === 'agent' }"
              @click="aiMode = 'agent'"
              title="智能体模式：AI 可以自主决定并运行取证工具"
            >Agent</button>
          </div>
          <label class="ai-context-toggle" title="附带当前插件数据">
            <input type="checkbox" v-model="includeContext" />
            <span class="ai-context-label">附带证据</span>
          </label>
        </div>
        <span class="ai-context-current">{{ liveContextLabel }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, watch, onMounted, onBeforeUnmount } from 'vue'
import { marked } from 'marked'
import AppIcon from './AppIcon.vue'
import AiRunRail from './AiRunRail.vue'
import { confirmAction } from '../composables/confirm'
import {
  getAiConfig, clearAiHistory,
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
const AI_LAYOUT_KEY = 'zero-ai-panel-layout-v1'

function loadLayoutPreference() {
  try {
    const saved = JSON.parse(localStorage.getItem(AI_LAYOUT_KEY) || '{}')
    return {
      context: saved.context !== false,
      evidence: saved.evidence !== false,
    }
  } catch {
    return { context: true, evidence: true }
  }
}

const initialLayout = loadLayoutPreference()

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
const contextDeckCollapsed = ref(initialLayout.context)
const evidenceRailCollapsed = ref(initialLayout.evidence)

// Prompt selector
const prompts = ref([])
const activePromptId = ref('default')
const activeDropdown = ref('')
const actionsEl = ref(null)
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
const showRunPanel = computed(() => activeDropdown.value === 'run')
const showHistory = computed(() => activeDropdown.value === 'history')
const focusMode = computed(() => contextDeckCollapsed.value && evidenceRailCollapsed.value)

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
    { label: '模型', value: modelLabel.value || '未配置', muted: !modelLabel.value },
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

const runStatusLabel = computed(() => {
  if (streaming.value) return '运行中'
  const labels = {
    idle: '待命',
    completed: '已完成',
    failed: '失败',
    cancelled: '已停止',
    interrupted: '已中断',
  }
  return labels[aiRun.current.status] || aiRun.current.status || '待命'
})

const streamingStageLabel = computed(() => {
  if (aiRun.current.verifications > 0 && aiRun.current.lastEventType !== 'run_end') return '自动补证'
  if (aiRun.current.continuations > 0 && aiRun.current.lastEventType !== 'run_end') return '自动续写'
  const tools = Object.values(aiRun.current.tools || {})
  const runningTool = tools.find(tool => tool.status === 'running')
  if (runningTool) return `执行 ${runningTool.name || '取证工具'}`
  if (streamBuffer.value) return '形成结论'
  if (aiRun.current.lastEventType === 'context') return '装载证据'
  return '分析证据'
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

function toggleFocusMode() {
  const next = !focusMode.value
  contextDeckCollapsed.value = next
  evidenceRailCollapsed.value = next
}

function handleRunEvent(event) {
  const payload = event.data || {}
  if (event.type === 'text_delta') {
    streamBuffer.value += payload.text || ''
    scrollToBottom()
  } else if (event.type === 'verification_required') {
    // A recommendation-only draft was superseded; the Agent is continuing
    // with real plugins, so keep it out of the visible final answer.
    streamBuffer.value = ''
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
      toolExpanded: true,
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
      if (payload.is_error) {
        message.toolError = payload.content || 'Unknown error'
        message.toolExpanded = true
      } else {
        message.toolSummary = payload.content || ''
        message.toolExpanded = false
      }
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
        stopReason: aiRun.current.stopReason,
      })
    }
  } catch (err) {
    if (streamBuffer.value) {
      chatMessages.value.push({
        role: 'assistant',
        content: streamBuffer.value,
        status: aiRun.current.status === 'cancelled' ? 'aborted' : 'interrupted',
        stopReason: aiRun.current.stopReason,
      })
    }
    if (err?.name !== 'AbortError') errorText.value = err?.message || String(err)
  } finally {
    streamBuffer.value = ''
    streaming.value = false
    currentAbort = null
    if (showHistory.value) loadConversations()
    scrollToBottom()
  }
}

function formatContextMeta(context) {
  if (!context || !context.include) return '附带数据: 关闭'
  return `附带数据: ${context.plugin || '无'}`
}

function formatStopReason(reason) {
  const labels = {
    stop: '完整',
    completed: '完整',
    length: '达到输出上限',
    cancelled: '已停止',
    content_filter: '内容受限',
    error: '异常结束',
  }
  return labels[reason] || reason
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

function toolCompactSummary(message) {
  if (message.toolRunning) {
    if (message.toolProgress?.percent != null) return `${message.toolProgress.percent}%`
    return message.toolProgress?.message || '执行中'
  }
  if (message.toolError) return '执行失败'
  if (message.details?.total != null) return `${message.details.total} 行证据`
  if (message.details?.result_id) return '结果已保存'
  return message.toolSummary ? String(message.toolSummary).replace(/\s+/g, ' ').slice(0, 48) : '执行完成'
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
        stopReason: 'cancelled',
      })
    }
    streamBuffer.value = ''
    streaming.value = false
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
  conversationId.value = null
  toolCallIndex.value = {}
  try { await clearAiHistory() } catch { /* ignore */ }
}

function toggleRunPanel() {
  activeDropdown.value = activeDropdown.value === 'run' ? '' : 'run'
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
      details: m.details,
      stopReason: m.stopReason,
      status: m.status,
      toolExpanded: Boolean(m.toolError),
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
          stopReason: aiRun.current.stopReason,
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

watch([contextDeckCollapsed, evidenceRailCollapsed], ([context, evidence]) => {
  try {
    localStorage.setItem(AI_LAYOUT_KEY, JSON.stringify({ context, evidence }))
  } catch {
    // Layout persistence is optional in private browsing contexts.
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

/* ── Evidence workbench refresh ────────────────────── */
.ai-panel {
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--accent) 2%, transparent), transparent 26%),
    var(--bg-secondary);
}

.ai-panel-header {
  min-height: 54px;
  padding: 10px 13px;
  border-bottom-color: var(--border-subtle);
  background:
    linear-gradient(100deg, color-mix(in srgb, var(--accent) 8%, transparent), transparent 42%),
    var(--bg-tertiary);
}

.ai-panel-title {
  flex: 1;
  flex-wrap: nowrap;
  gap: 9px;
}

.ai-panel-icon {
  display: grid;
  place-items: center;
  width: 34px;
  height: 28px;
  flex: none;
  border: 1px solid color-mix(in srgb, #39c6c8 50%, var(--border));
  border-radius: 5px;
  background: color-mix(in srgb, #39c6c8 8%, var(--bg-elevated));
  color: #60dadd;
  font: 800 10px/1 var(--font-mono, monospace);
  letter-spacing: .04em;
}

.ai-panel-heading {
  display: grid;
  min-width: 0;
  line-height: 1.05;
}

.ai-panel-heading b {
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 650;
}

.ai-panel-heading small {
  margin-top: 4px;
  color: var(--text-muted);
  font: 700 8px/1 var(--font-mono, monospace);
  letter-spacing: .11em;
}

.ai-panel-model {
  display: none;
}

.ai-control-deck {
  flex: none;
  border-bottom: 1px solid var(--border-subtle);
  background: color-mix(in srgb, var(--bg-secondary) 96%, black 4%);
}

.ai-control-deck.collapsed {
  height: 31px;
}

.ai-control-deck-compact {
  width: 100%;
  height: 30px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 12px;
  border: 0;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  text-align: left;
}

.ai-control-deck-compact:hover {
  background: color-mix(in srgb, #39c6c8 6%, transparent);
}

.compact-deck-label {
  flex: none;
  color: #39c6c8;
  font: 700 8px/1 var(--font-mono, monospace);
  letter-spacing: .08em;
}

.ai-control-deck-compact strong,
.ai-control-deck-compact > span:not(.compact-deck-label) {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ai-control-deck-compact strong {
  flex: 1;
  color: var(--text-secondary);
  font-size: 9px;
  font-weight: 600;
}

.ai-control-deck-compact > span:not(.compact-deck-label) {
  max-width: 90px;
  flex: none;
  font: 500 8px/1 var(--font-mono, monospace);
}

.ai-prompt-bar {
  gap: 6px;
  padding: 7px 12px 5px;
  border: 0;
  background: transparent;
}

.ai-deck-collapse-btn {
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  flex: none;
  border: 1px solid var(--border-subtle);
  border-radius: 5px;
  background: var(--bg-elevated);
  color: var(--text-muted);
  cursor: pointer;
}

.ai-deck-collapse-btn:hover {
  border-color: #39c6c8;
  color: var(--text-primary);
}

.ai-prompt-bar-label {
  color: var(--text-secondary);
  font: 650 10px/1 var(--font-mono, monospace);
}

.ai-prompt-select {
  min-width: 88px;
  height: 26px;
  border-radius: 5px;
  background-color: var(--bg-elevated);
}

.ai-quick-max-btn {
  height: 26px;
  border-radius: 5px;
  font-size: 9px;
}

.ai-agent-strip {
  gap: 5px;
  padding: 4px 12px 8px;
  border: 0;
  background: transparent;
}

.ai-agent-pill {
  height: 21px;
  padding: 0 7px;
  border-radius: 4px;
  background: color-mix(in srgb, var(--bg-elevated) 70%, transparent);
  font-size: 9px;
}

.ai-agent-pill:first-child {
  border-color: color-mix(in srgb, #39c6c8 36%, var(--border-subtle));
}

.ai-run-inspector {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1px;
  padding: 1px;
  background: var(--border-subtle);
}

.ai-run-inspector > div {
  display: grid;
  gap: 5px;
  padding: 12px 14px;
  background: var(--bg-secondary);
}

.ai-run-inspector span {
  color: var(--text-muted);
  font-size: 9px;
  text-transform: uppercase;
  letter-spacing: .05em;
}

.ai-run-inspector b {
  color: var(--text-primary);
  font: 600 11px/1.2 var(--font-mono, monospace);
}

.ai-messages {
  gap: 18px;
  padding: 18px 14px 22px;
  scroll-padding-bottom: 24px;
  background-image:
    linear-gradient(color-mix(in srgb, var(--border-subtle) 35%, transparent) 1px, transparent 1px);
  background-size: 100% 44px;
}

.ai-welcome {
  align-items: stretch;
  justify-content: flex-start;
  padding: 34px 12px 22px;
  text-align: left;
}

.ai-welcome-overline {
  margin-bottom: 12px;
  color: #39c6c8;
  font: 700 9px/1 var(--font-mono, monospace);
  letter-spacing: .16em;
}

.ai-welcome-icon {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  margin: 0 0 18px;
  border: 1px solid color-mix(in srgb, #39c6c8 45%, var(--border));
  border-radius: 50%;
  background: color-mix(in srgb, #39c6c8 9%, var(--bg-secondary));
  color: #65dfe1;
  font: 700 12px/1 var(--font-mono, monospace);
  opacity: 1;
  animation: none;
}

.ai-welcome-title {
  max-width: 320px;
  margin-bottom: 10px;
  font-size: 19px;
  line-height: 1.25;
  letter-spacing: -.015em;
}

.ai-welcome-text {
  max-width: 390px;
  margin-bottom: 24px;
  color: var(--text-secondary);
  line-height: 1.75;
}

.ai-quick-actions {
  max-width: none;
  gap: 7px;
}

.ai-quick-btn {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 54px;
  padding: 9px 12px;
  border-radius: 7px;
  background:
    linear-gradient(90deg, color-mix(in srgb, var(--accent) 5%, transparent), transparent 55%),
    var(--bg-elevated);
}

.ai-quick-btn:hover:not(:disabled) {
  transform: translateX(2px);
}

.ai-quick-index {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  flex: none;
  border-right: 1px solid var(--border);
  color: #39c6c8;
  font: 700 9px/1 var(--font-mono, monospace);
}

.ai-quick-btn > span:last-child {
  display: grid;
  gap: 3px;
}

.ai-quick-btn b {
  color: var(--text-primary);
  font-size: 11px;
  font-weight: 650;
}

.ai-quick-btn small {
  color: var(--text-muted);
  font-size: 9px;
}

.ai-message {
  position: relative;
  gap: 9px;
}

.ai-message::before {
  position: absolute;
  top: 30px;
  bottom: -18px;
  left: 13px;
  width: 1px;
  background: var(--border-subtle);
  content: "";
}

.ai-message:last-of-type::before {
  display: none;
}

.ai-message-avatar {
  z-index: 1;
  width: 27px;
  height: 27px;
  border-radius: 50%;
  background: var(--bg-secondary);
  font: 700 9px/1 var(--font-mono, monospace);
}

.ai-message-tool .ai-message-avatar {
  border-color: color-mix(in srgb, var(--text-success, #46c780) 45%, var(--border));
  color: var(--text-success, #46c780);
}

.ai-message-role {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 20px;
  margin-bottom: 5px;
  font-family: var(--font-mono, monospace);
  letter-spacing: .08em;
}

.ai-message-state,
.ai-live-label {
  color: var(--text-muted);
  font-size: 9px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
}

.ai-live-label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: #39c6c8;
}

.ai-live-label i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
  animation: ai-live-pulse 1.2s ease-in-out infinite;
}

.ai-message-assistant .ai-message-body {
  padding: 10px 12px 8px;
  border: 1px solid var(--border-subtle);
  border-radius: 7px;
  background: color-mix(in srgb, var(--bg-elevated) 85%, transparent);
}

.ai-message-user .ai-message-content {
  border-radius: 7px;
  background: color-mix(in srgb, var(--accent) 5%, var(--bg-elevated));
}

.ai-message-content {
  font-size: 12px;
  line-height: 1.72;
}

.ai-incomplete-note {
  margin-top: 10px;
  padding: 8px 9px;
  border-left: 2px solid var(--text-warning, #e5a84b);
  background: color-mix(in srgb, var(--text-warning, #e5a84b) 8%, transparent);
  color: var(--text-warning, #e5a84b);
  font-size: 10px;
  line-height: 1.5;
}

.ai-thinking-summary {
  margin: 0 0 9px;
  padding: 7px 9px;
  border: 1px dashed var(--border);
  border-radius: 5px;
  color: var(--text-muted);
  font-size: 10px;
}

.ai-thinking-summary summary {
  cursor: pointer;
  color: var(--text-secondary);
}

.ai-thinking-summary p {
  margin: 7px 0 0;
  white-space: pre-wrap;
}

.ai-tool-card {
  border-radius: 6px;
  background: var(--bg-secondary);
}

.ai-tool-card.collapsed {
  padding: 0;
}

.ai-tool-toggle {
  width: 100%;
  min-width: 0;
  min-height: 32px;
  margin: 0;
  padding: 6px 8px;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-align: left;
}

.ai-tool-toggle:hover {
  background: color-mix(in srgb, var(--accent) 5%, transparent);
}

.ai-tool-toggle .ai-tool-name {
  max-width: 42%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ai-tool-compact-summary {
  min-width: 0;
  overflow: hidden;
  flex: 1;
  color: var(--text-muted);
  font-size: 9px;
  font-weight: 400;
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ai-tool-details {
  padding: 0 2px 2px;
}

.ai-input-area {
  padding: 9px 12px 10px;
  border-top-color: var(--border-subtle);
  background:
    linear-gradient(180deg, color-mix(in srgb, #39c6c8 4%, transparent), transparent 50%),
    var(--bg-tertiary);
}

.ai-composer,
.ai-composer-foot {
  display: flex;
  align-items: center;
}

.ai-composer-bottom-controls {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: none;
}

.ai-mode-switch-group {
  margin: 0;
  padding: 1px;
  border-radius: 4px;
}

.ai-mode-switch-btn {
  min-height: 18px;
  border-radius: 2px;
  padding: 2px 6px;
  font-size: 8px;
}

.ai-mode-switch-btn.active {
  background: color-mix(in srgb, #39c6c8 22%, var(--bg-elevated));
  color: #73e3e5;
  box-shadow: none;
}

.ai-context-current {
  max-width: 42%;
  font-size: 8px;
}

.ai-composer {
  gap: 7px;
  align-items: stretch;
  padding: 5px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-secondary);
  transition: border-color .18s ease, box-shadow .18s ease;
}

.ai-composer:focus-within {
  border-color: color-mix(in srgb, #39c6c8 58%, var(--border));
  box-shadow: 0 0 0 3px color-mix(in srgb, #39c6c8 8%, transparent);
}

.ai-input {
  min-height: 36px;
  max-height: 150px;
  padding: 8px 9px;
  border: 0;
  background: transparent;
  box-shadow: none;
  resize: vertical;
}

.ai-input:focus {
  border: 0;
  box-shadow: none;
}

.ai-send-btn {
  min-width: 76px;
  border-radius: 5px;
  background: linear-gradient(135deg, #278b91, #37b7b9);
  color: #041315;
  font-size: 10px;
  font-weight: 700;
}

.ai-stop-btn {
  background: color-mix(in srgb, var(--text-error, #f87171) 18%, var(--bg-elevated));
  color: var(--text-error, #f87171);
}

.ai-composer-foot {
  gap: 8px;
  min-height: 22px;
  margin-top: 4px;
  color: var(--text-muted);
  font-size: 8px;
}

.ai-composer-foot .ai-context-toggle {
  gap: 4px;
}

.ai-composer-foot .ai-context-toggle input {
  width: 11px;
  height: 11px;
}

.ai-composer-foot .ai-context-label {
  font-size: 8px;
  white-space: nowrap;
}

.ai-composer-foot .ai-context-current {
  margin-left: auto;
}

@keyframes ai-live-pulse {
  50% { opacity: .3; }
}

@media (max-width: 560px) {
  .ai-panel-model,
  .ai-prompt-bar-label,
  .ai-quick-max-btn:last-child {
    display: none;
  }

  .ai-control-deck .ai-agent-pill:nth-child(n+4) {
    display: none;
  }

  .ai-context-current {
    max-width: 44%;
  }

  .ai-control-deck-compact > span:not(.compact-deck-label) {
    display: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .ai-live-label i {
    animation: none;
  }
}
</style>
