/**
 * API client for the Zero Web UI.
 */
import axios from 'axios'

const API_TOKEN_KEY = 'zero-api-token'

export function getApiToken() {
  try {
    return String(localStorage.getItem(API_TOKEN_KEY) || '').trim()
  } catch {
    return ''
  }
}

export function setApiToken(token) {
  const value = String(token || '').trim()
  try {
    if (value) localStorage.setItem(API_TOKEN_KEY, value)
    else localStorage.removeItem(API_TOKEN_KEY)
  } catch {
    /* ignore quota / private mode */
  }
  return value
}

export function authHeaders() {
  const token = getApiToken()
  if (!token) return {}
  return {
    'X-API-Token': token,
    Authorization: `Bearer ${token}`,
  }
}

const api = axios.create({
  baseURL: '',   // Vite proxy handles /api to backend
  timeout: 30000,
})

api.interceptors.request.use((config) => {
  const headers = authHeaders()
  if (Object.keys(headers).length) {
    config.headers = { ...config.headers, ...headers }
  }
  return config
})

api.interceptors.response.use(
  (res) => res.data,
  (err) => {
    if (err?.code === 'ERR_CANCELED' || err?.name === 'CanceledError') {
      return Promise.reject(err)
    }
    const detail = err.response?.data?.detail
    const msg = (detail && typeof detail === 'object' ? detail.message : detail)
      || err.message || 'Network error'
    return Promise.reject(new Error(msg))
  },
)

/* ── Engine helpers ─────────────────────────────────── */

export function listEngines() {
  return api.get('/api/engines')
}

export function getEngineSettings(engine = 'vol3') {
  return api.get(`/api/engines/${engine}/settings`)
}

export function updateEngineSettings(engine = 'vol3', settings = {}) {
  return api.post(`/api/engines/${engine}/settings`, settings)
}

/* ── REST helpers ──────────────────────────────────── */

export function loadImage(path, engine = 'vol3') {
  // No timeout: opening a multi-GB memory image can legitimately exceed the
  // 30s global default, and a client-side abort here just strands the user.
  return api.post('/api/image/load', { path, engine }, { timeout: 0 })
}

/**
 * Detect matching kernel symbols, or download candidates after the operator
 * confirms. Native fetch is used for byte-level NDJSON progress.
 */
export async function autoDownloadImageSymbols(path, onProgress, options = {}) {
  const response = await fetch('/api/image/symbols/auto', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify({
      path,
      download: Boolean(options.download),
      use_gh_proxy: Boolean(options.useGhProxy),
      paths: Array.isArray(options.paths) ? options.paths : [],
      repo: options.repo || '',
    }),
    signal: options.signal,
  })
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      const payload = await response.json()
      detail = payload?.detail || detail
    } catch {
      /* keep the status fallback */
    }
    throw new Error(detail)
  }
  if (!response.body) throw new Error('浏览器不支持流式符号下载进度')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let result = null

  function consumeLine(line) {
    if (!line.trim()) return
    const event = JSON.parse(line)
    if (event.type === 'progress') {
      onProgress?.(event.data || {})
    } else if (event.type === 'result') {
      result = event.data || {}
    } else if (event.type === 'error') {
      throw new Error(event.data?.message || '自动下载符号表失败')
    }
  }

  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) consumeLine(line)
    if (done) break
  }
  if (buffer.trim()) consumeLine(buffer)
  if (result == null) throw new Error('符号下载连接结束但未返回结果')
  return result
}

export function getImageStatus(engine = 'vol3') {
  return api.get('/api/image/status', { params: { engine } })
}

export function listImages() {
  return api.get('/api/image/list')
}

export function getPlugins(osFamily, engine = 'vol3') {
  return api.get(`/api/plugins/${osFamily}`, { params: { engine } })
}

export function getPluginArgs(pluginName, engine = 'vol3') {
  return api.get(`/api/plugin-args/${encodeURIComponent(pluginName)}`, { params: { engine } })
}

export function getPluginDocs(pluginName, engine = 'vol3') {
  return api.get(`/api/plugin-docs/${encodeURIComponent(pluginName)}`, { params: { engine } })
}

export function reloadPlugins(engine = 'vol3') {
  return api.post('/api/plugins/reload', null, { params: { engine } })
}

export function getResults(params = {}, options = {}) {
  return api.get('/api/results', { params, signal: options.signal })
}

