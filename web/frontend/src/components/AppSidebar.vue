<template>
  <div class="sidebar" :class="{ collapsed: sidebarCollapsed }">
    <div class="sidebar-header">
      <div class="sidebar-title-row">
        <div class="sidebar-logo">
          <img class="sidebar-logo-icon" src="/favicon.svg" alt="Zero" />
          <span v-if="sidebarExpanded" class="sidebar-logo-text">Zero</span>
        </div>
        <select
          v-if="sidebarExpanded"
          class="sidebar-engine-select"
          :value="store.selectedEngine"
          aria-label="取证引擎"
          @change="store.switchEngine($event.target.value)"
        >
          <option
            v-for="engine in store.availableEngines"
            :key="engine.engine_id"
            :value="engine.engine_id"
            :disabled="engine.available === false"
          >{{ engine.display_name }}</option>
        </select>
      </div>
    </div>

    <template v-if="sidebarExpanded">
      <div class="sidebar-platform-strip">
        <div class="platform-label">
          <span>平台</span>
          <span class="platform-meta">
            {{ store.imageLoaded ? '镜像已加载' : '未加载镜像' }} · {{ store.pluginCount }} 插件
          </span>
        </div>
        <div v-if="store.selectedEngine === 'vol3'" class="os-switch">
          <button
            class="os-btn"
            :class="{ 'active-linux': store.osFamily === 'linux' }"
            @click="switchOS('linux')"
          >Linux</button>
          <button
            class="os-btn"
            :class="{ 'active-windows': store.osFamily === 'windows' }"
            @click="switchOS('windows')"
          >Windows</button>
        </div>
        <button
          v-else
          class="rule-center-entry"
          type="button"
          @click="window.dispatchEvent(new CustomEvent('zero:open-rule-center'))"
        >
          <AppIcon name="package" :size="13" />
          打开规则中心
        </button>
      </div>

    <div class="sidebar-search">
      <input
        ref="searchInputRef"
        v-model="pluginSearch"
        class="sidebar-search-input"
        type="text"
        placeholder="搜索插件名... (Ctrl+K)"
        @keydown.esc.prevent="pluginSearch = ''"
      />
    </div>

    <div class="sidebar-tree" @keydown="onTreeKeydown">
      <div
        v-for="(group, category) in filteredCategories"
        :key="category"
        class="tree-category"
      >
        <div
          class="tree-category-header"
          role="button"
          tabindex="0"
          :aria-expanded="!!expanded[category]"
          @click="toggle(category)"
          @keydown.enter.prevent="toggle(category)"
          @keydown.space.prevent="toggle(category)"
        >
          <span class="chevron" :class="{ expanded: expanded[category] }"><AppIcon name="chevron-right" :size="12" /></span>
          <span>{{ category }}</span>
          <span class="tree-category-count">
            {{ normalizedSearch ? `${group.plugins.length}/${group.total}` : group.total }}
          </span>
        </div>
        <div v-show="expanded[category]" class="tree-plugin-list">
          <a
            v-for="plugin in group.plugins"
            :key="plugin"
            class="tree-plugin"
            role="button"
            tabindex="0"
            :class="{ active: store.currentPlugin === plugin }"
            @click.prevent="selectPlugin(plugin)"
            @keydown.enter.prevent="selectPlugin(plugin)"
            @keydown.space.prevent="selectPlugin(plugin)"
          >
            <template v-if="normalizedSearch">
              <span
                v-for="(part, idx) in highlightPlugin(plugin)"
                :key="`${plugin}-${idx}`"
                :class="{ 'plugin-hit': part.hit }"
              >{{ part.text }}</span>
            </template>
            <template v-else>{{ plugin }}</template>
          </a>
        </div>
      </div>
      <div v-if="!Object.keys(filteredCategories).length" class="sidebar-empty">
        未找到匹配插件
      </div>
    </div>
    </template><!-- end sidebarExpanded -->

    <!-- Footer always visible — collapse button stays at bottom -->
    <div class="sidebar-footer" :class="{ 'sidebar-footer-collapsed': !sidebarExpanded }">
      <button v-if="sidebarExpanded" class="reload-btn" @click="reloadPlugins" :disabled="reloading">
        <AppIcon name="refresh" :size="13" :class="{ spin: reloading }" />
        {{ reloading ? '扫描中...' : '刷新插件' }}
      </button>
      <button
        class="sidebar-collapse-btn"
        @click="toggleSidebar"
        :title="sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'"
      ><AppIcon :name="sidebarCollapsed ? 'chevron-right' : 'panel-left'" :size="14" /></button>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref, watch, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useAppStore } from '../stores/app'
import AppIcon from './AppIcon.vue'

