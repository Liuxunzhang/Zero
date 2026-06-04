<template>
  <div
    class="sidebar"
    :class="{ collapsed: sidebarCollapsed && !sidebarHoverOpen, 'hover-open': sidebarCollapsed && sidebarHoverOpen }"
    @mouseenter="sidebarHoverOpen = true"
    @mouseleave="sidebarHoverOpen = false"
  >
    <div class="sidebar-header">
      <div class="sidebar-title-row">
        <div class="sidebar-logo">
          <img class="sidebar-logo-icon" src="/favicon.svg" alt="Zero" />
          <span v-if="sidebarExpanded" class="sidebar-logo-text">Zero</span>
        </div>
        <span v-if="sidebarExpanded" class="sidebar-engine-pill">Volatility 3</span>
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
        <div class="os-switch">
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
      </div>

    <div class="sidebar-search">
      <input
        v-model="pluginSearch"
        class="sidebar-search-input"
        type="text"
        placeholder="搜索插件名..."
        @keydown.esc.prevent="pluginSearch = ''"
      />
    </div>

    <div class="sidebar-tree">
      <div
        v-for="(group, category) in filteredCategories"
        :key="category"
        class="tree-category"
      >
        <div class="tree-category-header" @click="toggle(category)">
          <span class="chevron" :class="{ expanded: expanded[category] }">›</span>
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
            :class="{ active: store.currentPlugin === plugin }"
            @click.prevent="selectPlugin(plugin)"
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
        <span :class="{ 'spin': reloading }">⟳</span>
        {{ reloading ? '扫描中...' : '刷新插件' }}
      </button>
      <button
        class="sidebar-collapse-btn"
        @click="toggleSidebar"
        :title="sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'"
      >{{ sidebarCollapsed ? '展开' : '收起' }}</button>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref, watch, computed } from 'vue'
import { useAppStore } from '../stores/app'

const store = useAppStore()

const expanded = reactive({})
const reloading = ref(false)
const pluginSearch = ref('')

// Sidebar collapse state, persisted in localStorage
const SIDEBAR_COLLAPSED_KEY = 'zero-sidebar-collapsed'
const sidebarCollapsed = ref(localStorage.getItem(SIDEBAR_COLLAPSED_KEY) !== 'false')
const sidebarHoverOpen = ref(false)
const sidebarExpanded = computed(() => !sidebarCollapsed.value || sidebarHoverOpen.value)

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

// Auto-expand first category on data change
watch(() => store.categories, (cats) => {
  const keys = Object.keys(cats)
  if (keys.length && !Object.keys(expanded).length) {
    expanded[keys[0]] = true
  }
}, { immediate: true })

watch(filteredCategories, (cats) => {
  const keys = Object.keys(cats)
  if (!keys.length) return
  for (const key of keys) {
    if (!(key in expanded)) expanded[key] = true
  }
}, { immediate: true })

function toggle(category) {
  expanded[category] = !expanded[category]
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