export function exportResults(format = 'csv', engine = 'vol3', view = null) {
  // view: { filter, sort, desc } — when present the backend exports the
  // filtered/sorted view instead of the full result set.
  return api.post('/api/export', { format, engine, ...(view || {}) })
}

export function clearCache(plugin = null, engine = 'vol3') {
  return api.delete('/api/cache', { data: { plugin, engine } })
}

export function cancelPlugin(engine = 'vol3') {
  return api.delete('/api/plugin/cancel', { params: { engine } })
}

/* ── YARA-X rule centre ───────────────────────────── */

export function listYaraPackages() {
  return api.get('/api/yarax/packages')
}

export function createYaraPackage(payload) {
  return api.post('/api/yarax/packages', payload)
}

export function setYaraPackageEnabled(packageId, enabled) {
  return api.patch(`/api/yarax/packages/${encodeURIComponent(packageId)}`, { enabled })
}

export function deleteYaraPackage(packageId) {
  return api.delete(`/api/yarax/packages/${encodeURIComponent(packageId)}`)
}

export function exportYaraPackageUrl(packageId) {
  return `/api/yarax/packages/${encodeURIComponent(packageId)}/export`
}

export function previewYaraZip(file, entrypoints = []) {
  const form = new FormData()
  form.append('file', file)
  const params = new URLSearchParams()
  for (const entry of entrypoints) params.append('entrypoint', entry)
  return api.post(`/api/yarax/import/preview?${params}`, form, {
    timeout: 0,
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export function confirmYaraZip(payload) {
  return api.post('/api/yarax/import/confirm', payload)
}

export function getYaraDraft(packageId) {
  return api.get(`/api/yarax/packages/${encodeURIComponent(packageId)}/draft`)
}

export function ensureYaraDraft(packageId) {
  return api.post(`/api/yarax/packages/${encodeURIComponent(packageId)}/draft`)
}

export function getYaraDraftFile(packageId, path) {
  return api.get(`/api/yarax/packages/${encodeURIComponent(packageId)}/draft/file`, {
    params: { path },
  })
}

export function searchYaraDraft(packageId, query) {
  return api.get(`/api/yarax/packages/${encodeURIComponent(packageId)}/draft/search`, {
    params: { q: query },
  })
}

export function saveYaraDraftFile(packageId, payload) {
  return api.put(`/api/yarax/packages/${encodeURIComponent(packageId)}/draft/file`, payload)
}

export function deleteYaraDraftFile(packageId, path, baseRevision) {
  return api.delete(`/api/yarax/packages/${encodeURIComponent(packageId)}/draft/file`, {
    data: { path, base_revision: baseRevision },
  })
}

export function renameYaraDraftFile(packageId, oldPath, newPath, baseRevision) {
  return api.post(`/api/yarax/packages/${encodeURIComponent(packageId)}/draft/rename`, {
    old_path: oldPath, new_path: newPath, base_revision: baseRevision,
  })
}

export function validateYaraPackage(packageId, payload = {}) {
  return api.post(`/api/yarax/packages/${encodeURIComponent(packageId)}/validate`, payload)
}

export function commitYaraPackage(packageId, payload = {}) {
  return api.post(`/api/yarax/packages/${encodeURIComponent(packageId)}/commit`, payload)
}

export function getYaraVersions(packageId) {
  return api.get(`/api/yarax/packages/${encodeURIComponent(packageId)}/versions`)
}

export function getYaraVersionDiff(packageId, versionId, otherVersionId = null) {
  return api.get(`/api/yarax/packages/${encodeURIComponent(packageId)}/versions/${encodeURIComponent(versionId)}/diff`, {
    params: { other_version_id: otherVersionId || undefined },
  })
}

export function restoreYaraVersion(packageId, versionId) {
  return api.post(`/api/yarax/packages/${encodeURIComponent(packageId)}/versions/${encodeURIComponent(versionId)}/restore`)
}

export function forkYaraPackage(packageId, name = null) {
  return api.post(`/api/yarax/packages/${encodeURIComponent(packageId)}/fork`, {
    name: name || undefined,
  })
}

export function listYaraMarket() {
  return api.get('/api/yarax/market')
}

export function addYaraMarketSource(payload) {
  return api.post('/api/yarax/market/sources', payload)
}

export function installYaraMarketSource(sourceId, packageId = null) {
  return api.post(`/api/yarax/market/${encodeURIComponent(sourceId)}/install`, {
    package_id: packageId || undefined,
  }, { timeout: 0 })
}

/* ── Symbol helpers ─────────────────────────────────── */

export function getSymbolRepos() {
  return api.get('/api/symbols/repos')
}

export function getRemoteSymbols(params = {}) {
  return api.get('/api/symbols/remote', { params })
}

export function getLocalSymbols() {
  return api.get('/api/symbols/local')
}

export function downloadSymbols(paths, repo = '', useGhProxy = false) {
  return api.post('/api/symbols/download', {
    paths,
    repo,
    use_gh_proxy: Boolean(useGhProxy),
  })
}

/* ── Runtime settings helpers ─────────────────────── */

export function getRuntimeSettings() {
  return api.get('/api/settings')
}

export function saveRuntimeSettings(settings) {
  return api.put('/api/settings', { settings })
}

/* ── WebSocket helper ──────────────────────────────── */

// Reconnect backoff schedule; clamps at the last entry.
const WS_BACKOFF_MS = [1000, 2000, 5000, 10000]

/**
 * Plugin WebSocket with automatic reconnect.
 *
 * States: connecting → open → reconnecting → … → closed (only via close()).
 * Reconnecting never re-sends commands by itself: `send()` returns false when
 * the socket is down, and one-shot `onOpenOnce` callbacks fire on the *next*
 * open only — a queued "run" can never be replayed by a later reconnect.
 *
 * @param {Function} onMessage - parsed-JSON message handler
 * @param {{ onStateChange?: (state: string, info?: object) => void }} opts
 */
export function createPluginSocket(onMessage, { onStateChange } = {}) {
  let ws = null
  let state = 'connecting'
  let attempt = 0
  let reconnectTimer = null
  let closedByUs = false
  const persistentOpenCbs = []
  let onceOpenCbs = []

  function setState(next, info) {
    state = next
    try { onStateChange?.(next, info) } catch (e) { console.error('WS state cb error:', e) }
  }

  function connect() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const token = getApiToken()
    const qs = token ? `?token=${encodeURIComponent(token)}` : ''
    ws = new WebSocket(`${protocol}//${location.host}/ws/plugin${qs}`)

    ws.onopen = () => {
      attempt = 0
      setState('open')
      for (const cb of persistentOpenCbs) cb()
      const once = onceOpenCbs
      onceOpenCbs = []
      for (const cb of once) cb()
    }

    ws.onmessage = (event) => {
      try {
        onMessage(JSON.parse(event.data))
      } catch (e) {
        console.error('WS parse error:', e)
      }
    }

    ws.onerror = (err) => console.error('WS error:', err)

    ws.onclose = () => {
      if (closedByUs) {
        setState('closed')
        return
      }
      const delay = WS_BACKOFF_MS[Math.min(attempt, WS_BACKOFF_MS.length - 1)]
      attempt += 1
      setState('reconnecting', { attempt, delay })
      reconnectTimer = setTimeout(connect, delay)
    }
  }

  connect()

  return {
    /** Send an action; returns false (visibly) when the socket is down. */
    send(action, payload = {}) {
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action, ...payload }))
        return true
      }
      return false
    },
    close() {
      closedByUs = true
      clearTimeout(reconnectTimer)
      ws?.close()
    },
    get ready() {
      return ws?.readyState === WebSocket.OPEN
    },
    get state() {
      return state
    },
    /** Fires on every (re)open — for resubscribe-style logic. */
    onOpen(cb) {
      persistentOpenCbs.push(cb)
    },
    /** Fires once, on the next open only — for queued one-shot sends. */
    onOpenOnce(cb) {
      if (ws?.readyState === WebSocket.OPEN) cb()
      else onceOpenCbs.push(cb)
    },
  }
}