const store = useAppStore()

const EXPANDED_KEY = 'zero-sidebar-expanded'

function restoreExpanded() {
  try {
    const saved = JSON.parse(localStorage.getItem(EXPANDED_KEY) || 'null')
    if (saved && typeof saved === 'object') return saved
  } catch {}
  return {}
}

const expanded = reactive(restoreExpanded())
const reloading = ref(false)
const pluginSearch = ref('')
const searchInputRef = ref(null)
// Category state as it was before a search started, restored on clear.
let expandedBeforeSearch = null

// Sidebar collapse state, persisted in localStorage.
// First run (no saved value) defaults to expanded — a collapsed rail with no
// visible plugin list is a bad first impression.
const SIDEBAR_COLLAPSED_KEY = 'zero-sidebar-collapsed'
const sidebarCollapsed = ref(localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === 'true')
const sidebarExpanded = computed(() => !sidebarCollapsed.value)

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
  localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(sidebarCollapsed.value))
}

const normalizedSearch = computed(() => pluginSearch.value.trim().toLowerCase())

const filteredCategories = computed(() => {
  const result = {}
  const query = normalizedSearch.value
  const source = store.categories || {}

  for (const [category, plugins] of Object.entries(source)) {
    const list = Array.isArray(plugins) ? plugins : []
    const matched = query
      ? list.filter((plugin) => String(plugin).toLowerCase().includes(query))
      : list
    if (matched.length > 0) {
      result[category] = { plugins: matched, total: list.length }
    }
  }
  return result
})

// Auto-expand first category when nothing is expanded yet (fresh profile).
watch(() => store.categories, (cats) => {
  const keys = Object.keys(cats)
  if (keys.length && !Object.keys(expanded).length) {
    expanded[keys[0]] = true
  }
}, { immediate: true })

// Persist expansion across reloads (skipped mid-search: that state is forced).
watch(expanded, (val) => {
  if (expandedBeforeSearch) return
  try { localStorage.setItem(EXPANDED_KEY, JSON.stringify({ ...val })) } catch {}
}, { deep: true })

// While searching, force-expand every matching category so hits are visible;
// snapshot beforehand and restore when the query is cleared.
watch(normalizedSearch, (query, prev) => {
  if (query && !prev) {
    expandedBeforeSearch = { ...expanded }
  }
  if (query) {
    for (const key of Object.keys(filteredCategories.value)) {
      expanded[key] = true
    }
  } else if (prev && expandedBeforeSearch) {
    for (const key of Object.keys(expanded)) delete expanded[key]
    Object.assign(expanded, expandedBeforeSearch)
    expandedBeforeSearch = null
  }
})

// Categories matched by an ongoing search stay expanded as the query narrows.
watch(filteredCategories, (cats) => {
  if (!normalizedSearch.value) return
  for (const key of Object.keys(cats)) expanded[key] = true
})

function toggle(category) {
  expanded[category] = !expanded[category]
}

// Arrow-key navigation across the visible tree items (category headers +
// expanded plugins). Enter/Space activation lives on the elements themselves.
function onTreeKeydown(e) {
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
  const root = e.currentTarget
  const items = Array.from(root.querySelectorAll('[tabindex="0"]')).filter(
    (el) => el.offsetParent !== null,
  )
  const idx = items.indexOf(document.activeElement)
  if (idx < 0) return
  e.preventDefault()
  const next = e.key === 'ArrowDown'
    ? Math.min(idx + 1, items.length - 1)
    : Math.max(idx - 1, 0)
  items[next]?.focus()
}

function switchOS(os) {
  store.fetchPlugins(os)
}

function selectPlugin(plugin) {
  store.openPluginWithArgs(plugin)
}

function highlightPlugin(plugin) {
  const text = String(plugin)
  const query = normalizedSearch.value
  if (!query) return [{ text, hit: false }]

  const source = text.toLowerCase()
  const parts = []
  let start = 0

  while (start < text.length) {
    const hit = source.indexOf(query, start)
    if (hit < 0) {
      parts.push({ text: text.slice(start), hit: false })
      break
    }
    if (hit > start) {
      parts.push({ text: text.slice(start, hit), hit: false })
    }
    const end = hit + query.length
    parts.push({ text: text.slice(hit, end), hit: true })
    start = end
  }

  return parts
}

async function focusPluginSearch() {
  sidebarCollapsed.value = false
  localStorage.setItem(SIDEBAR_COLLAPSED_KEY, 'false')
  await nextTick() // the search input only exists once the tree re-renders
  searchInputRef.value?.focus()
}

onMounted(() => window.addEventListener('zero:focus-plugin-search', focusPluginSearch))
onUnmounted(() => window.removeEventListener('zero:focus-plugin-search', focusPluginSearch))

