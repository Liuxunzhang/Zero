/**
 * Pinia store for the Volatility 3 Web UI.
 */
import { defineStore } from "pinia"
import { ref, computed, reactive, markRaw } from "vue"
import {
  loadImage as apiLoadImage,
  getImageStatus,
  getPlugins,
  getPluginArgs as apiGetPluginArgs,
  reloadPlugins as apiReload,
  getResults,
  exportResults as apiExport,
  clearCache as apiClearCache,
  cancelPlugin as apiCancel,
  createPluginSocket,
  listEngines as apiListEngines,
  getEngineSettings as apiGetEngineSettings,
} from "../api"

const GLOBAL_ARGS_KEY = "zero-global-args"
const PAGE_SIZE_OPTIONS = [50, 100, 200, 500, 1000]

function makeEngineState() {
  return {
    imagePath: "",
    imageLoaded: false,
    osFamily: "linux",
    categories: {},
    currentPlugin: "",
    runningPlugin: "",
    pluginBusy: false,
    columns: [],
    rows: [],
    totalRows: 0,
    page: 1,
    pageSize: 200,
    totalPages: 1,
    filterText: "",
    sortColumn: "",
    sortDesc: false,
    progress: -1,
    profile: "",
    suggestedProfiles: [],
    available: true,
  }
}