/* ── AI helpers ────────────────────────────────────── */

/**
 * Stream AI chat response via SSE (fetch + ReadableStream).
 */
export function streamAiChat(message, includeContext, onChunk, onDone, onError, onMemoryStatus, onToolCall, onToolResult, engine = 'vol3', conversationId = null, mode = 'agent', osFamily = 'linux') {
  const controller = new AbortController()

  fetch('/api/ai/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({
      message,
      include_context: includeContext,
      engine,
      conversation_id: conversationId || undefined,
      mode,
      os_family: osFamily,
    }),
    signal: controller.signal,
  })
    .then(async (res) => {
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
          if (!line.startsWith('data: ')) continue
          try {
            const payload = JSON.parse(line.slice(6))
            if (payload.type === 'chunk') {
              onChunk(payload.content)
            } else if (payload.type === 'memory_status') {
              if (onMemoryStatus) onMemoryStatus(payload.status || '')
            } else if (payload.type === 'tool_call') {
              if (onToolCall) onToolCall(payload)
            } else if (payload.type === 'tool_result') {
              if (onToolResult) onToolResult(payload)
            } else if (payload.type === 'done') {
              onDone(payload.conversation_id || null)
              return
            } else if (payload.type === 'error') {
              onError(payload.content)
              return
            }
          } catch {
            // ignore non-JSON lines
          }
        }
      }
      onDone(null)
    })
    .catch((err) => {
      if (err.name !== 'AbortError') {
        onError(err.message || 'Network error')
      }
    })

  return { abort: () => controller.abort() }
}