async function reloadPlugins() {
  reloading.value = true
  try {
    await store.reloadAllPlugins()
  } finally {
    reloading.value = false
  }
}

</script>

<style scoped>
.sidebar-header {
  padding: 9px 10px;
  border-bottom: 1px solid var(--border-subtle);
}

.sidebar-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.sidebar-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.sidebar-logo-icon {
  width: 28px;
  height: 28px;
  display: block;
  border-radius: 8px;
  box-shadow: 0 6px 14px color-mix(in srgb, var(--accent-glow) 70%, transparent);
}

.sidebar-logo-text {
  color: var(--text-primary);
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.sidebar-engine-pill {
  flex-shrink: 0;
  padding: 5px 9px;
  border: 1px solid var(--border-subtle);
  border-radius: 999px;
  background: color-mix(in srgb, var(--accent-glow) 82%, var(--bg-elevated));
  color: var(--accent-bright);
  font-size: 11px;
  font-weight: 700;
  white-space: nowrap;
}

.sidebar-engine-select {
  min-width: 0;
  max-width: 132px;
  height: 28px;
  padding: 0 24px 0 9px;
  border: 1px solid var(--border-subtle);
  border-radius: 999px;
  background: var(--bg-elevated);
  color: var(--accent-bright);
  font: 700 11px var(--font-sans);
  cursor: pointer;
}

.rule-center-entry {
  width: 100%;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  border: 1px solid var(--accent-dim);
  border-radius: 9px;
  color: var(--accent-bright);
  background: var(--accent-glow);
  cursor: pointer;
  font-weight: 650;
}

.sidebar-platform-strip {
  padding: 7px 10px;
  border-bottom: 1px solid var(--border-subtle);
}

.sidebar-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  padding: 12px 14px 10px;
  border-bottom: 1px solid var(--border-subtle);
}

.sidebar-summary-item {
  padding: 10px 8px;
  background: color-mix(in srgb, var(--bg-elevated) 92%, transparent);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  text-align: center;
}

.sidebar-summary-label {
  display: block;
  color: var(--text-muted);
  font-size: 10px;
  margin-bottom: 4px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.sidebar-summary-value {
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 700;
}

.sidebar-summary-value.ready {
  color: var(--text-success);
}

.platform-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
  color: var(--text-muted);
  font-size: 11px;
}

.platform-meta {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 10px;
}

.os-switch {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.os-btn {
  height: 28px;
  border-radius: 9px;
  border: 1px solid var(--border);
  background: var(--bg-elevated);
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.os-btn:hover {
  border-color: var(--accent-dim);
  color: var(--text-primary);
}

.os-btn.active-linux {
  border-color: color-mix(in srgb, var(--accent) 82%, white 18%);
  background: color-mix(in srgb, var(--accent-glow) 90%, var(--bg-elevated));
  color: var(--accent-bright);
}

.os-btn.active-windows {
  border-color: color-mix(in srgb, var(--accent-windows) 82%, white 18%);
  background: color-mix(in srgb, var(--accent-windows-glow) 90%, var(--bg-elevated));
  color: var(--accent-windows);
}

.sidebar-search {
  padding: 7px 10px;
  border-bottom: 1px solid var(--border-subtle);
}

.sidebar-search-input {
  width: 100%;
  height: 28px;
  padding: 0 10px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font-size: 12px;
  outline: none;
}

.sidebar-search-input:focus {
  border-color: var(--accent-dim);
}

.sidebar-empty {
  padding: 14px 16px;
  color: var(--text-muted);
  font-size: 12px;
}

.plugin-hit {
  color: var(--accent-bright);
  background: color-mix(in srgb, var(--accent-glow) 80%, transparent);
  border-radius: 3px;
  padding: 0 1px;
}

.sidebar-footer {
  padding: 7px 10px;
  border-top: 1px solid var(--border);
  flex-shrink: 0;
  display: flex;
  gap: 6px;
  align-items: center;
}

.sidebar-footer-collapsed {
  margin-top: auto;
  padding: 10px 8px;
  justify-content: center;
}

.reload-btn {
  flex: 1;
  padding: 6px 0;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: all var(--transition-fast);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.reload-btn:hover:not(:disabled) {
  background: var(--accent-glow);
  border-color: var(--accent-dim);
  color: var(--accent-bright);
}

.reload-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.spin {
  display: inline-block;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* ── Sidebar collapse button ──────────────────── */
.sidebar-collapse-btn {
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  padding: 0;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  font-size: 11px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all var(--transition-fast);
}

.sidebar-collapse-btn:hover {
  color: var(--text-primary);
  border-color: var(--accent-dim);
}
</style>