export const useAppStore = defineStore("app", () => {
  const availableEngines = ref([
    { engine_id: "vol3", display_name: "Volatility 3" },
  ])
  const selectedEngine = ref("vol3")
  const engineStates = reactive({
    vol3: makeEngineState(),
  })

  // Active engine state shortcut
  const es = computed(() => engineStates[selectedEngine.value] || engineStates.vol3)

  // Computed shims so existing components work without change
  const imagePath     = computed(() => es.value.imagePath)
  const imageLoaded   = computed(() => es.value.imageLoaded)
  const osFamily      = computed({ get: () => es.value.osFamily, set: v => { es.value.osFamily = v } })
  const categories    = computed(() => es.value.categories)
  const currentPlugin = computed(() => es.value.currentPlugin)
  const runningPlugin = computed(() => es.value.runningPlugin)
  const pluginBusy    = computed(() => es.value.pluginBusy)
  const columns       = computed(() => es.value.columns)
  const rows          = computed(() => es.value.rows)
  const totalRows     = computed(() => es.value.totalRows)
  const page          = computed({ get: () => es.value.page, set: v => { es.value.page = v } })
  const pageSize      = computed({ get: () => es.value.pageSize, set: v => { es.value.pageSize = v } })
  const totalPages    = computed(() => es.value.totalPages)
  const filterText    = computed(() => es.value.filterText)
  const sortColumn    = computed(() => es.value.sortColumn)
  const sortDesc      = computed(() => es.value.sortDesc)
  const progress      = computed({ get: () => es.value.progress, set: v => { es.value.progress = v } })
  const profile       = computed({ get: () => es.value.profile, set: v => { es.value.profile = v } })
  const hasData = computed(() => es.value.columns.length > 0 && es.value.rows.length > 0)
  const visibleRows = computed(() => es.value.rows.length)
  const hasFilter = computed(() => Boolean(String(es.value.filterText || "").trim()))
  const hasSort = computed(() => Boolean(es.value.sortColumn))
  const pluginCount = computed(() => Object.values(es.value.categories || {}).reduce((total, plugins) => {
    return total + (Array.isArray(plugins) ? plugins.length : 0)
  }, 0))

  const messages = ref([])
  const maxMessages = 50
  const initBusy = ref(true)
  const backendReady = ref(false)
  const initError = ref("")

  function pushMessage(text, severity = "info") {
    const ts = new Date().toLocaleTimeString("zh-CN", { hour12: false })
    messages.value.push({ text, severity, ts })
    if (messages.value.length > maxMessages)
      messages.value.splice(0, messages.value.length - maxMessages)
  }

  function requireImageLoaded(actionLabel = "运行插件", engineId = selectedEngine.value) {
    const st = engineStates[engineId]
    if (st?.imageLoaded) return true
    pushMessage("[" + engineId + "] 请先加载内存镜像后再" + actionLabel, "warning")
    return false
  }

  const _sockets = {}
  const resultsAbortControllers = reactive({})
  const latestResultsRequestIds = reactive({})

  async function fetchEngineList() {
    try {
      const data = await apiListEngines()
      if (data && data.engines) {
        availableEngines.value = data.engines
        for (const eng of data.engines) {
          if (!engineStates[eng.engine_id]) {
            engineStates[eng.engine_id] = makeEngineState()
          }
        }
      }
    } catch (e) { console.warn("Could not fetch engine list:", e) }
  }

  const imageLoadBusy = ref(false)

  async function loadImage(path) {
    if (imageLoadBusy.value) return
    const engineId = selectedEngine.value
    const st = engineStates[engineId]
    imageLoadBusy.value = true
    try {
      await apiLoadImage(path, engineId)
      st.imagePath = path
      st.imageLoaded = true
      st.columns = []
      st.rows = []
      st.currentPlugin = ""
      st.runningPlugin = ""
      st.pluginBusy = false
      await fetchEngineSettings(engineId)
      pushMessage("[" + engineId + "] 镜像已加载: " + path, "success")
    } catch (e) {
      pushMessage("[" + engineId + "] 加载失败: " + e.message, "error")
    } finally {
      imageLoadBusy.value = false
    }
  }

  async function fetchPlugins(os) {
    const engineId = selectedEngine.value
    const st = engineStates[engineId]
    try {
      st.osFamily = os
      const data = await getPlugins(os, engineId)
      st.categories = data.categories || {}
    } catch (e) { pushMessage("获取插件列表失败: " + e.message, "error") }
  }

  async function fetchEngineSettings(engineId = selectedEngine.value) {
    const st = engineStates[engineId]
    try {
      const data = await apiGetEngineSettings(engineId)
      const settings = data?.settings || {}
      st.profile = settings.profile || ""
      st.suggestedProfiles = settings.suggested_profiles || []
      st.available = settings.available !== false
    } catch (e) {
      console.warn("Could not fetch engine settings:", e)
    }
  }

  async function fetchResults() {
    const engineId = selectedEngine.value
    const st = engineStates[engineId]
    if (!st.imageLoaded || (!st.currentPlugin && !st.runningPlugin)) return
    const requestId = (latestResultsRequestIds[engineId] || 0) + 1
    latestResultsRequestIds[engineId] = requestId
    if (resultsAbortControllers[engineId]) {
      resultsAbortControllers[engineId].abort()
    }
    resultsAbortControllers[engineId] = new AbortController()
    try {
      const data = await getResults({
        filter: st.filterText || undefined,
        sort: st.sortColumn || undefined,
        desc: st.sortDesc || undefined,
        page: st.page,
        page_size: st.pageSize,
        engine: engineId,
      }, { signal: resultsAbortControllers[engineId].signal })
      if (requestId !== latestResultsRequestIds[engineId]) return
      // markRaw: engineStates is reactive(), so without it every row array and
      // every cell would get its own Proxy (1000 rows x 20 cols = 20k proxies per
      // fetch). Replacing the whole array still triggers re-render; individual
      // cells are never mutated in place.
      st.columns = markRaw(data.columns || [])
      st.rows = markRaw(data.rows || [])
      st.totalRows = data.total || 0
      st.totalPages = data.total_pages || 1
      st.currentPlugin = data.current_plugin || st.runningPlugin || st.currentPlugin
    } catch (e) {
      if (e?.code === "ERR_CANCELED" || e?.name === "CanceledError") return
      pushMessage("获取结果失败: " + e.message, "error")
    } finally {
      if (requestId === latestResultsRequestIds[engineId]) {
        resultsAbortControllers[engineId] = null
      }
    }
  }

  // Busy-recovery pollers, one per engine (see _startBusyRecovery).
  const _recoveryTimers = {}

  function _stopBusyRecovery(engineId) {
    if (_recoveryTimers[engineId]) {
      clearInterval(_recoveryTimers[engineId])
      _recoveryTimers[engineId] = null
    }
  }

  /**
   * The socket died while a plugin was running. The backend keeps running the
   * plugin and the engine retains its results, so poll status until the run
   * finishes, then pull the results over REST.
   */
  function _startBusyRecovery(engineId) {
    if (_recoveryTimers[engineId]) return
    _recoveryTimers[engineId] = setInterval(async () => {
      try {
        const status = await getImageStatus(engineId)
        if (!status.plugin_busy) {
          _stopBusyRecovery(engineId)
          const st = engineStates[engineId]
          if (st) {
            st.pluginBusy = false
            st.progress = -1
            st.runningPlugin = ""
          }
          pushMessage("[" + engineId + "] 连接中断期间插件已在后台完成，已加载结果", "success")
          if (engineId === selectedEngine.value) fetchResults()
        }
      } catch {
        // Backend still down — keep polling.
      }
    }, 3000)
  }

  function _getSocket(engineId) {
    // The socket reconnects itself; reuse it in any non-closed state so we
    // never stack up parallel reconnect loops.
    const existing = _sockets[engineId]
    if (existing && existing.state !== "closed") return existing
    _sockets[engineId] = createPluginSocket((msg) => {
      const msgEngine = msg.engine || engineId
      const target = engineStates[msgEngine]
      if (msg.type === "progress") {
        const text = String(msg.data || "")
        const fromCache = /cached|缓存|saved results|Loaded saved|Using cached/i.test(text)
        pushMessage(fromCache ? ("[" + msgEngine + "] 结果来自缓存 — " + text) : text, fromCache ? "success" : "info")
        const m = text.match(/(\d+(?:\.\d+)?)/)
        if (m && target) target.progress = Math.min(parseFloat(m[1]) * 100, 100)
      } else if (msg.type === "result") {
        const d = msg.data
        if (target) {
          target.page = 1
          target.pluginBusy = false
          target.progress = -1
          if (d?.total != null) target.totalRows = d.total
          if (d?.plugin) target.currentPlugin = d.plugin
          if (Array.isArray(d?.columns) && d.columns.length) target.columns = markRaw(d.columns)
        }
        pushMessage("[" + msgEngine + "] 插件完成: " + d.plugin + " - " + d.total + " 行", "success")
        if (msgEngine === selectedEngine.value) {
          fetchResults().finally(() => {
            if (target) target.runningPlugin = ""
          })
        } else if (target) {
          target.runningPlugin = ""
        }
      } else if (msg.type === "error") {
        if (target) { target.pluginBusy = false; target.progress = -1; target.runningPlugin = "" }
        pushMessage("[" + msgEngine + "] " + msg.data, "error")
      } else if (msg.type === "status") {
        if (target) {
          if (msg.data === "running") { target.pluginBusy = true; target.progress = 0 }
          else { target.pluginBusy = false; target.progress = -1 }
        }
      }
    }, {
      onStateChange: (() => {
        let wasDisconnected = false
        return (state, info) => {
          if (state === "reconnecting") {
            if (info?.attempt === 1) {
              pushMessage("[" + engineId + "] 连接已断开，正在自动重连…", "warning")
            }
            wasDisconnected = true
            if (engineStates[engineId]?.pluginBusy) _startBusyRecovery(engineId)
          } else if (state === "open" && wasDisconnected) {
            wasDisconnected = false
            pushMessage("[" + engineId + "] 连接已恢复", "success")
          }
        }
      })(),
    })
    return _sockets[engineId]
  }

  // Last successful run request — used by force re-run with same params.
  const lastRunPayload = reactive({
    plugin: "",
    engineId: "",
    params: {},
  })

  function runPlugin(pluginName, options = {}) {
    runPluginWithPayload(pluginName, selectedEngine.value, {}, options)
  }

  async function cancelRunningPlugin() {
    const engineId = selectedEngine.value
    const st = engineStates[engineId]
    _stopBusyRecovery(engineId)
    try {
      await apiCancel(engineId)
      st.pluginBusy = false
      st.progress = -1
      st.runningPlugin = ""
      pushMessage("[" + engineId + "] 插件已取消", "warning")
    } catch (e) { pushMessage("取消失败: " + e.message, "error") }
  }

  async function doExport(format = "csv") {
    const engineId = selectedEngine.value
    try {
      const data = await apiExport(format, engineId)
      pushMessage("已导出: " + data.path, "success")
    } catch (e) { pushMessage("导出失败: " + e.message, "error") }
  }

  async function doClearCache(plugin = null) {
    const engineId = selectedEngine.value
    try {
      await apiClearCache(plugin, engineId)
      pushMessage(
        plugin
          ? "缓存已清除（当前表格仍可能显示上次结果，可点「强制重跑」刷新）"
          : "缓存已清除",
        "success",
      )
    } catch (e) { pushMessage("清除失败: " + e.message, "error") }
  }

  function toggleSort(col) {
    const st = engineStates[selectedEngine.value]
    if (st.sortColumn === col) {
      st.sortDesc = !st.sortDesc
    } else {
      st.sortColumn = col
      st.sortDesc = false
    }
    st.page = 1
    fetchResults()
  }

  function resetSort() {
    const st = engineStates[selectedEngine.value]
    if (!st.sortColumn && !st.sortDesc) return
    st.sortColumn = ""
    st.sortDesc = false
    st.page = 1
    fetchResults()
  }

  function setFilter(text) {
    const st = engineStates[selectedEngine.value]
    const next = String(text || "")
    if (st.filterText === next) return
    st.filterText = next
    st.page = 1
    fetchResults()
  }

  function appendFilterCondition(column, operator, rawValue = "") {
    const st = engineStates[selectedEngine.value]
    const col = String(column || "").trim()
    const op = String(operator || "").trim().toLowerCase()
    const escapedColumn = col.replace(/\\/g, "\\\\").replace(/"/g, '\\"')
    const colExpr = '"' + escapedColumn + '"'
    const numberOps = new Set(["gt", "lt", "ge", "le"])
    const stringOps = new Set(["contain", "notcontain", "startswith", "endswith", "match"])
    const equalOps = new Set(["eq", "ne"])
    const valueText = String(rawValue ?? "").trim()
    const asNumber = Number(valueText)
    const hasNumericValue = valueText !== "" && Number.isFinite(asNumber)
    const escapedValue = valueText.replace(/\\/g, "\\\\").replace(/"/g, '\\"')
    let valueExpr = '""'
    if (numberOps.has(op)) valueExpr = hasNumericValue ? String(asNumber) : "0"
    else if (stringOps.has(op)) valueExpr = '"' + escapedValue + '"'
    else if (equalOps.has(op)) valueExpr = hasNumericValue ? String(asNumber) : '"' + escapedValue + '"'
    else return
    const condition = colExpr + " -" + op + " " + valueExpr
    const current = (st.filterText || "").trim()
    setFilter(current ? current + " && " + condition : condition)
  }

  function goToPage(p) {
    const st = engineStates[selectedEngine.value]
    const total = Math.max(1, Number(st.totalPages) || 1)
    const raw = Number(p)
    const target = Math.min(Math.max(1, Number.isFinite(raw) ? Math.trunc(raw) : 1), total)
    if (target === st.page) return
    st.page = target
    fetchResults()
  }

  function setPageSize(size) {
    const st = engineStates[selectedEngine.value]
    const raw = Number(size)
    if (!PAGE_SIZE_OPTIONS.includes(raw) || raw === st.pageSize) return
    st.pageSize = raw
    st.page = 1
    fetchResults()
  }

  async function reloadAllPlugins() {
    const engineId = selectedEngine.value
    try {
      pushMessage("[" + engineId + "] 正在重新扫描插件目录...")
      const data = await apiReload(engineId)
      pushMessage("插件重载完成，共 " + data.plugin_count + " 个插件", "success")
      await fetchPlugins(engineStates[engineId].osFamily || "linux")
    } catch (e) { pushMessage("插件重载失败: " + e.message, "error") }
  }

  async function init() {
    initBusy.value = true
    initError.value = ""
    try {
      _loadGlobalArgs()
      await fetchEngineList()
      backendReady.value = true
      const status = await getImageStatus("vol3")
      const st = engineStates.vol3
      st.imagePath = status.path || ""
      st.imageLoaded = status.loaded || false
      st.osFamily = status.os_family || "linux"
      st.currentPlugin = status.current_plugin || ""
      st.runningPlugin = ""
      st.available = status.available !== false
      await fetchPlugins(engineStates[selectedEngine.value].osFamily || "linux")
    } catch (e) {
      backendReady.value = false
      initError.value = e.message || "初始化失败"
      pushMessage("初始化失败: " + initError.value, "error")
    } finally {
      initBusy.value = false
    }
  }

  // Plugin args modal state (shared across engines)
  const pluginArgsModal = reactive({
    show: false,
    pluginName: '',
    engineId: '',
    args: [],
  })

  // Global args defaults — persisted to localStorage, pre-fill plugin params modal
  const globalArgs = reactive({
    dump_dir: '',
    pid: '',
    offset: '',
    base: '',
    name: '',
    key: '',
    regex: '',
  })

  // ArgsPanel visibility
  const showArgsPanel = ref(false)

  function _loadGlobalArgs() {
    try {
      const raw = localStorage.getItem(GLOBAL_ARGS_KEY)
      if (raw) Object.assign(globalArgs, JSON.parse(raw))
    } catch (_e) {}
  }

  function saveGlobalArgs(values) {
    Object.assign(globalArgs, values)
    localStorage.setItem(GLOBAL_ARGS_KEY, JSON.stringify({ ...globalArgs }))
  }

  function _pickGlobalArgs(argNames) {
    const result = {}
    for (const name of argNames || []) {
      const v = globalArgs[name]
      if (v !== undefined && v !== null && String(v).trim() !== '') {
        result[name] = String(v).trim()
      }
    }
    return result
  }

  async function openPluginWithArgs(pluginName) {
    const engineId = selectedEngine.value
    if (!requireImageLoaded("运行插件", engineId)) return
    try {
      const res = await apiGetPluginArgs(pluginName, engineId)
      const args = res.data?.args ?? res.args ?? []
      const needsModal = res.data?.requires_modal ?? res.requires_modal ?? false
      const argNames = res.data?.arg_names ?? res.arg_names ?? []
      if (!args.length) {
        // No args defined — run immediately
        runPlugin(pluginName)
        return
      }

      if (needsModal) {
        // Show modal for dump/export plugins or plugins with required args
        pluginArgsModal.pluginName = pluginName
        pluginArgsModal.engineId = engineId
        pluginArgsModal.args = args
        pluginArgsModal.globalArgsCopy = { ...globalArgs }
        pluginArgsModal.show = true
      } else {
        // Direct run: auto-merge matching global args
        const merged = _pickGlobalArgs(argNames)
        const usedKeys = Object.keys(merged)
        if (usedKeys.length) {
          const summary = usedKeys.map(k => k + '=' + merged[k]).join(', ')
          pushMessage('已使用默认参数: ' + summary, 'info')
        }
        runPluginWithPayload(pluginName, engineId, merged)
      }
    } catch (_e) {
      // If args endpoint fails, run without params
      runPlugin(pluginName)
    }
  }

  /**
   * Send a plugin run via websocket with arbitrary params.
   * @param {object} options - { force?: boolean } when true, ignore result cache
   */
  function runPluginWithPayload(pluginName, engineId, params, options = {}) {
    const st = engineStates[engineId]
    if (!requireImageLoaded("运行插件", engineId)) return
    if (st.pluginBusy) { pushMessage("[" + engineId + "] 已有插件正在运行", "warning"); return }
    const force = Boolean(options.force)
    const cleanParams = { ...(params || {}) }
    lastRunPayload.plugin = pluginName
    lastRunPayload.engineId = engineId
    lastRunPayload.params = { ...cleanParams }
    st.runningPlugin = pluginName
    st.page = 1
    st.pluginBusy = true
    st.progress = 0
    pushMessage(
      "[" + engineId + "] " + (force ? "强制重跑: " : "运行插件: ") + pluginName + "...",
      force ? "warning" : "info",
    )
    _stopBusyRecovery(engineId)
    const socket = _getSocket(engineId)
    const payload = {
      plugin: pluginName,
      engine: engineId,
      os_family: st.osFamily || "linux",
      ...cleanParams,
    }
    if (force) payload.force = true
    if (!socket.send("run", payload)) {
      // Down right now: queue exactly one send for the next (re)connect.
      pushMessage("[" + engineId + "] 连接未就绪，将在重连后开始运行", "warning")
      socket.onOpenOnce(() => socket.send("run", payload))
    }
  }

  function runPluginWithParams(params) {
    const engineId = pluginArgsModal.engineId || selectedEngine.value
    const pluginName = pluginArgsModal.pluginName
    pluginArgsModal.show = false
    runPluginWithPayload(pluginName, engineId, params)
  }

  /** Re-run last (or current) plugin ignoring cache. */
  function forceRerunCurrentPlugin() {
    const engineId = selectedEngine.value
    const st = engineStates[engineId]
    const plugin =
      lastRunPayload.plugin ||
      st.runningPlugin ||
      st.currentPlugin ||
      ""
    if (!plugin) {
      pushMessage("没有可重跑的插件", "warning")
      return
    }
    const params =
      lastRunPayload.plugin === plugin && lastRunPayload.engineId === engineId
        ? { ...lastRunPayload.params }
        : {}
    runPluginWithPayload(plugin, engineId, params, { force: true })
  }

  return {
    availableEngines, selectedEngine, engineStates,
    fetchEngineList,
    imagePath, imageLoaded, osFamily,
    categories, currentPlugin, runningPlugin, pluginBusy,
    columns, rows, totalRows, page, pageSize, totalPages,
    filterText, sortColumn, sortDesc, profile,
    messages, progress, hasData, hasFilter, hasSort, visibleRows, pluginCount,
    initBusy, backendReady, initError,
    loadImage, imageLoadBusy, fetchPlugins, fetchResults, runPlugin,
    cancelRunningPlugin, doExport, doClearCache,
    toggleSort, resetSort, setFilter, goToPage, setPageSize,
    appendFilterCondition,
    pushMessage, init, reloadAllPlugins, fetchEngineSettings,
    pluginArgsModal, openPluginWithArgs, runPluginWithParams, runPluginWithPayload,
    forceRerunCurrentPlugin, lastRunPayload,
    globalArgs, showArgsPanel, saveGlobalArgs,
  }
})