export function getAiHistory() {
  return api.get('/api/ai/history')
}

export function clearAiHistory() {
  return api.delete('/api/ai/history')
}

export function clearAiMemory() {
  return api.delete('/api/ai/memory')
}

export function getAiMemory() {
  return api.get('/api/ai/memory')
}

export function getAiMemoryStats() {
  return api.get('/api/ai/memory/stats')
}

export function getAiConfig() {
  return api.get('/api/ai/config')
}

export function getAiPersistConfig() {
  return api.get('/api/ai/persist-config')
}

export function setAiPersistConfig(enabled) {
  return api.post('/api/ai/persist-config', { persist_to_config_py: !!enabled })
}

export function getAiSettings() {
  return api.get('/api/ai/settings')
}

export function saveAiSettings(settings) {
  return api.post('/api/ai/settings', settings)
}

/* ── AI Profiles ───────────────────────────────────── */

export function getAiProfiles() {
  return api.get('/api/ai/profiles')
}

export function getAiModelCatalog() {
  return api.get('/api/ai/profiles/catalog')
}

export function saveAiProfiles(profiles) {
  return api.post('/api/ai/profiles', { profiles })
}

export function setActiveProfile(profile) {
  return api.post('/api/ai/profiles/active', { profile })
}

export function getActiveProfile() {
  return api.get('/api/ai/profiles/active')
}

export function testAiProfile(profile) {
  return api.post('/api/ai/profiles/test', { profile })
}

/* ── AI Prompts ────────────────────────────────────── */

export function getAiPrompts() {
  return api.get('/api/ai/prompts')
}

export function saveAiPrompt(prompt) {
  return api.post('/api/ai/prompts', prompt)
}

export function deleteAiPrompt(promptId) {
  return api.delete(`/api/ai/prompts/${promptId}`)
}

export function setActivePrompt(promptId) {
  return api.post('/api/ai/prompts/active', { prompt_id: promptId })
}

/* ── Filter syntax ─────────────────────────────────── */

export function getFilterSyntax() {
  return api.get('/api/ai/filter-syntax')
}

/* ── Conversations (persistent history) ────────────── */

export function listConversations() {
  return api.get('/api/ai/conversations')
}

export function createConversation(title = '', engine = 'vol3') {
  return api.post('/api/ai/conversations', { title, engine })
}

export function getConversation(convId) {
  return api.get(`/api/ai/conversations/${convId}`)
}

export function renameConversation(convId, title) {
  return api.patch(`/api/ai/conversations/${convId}`, { title })
}

export function deleteConversation(convId) {
  return api.delete(`/api/ai/conversations/${convId}`)
}

export function loadConversation(convId) {
  return api.post(`/api/ai/conversations/${convId}/load`)
}

/* ── Agent Runtime v1 ───────────────────────────────── */

export function createAiRun(convId, payload) {
  return api.post(`/api/ai/conversations/${convId}/runs`, payload)
}

export function getAiRun(runId) {
  return api.get(`/api/ai/runs/${runId}`)
}

export function cancelAiRun(runId) {
  return api.post(`/api/ai/runs/${runId}/cancel`)
}

export function getConversationContext(convId) {
  return api.get(`/api/ai/conversations/${convId}/context`)
}

export function compactConversation(convId) {
  return api.post(`/api/ai/conversations/${convId}/compact`, {})
}

export function queryAiToolResult(resultId, params) {
  return api.get(`/api/ai/tool-results/${resultId}`, { params })
}

export default api
