import { defineStore } from 'pinia'
import { computed, reactive, ref } from 'vue'
import {
  authHeaders,
  cancelAiRun,
  createAiRun,
  getAiRun,
} from '../api'

const ACTIVE_RUN_KEY = 'zero-ai-active-run'
const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'interrupted'])

export function initialRunState() {
  return {
    runId: '',
    conversationId: '',
    status: 'idle',
    lastSeq: 0,
    text: '',
    thinkingSummary: '',
    tools: {},
    budget: {},
    context: {},
    retry: null,
    compaction: null,
    stopReason: '',
    continuations: 0,
    lastEventType: '',
    error: '',
  }
}

export function reduceRunEvent(state, event) {
  const seq = Number(event?.seq || 0)
  if (!seq || seq <= state.lastSeq) return false
  state.lastSeq = seq
  state.runId = event.run_id || state.runId
  state.conversationId = event.conversation_id || state.conversationId
  state.lastEventType = event.type
  const data = event.data || {}
  switch (event.type) {
    case 'run_start':
      state.status = 'running'
      state.budget = data.budget || state.budget
      break
    case 'text_delta':
      state.text += data.text || ''
      break
    case 'thinking_summary_delta':
      state.thinkingSummary += data.text || ''
      break
    case 'tool_start':
      state.tools[data.tool_call_id] = {
        id: data.tool_call_id,
        name: data.tool_name,
        arguments: data.arguments || {},
        status: 'running',
        progress: {},
        result: null,
      }
      break
    case 'tool_progress':
      state.tools[data.tool_call_id] = {
        ...(state.tools[data.tool_call_id] || { id: data.tool_call_id, name: data.tool_name }),
        status: 'running',
        progress: data,
      }
      break
    case 'tool_end':
      state.tools[data.tool_call_id] = {
        ...(state.tools[data.tool_call_id] || { id: data.tool_call_id, name: data.tool_name }),
        status: data.is_error ? 'error' : 'complete',
        result: data,
      }
      break
    case 'usage':
      state.context = { ...state.context, usage: data }
      break
    case 'message_end':
      state.stopReason = data.message?.stop_reason || data.stop_reason || state.stopReason
      state.context = { ...state.context, usage: data.usage || state.context.usage }
      break
    case 'output_continuation':
      state.continuations = Math.max(state.continuations, Number(data.attempt || 0))
      state.budget = data.budget || state.budget
      break
    case 'context':
      state.context = { ...state.context, ...data }
      break
    case 'retry':
      state.retry = data
      break
    case 'compaction':
      state.compaction = data
      state.context = { ...state.context, ...data }
      break
    case 'turn_start':
    case 'turn_end':
    case 'budget_exhausted':
      state.budget = data.budget || state.budget
      break
    case 'run_end':
      state.status = data.status || 'completed'
      state.stopReason = data.reason || state.stopReason
      state.error = data.error || ''
      state.budget = data.budget || state.budget
      break
  }
  return true
}

export const useAiRunStore = defineStore('aiRuns', () => {
  const current = reactive(initialRunState())
  const reconnecting = ref(false)
  let controller = null

  const active = computed(() => current.status === 'running' || current.status === 'created')

  function reset(run = null) {
    Object.assign(current, initialRunState(), run ? {
      runId: run.run_id,
      conversationId: run.conversation_id,
      status: run.status,
      budget: run.budget || {},
    } : {})
  }

  function remember() {
    try {
      if (current.runId && !TERMINAL.has(current.status)) {
        localStorage.setItem(ACTIVE_RUN_KEY, JSON.stringify({
          runId: current.runId,
          conversationId: current.conversationId,
          lastSeq: current.lastSeq,
        }))
      } else {
        localStorage.removeItem(ACTIVE_RUN_KEY)
      }
    } catch {
      // localStorage can be unavailable in private mode.
    }
  }

  async function connect(runId, { afterSeq = current.lastSeq, onEvent = null } = {}) {
    if (controller) controller.abort()
    controller = new AbortController()
    const res = await fetch(`/api/ai/runs/${runId}/events?after_seq=${Number(afterSeq || 0)}`, {
      headers: authHeaders(),
      signal: controller.signal,
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (!line.startsWith('data:')) continue
        const event = JSON.parse(line.slice(5).trim())
        if (reduceRunEvent(current, event)) {
          remember()
          if (onEvent) onEvent(event)
        }
      }
    }
    remember()
  }

  async function start(conversationId, payload, onEvent = null) {
    const run = await createAiRun(conversationId, payload)
    reset(run)
    remember()
    await connect(run.run_id, { afterSeq: 0, onEvent })
    return run
  }

  async function cancel() {
    if (!current.runId || TERMINAL.has(current.status)) return false
    // Server cancellation must happen before the local SSE is closed.
    const result = await cancelAiRun(current.runId)
    if (controller) controller.abort()
    current.status = 'cancelled'
    remember()
    return Boolean(result.ok)
  }

  async function reconnect(onEvent = null) {
    let saved = null
    try {
      saved = JSON.parse(localStorage.getItem(ACTIVE_RUN_KEY) || 'null')
    } catch {
      return false
    }
    if (!saved?.runId) return false
    reconnecting.value = true
    try {
      const run = await getAiRun(saved.runId)
      reset(run)
      // Replay the independent event log from zero so partial text/tool cards
      // are rebuilt after a full page refresh. The reducer still de-duplicates.
      current.lastSeq = 0
      await connect(run.run_id, { afterSeq: 0, onEvent })
      return true
    } finally {
      reconnecting.value = false
    }
  }

  return { current, active, reconnecting, reset, start, connect, cancel, reconnect }
})
