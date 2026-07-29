<template>
  <Teleport to="body">
    <div class="system-settings-overlay" @click.self="emit('close')">
      <section
        class="system-settings-modal"
        role="dialog"
        aria-modal="true"
        aria-label="系统运行设置"
      >
        <header class="system-settings-header">
          <div class="system-settings-heading">
            <span class="system-settings-icon">
              <AppIcon name="wrench" :size="15" />
            </span>
            <div>
              <div class="system-settings-title">系统运行设置</div>
              <div class="system-settings-subtitle">运行配置、符号表、缓存与插件默认参数</div>
            </div>
            <span class="system-settings-live">即时生效</span>
          </div>
          <button class="system-settings-close" title="关闭" @click="emit('close')">
            <AppIcon name="x" :size="14" />
          </button>
        </header>

        <div v-if="loading" class="system-settings-loading">
          <span class="system-settings-spinner"></span>
          正在读取运行配置...
        </div>

        <div v-else-if="loadError" class="system-settings-load-error">
          <AppIcon name="alert-triangle" :size="18" />
          <span>{{ loadError }}</span>
          <button @click="loadSettings">重试</button>
        </div>

        <div v-else class="system-settings-content">
          <nav class="system-settings-nav" aria-label="设置分类">
            <button
              v-for="category in categories"
              :key="category.id"
              class="system-settings-nav-item"
              :class="{ active: activeCategoryId === category.id }"
              @click="activeCategoryId = category.id"
            >
              <span class="system-settings-nav-mark"></span>
              <span>
                <strong>{{ category.label }}</strong>
                <small>{{ category.fields.length }} 项</small>
              </span>
            </button>

            <button
              v-if="engineId === 'vol3'"
              class="system-settings-nav-item"
              :class="{ active: activeCategoryId === PLUGIN_ARGS_CATEGORY_ID }"
              @click="activeCategoryId = PLUGIN_ARGS_CATEGORY_ID"
            >
              <span class="system-settings-nav-mark"></span>
              <span>
                <strong>插件参数</strong>
                <small>{{ configuredArgsCount }} 项已设置</small>
              </span>
            </button>

            <div class="system-settings-storage">
              <span>保存位置</span>
              <code>{{ storagePath || '.zero/runtime_settings.json' }}</code>
            </div>
          </nav>

          <main
            v-if="activeCategoryId === PLUGIN_ARGS_CATEGORY_ID"
            class="system-settings-main"
          >
            <div class="system-settings-category-head">
              <div>
                <h2>插件默认参数</h2>
                <p>为常用 Volatility 插件预填参数；运行单个插件前仍可覆盖。</p>
              </div>
              <button class="system-settings-reset" @click="resetActiveCategory">
                清空本页参数
              </button>
            </div>

            <div class="plugin-args-summary">
              <span class="plugin-args-engine">{{ engineId }}</span>
              <span>仅把当前插件支持的字段带入运行窗口，不支持的字段会自动忽略。</span>
            </div>

            <div class="plugin-args-groups">
              <section
                v-for="group in pluginArgGroups"
                :key="group.id"
                class="plugin-args-group"
              >
                <header class="plugin-args-group-head">
                  <div>
                    <strong>{{ group.label }}</strong>
                    <p>{{ group.description }}</p>
                  </div>
                  <span>{{ group.code }}</span>
                </header>
                <div class="plugin-args-grid" :class="{ single: group.fields.length === 1 }">
                  <label
                    v-for="field in group.fields"
                    :key="field.key"
                    class="plugin-arg-field"
                  >
                    <span class="plugin-arg-label">
                      <strong>{{ field.label }}</strong>
                      <code>{{ field.flag }}</code>
                    </span>
                    <input
                      v-model="argsValues[field.key]"
                      type="text"
                      :placeholder="field.placeholder"
                    />
                    <small>{{ field.help }}</small>
                  </label>
                </div>
              </section>
            </div>
          </main>

          <main v-else-if="activeCategory" class="system-settings-main">
            <div class="system-settings-category-head">
              <div>
                <h2>{{ activeCategory.label }}</h2>
                <p>{{ activeCategory.description }}</p>
              </div>
              <button class="system-settings-reset" @click="resetActiveCategory">
                恢复本页默认值
              </button>
            </div>

            <div class="system-settings-fields">
              <article
                v-for="field in activeCategory.fields"
                :key="field.key"
                class="system-setting-card"
              >
                <div class="system-setting-copy">
                  <label :for="`setting-${field.key}`">{{ field.label }}</label>
                  <p>{{ field.description }}</p>
                  <span class="system-setting-default">
                    默认：{{ formatValue(field.default, field) }}
                  </span>
                </div>

                <div class="system-setting-control">
                  <label
                    v-if="field.type === 'boolean'"
                    class="system-setting-switch"
                    :for="`setting-${field.key}`"
                  >
                    <input
                      :id="`setting-${field.key}`"
                      v-model="formValues[field.key]"
                      type="checkbox"
                    />
                    <span class="system-setting-switch-track">
                      <span></span>
                    </span>
                    <strong>{{ formValues[field.key] ? '开启' : '关闭' }}</strong>
                  </label>

                  <select
                    v-else-if="field.type === 'select'"
                    :id="`setting-${field.key}`"
                    v-model="formValues[field.key]"
                    class="system-setting-select"
                  >
                    <option v-for="option in field.options" :key="option" :value="option">
                      {{ option }}
                    </option>
                  </select>

                  <div v-else class="system-setting-number-wrap">
                    <input
                      :id="`setting-${field.key}`"
                      v-model.number="formValues[field.key]"
                      class="system-setting-number"
                      type="number"
                      :min="field.min"
                      :max="field.max"
                      :step="field.step || 1"
                    />
                    <span v-if="field.unit">{{ field.unit }}</span>
                  </div>

                  <span
                    v-if="field.type === 'integer' || field.type === 'number'"
                    class="system-setting-range"
                  >
                    {{ rangeText(field) }}
                  </span>
                </div>
              </article>
            </div>
          </main>
        </div>

        <footer v-if="!loading && !loadError" class="system-settings-footer">
          <div class="system-settings-status" :class="saveStatus.type">
            {{ saveStatus.text || (dirty ? '存在尚未保存的修改' : '所有设置已保存') }}
          </div>
          <div class="system-settings-actions">
            <button class="system-settings-cancel" @click="emit('close')">关闭</button>
            <button
              class="system-settings-save"
              :disabled="saving || !dirty"
              @click="saveSettings"
            >
              {{ saving ? '保存中...' : '保存并应用' }}
            </button>
          </div>
        </footer>
      </section>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import { getRuntimeSettings, saveRuntimeSettings } from '../api'
import { useEscClose } from '../composables/useEscClose'

const props = defineProps({
  engineId: { type: String, default: 'vol3' },
  globalArgs: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['close', 'saved', 'update:globalArgs'])
const PLUGIN_ARGS_CATEGORY_ID = 'plugin_args'
const EMPTY_ARGS = () => ({
  dump_dir: '',
  pid: '',
  offset: '',
  base: '',
  name: '',
  key: '',
  regex: '',
})
const pluginArgGroups = [
  {
    id: 'output',
    code: 'OUT',
    label: '导出目录',
    description: '适用于 dlldump、procdump、memdump 等导出类插件。',
    fields: [
      {
        key: 'dump_dir',
        label: 'dump_dir',
        flag: '--dump-dir',
        placeholder: '例如 saved_results/vol3/dumps',
        help: '相对路径基于项目根目录解析，目录不存在时自动创建。',
      },
    ],
  },
  {
    id: 'process',
    code: 'PROC',
    label: '进程与对象定位',
    description: '预填进程、内核对象或基地址，便于连续验证同一目标。',
    fields: [
      { key: 'pid', label: 'pid', flag: '--pid', placeholder: '进程 PID', help: '整数，可用于 handles、malfind 等插件。' },
      { key: 'offset', label: 'offset', flag: '--offset', placeholder: '对象偏移，如 0x…', help: '支持插件所需的十六进制对象偏移。' },
      { key: 'base', label: 'base', flag: '--base', placeholder: '基地址，如 0x…', help: '模块或映像基地址。' },
      { key: 'name', label: 'name', flag: '--name', placeholder: '对象或模块名称', help: '按名称缩小插件检查范围。' },
    ],
  },
  {
    id: 'search',
    code: 'FIND',
    label: '注册表与搜索',
    description: '复用注册表路径和正则条件，减少重复输入。',
    fields: [
      { key: 'key', label: 'key', flag: '--key', placeholder: 'SOFTWARE\\Microsoft\\Windows\\…', help: '注册表键路径，无需 HKEY_LOCAL_MACHINE 前缀。' },
      { key: 'regex', label: 'regex', flag: '--regex', placeholder: '例如 \\.pdf$', help: '用于支持正则筛选的插件。' },
    ],
  },
]
const categories = ref([])
const activeCategoryId = ref('')
const storagePath = ref('')
const loading = ref(true)
const saving = ref(false)
const loadError = ref('')
const saveStatus = ref({ type: '', text: '' })
const formValues = reactive({})
const originalValues = ref({})
const argsValues = reactive(EMPTY_ARGS())
const originalArgs = ref(EMPTY_ARGS())

useEscClose(() => true, () => emit('close'))

const activeCategory = computed(
  () => categories.value.find((category) => category.id === activeCategoryId.value) || null,
)

const runtimeDirty = computed(
  () => JSON.stringify(formValues) !== JSON.stringify(originalValues.value),
)
const argsDirty = computed(
  () => JSON.stringify(argsValues) !== JSON.stringify(originalArgs.value),
)
const dirty = computed(() => runtimeDirty.value || argsDirty.value)
const configuredArgsCount = computed(
  () => Object.values(argsValues).filter((value) => String(value || '').trim()).length,
)

function assignForm(values) {
  for (const key of Object.keys(formValues)) delete formValues[key]
  Object.assign(formValues, values || {})
  originalValues.value = { ...(values || {}) }
}

function assignArgs(values) {
  Object.assign(argsValues, EMPTY_ARGS(), values || {})
  originalArgs.value = { ...argsValues }
}

async function loadSettings() {
  loading.value = true
  loadError.value = ''
  try {
    const data = await getRuntimeSettings()
    categories.value = data.categories || []
    storagePath.value = data.storage || ''
    assignForm(data.settings || {})
    assignArgs(props.globalArgs)
    const categoryExists =
      categories.value.some((category) => category.id === activeCategoryId.value) ||
      (props.engineId === 'vol3' && activeCategoryId.value === PLUGIN_ARGS_CATEGORY_ID)
    if (!categoryExists) {
      activeCategoryId.value = categories.value[0]?.id || ''
    }
  } catch (error) {
    loadError.value = error?.message || '读取运行配置失败'
  } finally {
    loading.value = false
  }
}

function resetActiveCategory() {
  if (activeCategoryId.value === PLUGIN_ARGS_CATEGORY_ID) {
    Object.assign(argsValues, EMPTY_ARGS())
    saveStatus.value = { type: '', text: '' }
    return
  }
  for (const field of activeCategory.value?.fields || []) {
    formValues[field.key] = field.default
  }
  saveStatus.value = { type: '', text: '' }
}

async function saveSettings() {
  if (saving.value || !dirty.value) return
  saving.value = true
  saveStatus.value = { type: '', text: '' }
  try {
    let savedSettings = { ...formValues }
    if (runtimeDirty.value) {
      const data = await saveRuntimeSettings({ ...formValues })
      savedSettings = data.settings || formValues
      assignForm(savedSettings)
    }
    if (argsDirty.value) {
      const cleanArgs = {}
      for (const [key, value] of Object.entries(argsValues)) {
        const cleaned = String(value || '').trim()
        if (cleaned) cleanArgs[key] = cleaned
      }
      emit('update:globalArgs', cleanArgs)
      assignArgs(cleanArgs)
    }
    saveStatus.value = { type: 'success', text: '设置已保存并应用' }
    emit('saved', savedSettings)
  } catch (error) {
    saveStatus.value = { type: 'error', text: error?.message || '保存设置失败' }
  } finally {
    saving.value = false
  }
}

function formatValue(value, field) {
  if (field.type === 'boolean') return value ? '开启' : '关闭'
  return `${value}${field.unit || ''}`
}

function rangeText(field) {
  if (field.min == null && field.max == null) return ''
  return `范围 ${field.min ?? '不限'}–${field.max ?? '不限'}${field.unit || ''}`
}

watch(
  () => props.globalArgs,
  (values) => {
    if (!argsDirty.value) assignArgs(values)
  },
  { deep: true },
)

onMounted(loadSettings)
</script>

<style scoped>
.system-settings-overlay {
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

.system-settings-modal {
  width: min(920px, 96vw);
  height: min(720px, 88vh);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg-secondary);
  box-shadow: var(--shadow-xl);
}

.system-settings-header {
  min-height: 62px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 0 18px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-tertiary);
  flex-shrink: 0;
}

.system-settings-heading {
  display: flex;
  align-items: center;
  gap: 10px;
}

.system-settings-icon {
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

.system-settings-title {
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 700;
}

.system-settings-subtitle {
  margin-top: 2px;
  color: var(--text-muted);
  font-size: 10px;
}

.system-settings-live {
  margin-left: 4px;
  padding: 3px 8px;
  color: var(--text-success);
  border: 1px solid color-mix(in srgb, var(--text-success) 35%, var(--border));
  border-radius: 999px;
  background: color-mix(in srgb, var(--text-success) 9%, transparent);
  font-size: 10px;
}

.system-settings-close {
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

.system-settings-close:hover {
  color: var(--text-primary);
  border-color: var(--accent-dim);
}

.system-settings-loading,
.system-settings-load-error {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: var(--text-muted);
  font-size: 12px;
}

.system-settings-load-error {
  color: var(--text-error);
}

.system-settings-load-error button {
  height: 28px;
  padding: 0 10px;
  color: var(--text-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
  cursor: pointer;
}

.system-settings-spinner {
  width: 16px;
  height: 16px;
  border: 2px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: settings-spin 0.8s linear infinite;
}

@keyframes settings-spin {
  to { transform: rotate(360deg); }
}

.system-settings-content {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 190px minmax(0, 1fr);
}

.system-settings-nav {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 14px 10px;
  border-right: 1px solid var(--border-subtle);
  background: var(--bg-primary);
}

.system-settings-nav-item {
  width: 100%;
  display: grid;
  grid-template-columns: 3px minmax(0, 1fr);
  gap: 9px;
  padding: 10px 9px;
  text-align: left;
  color: var(--text-secondary);
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  cursor: pointer;
}

.system-settings-nav-item:hover {
  background: var(--bg-hover);
}

.system-settings-nav-item.active {
  color: var(--text-primary);
  border-color: var(--border);
  background: var(--bg-elevated);
}

.system-settings-nav-mark {
  width: 3px;
  height: 100%;
  min-height: 28px;
  border-radius: 999px;
  background: var(--border);
}

.system-settings-nav-item.active .system-settings-nav-mark {
  background: var(--accent);
  box-shadow: 0 0 10px var(--accent-glow);
}

.system-settings-nav-item strong,
.system-settings-nav-item small {
  display: block;
}

.system-settings-nav-item strong {
  font-size: 12px;
  font-weight: 600;
}

.system-settings-nav-item small {
  margin-top: 3px;
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 9px;
}

.system-settings-storage {
  margin-top: auto;
  padding: 10px 8px;
  color: var(--text-muted);
  font-size: 9px;
}

.system-settings-storage span,
.system-settings-storage code {
  display: block;
}

.system-settings-storage code {
  margin-top: 4px;
  overflow-wrap: anywhere;
  color: var(--text-secondary);
  font-size: 9px;
}

.system-settings-main {
  min-width: 0;
  overflow-y: auto;
  padding: 18px 20px 24px;
}

.system-settings-category-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.system-settings-category-head h2 {
  margin: 0;
  color: var(--text-primary);
  font-size: 16px;
}

.system-settings-category-head p {
  margin: 5px 0 0;
  color: var(--text-muted);
  font-size: 11px;
}

.system-settings-reset {
  flex-shrink: 0;
  padding: 5px 9px;
  color: var(--text-muted);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
  font-size: 10px;
  cursor: pointer;
}

.system-settings-reset:hover {
  color: var(--text-primary);
  border-color: var(--accent-dim);
}

.system-settings-fields {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.system-setting-card {
  min-height: 72px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 210px;
  align-items: center;
  gap: 20px;
  padding: 11px 13px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--bg-primary);
}

.system-setting-card:focus-within {
  border-color: var(--accent-dim);
}

.system-setting-copy label {
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 600;
}

.system-setting-copy p {
  margin: 4px 0;
  color: var(--text-muted);
  font-size: 10px;
  line-height: 1.45;
}

.system-setting-default {
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 9px;
  opacity: 0.75;
}

.system-setting-control {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 4px;
}

.system-setting-number-wrap {
  height: 30px;
  display: flex;
  align-items: center;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
}

.system-setting-number {
  flex: 1;
  min-width: 0;
  height: 100%;
  padding: 0 9px;
  color: var(--text-primary);
  border: none;
  outline: none;
  background: transparent;
  font-family: var(--font-mono);
  font-size: 11px;
}

.system-setting-number-wrap > span {
  padding: 0 9px;
  color: var(--text-muted);
  border-left: 1px solid var(--border-subtle);
  font-size: 10px;
}

.system-setting-number-wrap:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-glow);
}

.system-setting-select {
  height: 30px;
  padding: 0 9px;
  color: var(--text-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  outline: none;
  background: var(--bg-elevated);
  font-family: var(--font-mono);
  font-size: 11px;
}

.system-setting-range {
  color: var(--text-muted);
  font-size: 9px;
  text-align: right;
}

.system-setting-switch {
  min-height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  cursor: pointer;
}

.system-setting-switch input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.system-setting-switch-track {
  width: 34px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  padding: 2px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--bg-elevated);
  transition: all var(--transition-fast);
}

.system-setting-switch-track span {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--text-muted);
  transition: all var(--transition-fast);
}

.system-setting-switch input:checked + .system-setting-switch-track {
  border-color: var(--accent-dim);
  background: var(--accent-glow);
}

.system-setting-switch input:checked + .system-setting-switch-track span {
  transform: translateX(16px);
  background: var(--accent-bright);
}

.system-setting-switch input:focus-visible + .system-setting-switch-track {
  outline: 2px solid var(--accent-glow);
  outline-offset: 2px;
}

.system-setting-switch strong {
  min-width: 24px;
  color: var(--text-secondary);
  font-size: 10px;
}

.plugin-args-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  padding: 9px 11px;
  color: var(--text-muted);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--bg-primary);
  font-size: 10px;
}

.plugin-args-engine {
  flex-shrink: 0;
  padding: 2px 6px;
  color: var(--accent-bright);
  border: 1px solid var(--accent-dim);
  border-radius: 3px;
  background: var(--accent-glow);
  font-family: var(--font-mono);
  font-size: 9px;
}

.plugin-args-groups {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.plugin-args-group {
  overflow: hidden;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--bg-primary);
}

.plugin-args-group:focus-within {
  border-color: var(--accent-dim);
}

.plugin-args-group-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px 9px;
  border-bottom: 1px solid var(--border-subtle);
  background: var(--bg-tertiary);
}

.plugin-args-group-head strong {
  display: block;
  color: var(--text-primary);
  font-size: 11px;
}

.plugin-args-group-head p {
  margin: 3px 0 0;
  color: var(--text-muted);
  font-size: 9px;
}

.plugin-args-group-head > span {
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.08em;
}

.plugin-args-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 12px;
  padding: 11px 12px 12px;
}

.plugin-args-grid.single {
  grid-template-columns: 1fr;
}

.plugin-arg-field {
  min-width: 0;
}

.plugin-arg-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 5px;
}

.plugin-arg-label strong {
  color: var(--text-secondary);
  font-size: 10px;
  font-weight: 600;
}

.plugin-arg-label code {
  color: var(--accent-bright);
  font-family: var(--font-mono);
  font-size: 9px;
}

.plugin-arg-field input {
  width: 100%;
  height: 30px;
  padding: 0 9px;
  color: var(--text-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  outline: none;
  background: var(--bg-elevated);
  font-family: var(--font-mono);
  font-size: 10px;
}

.plugin-arg-field input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-glow);
}

.plugin-arg-field input::placeholder {
  color: var(--text-muted);
}

.plugin-arg-field small {
  display: block;
  margin-top: 4px;
  color: var(--text-muted);
  font-size: 8px;
  line-height: 1.35;
}

.system-settings-footer {
  min-height: 54px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 18px;
  border-top: 1px solid var(--border);
  background: var(--bg-tertiary);
  flex-shrink: 0;
}

.system-settings-status {
  color: var(--text-muted);
  font-size: 10px;
}

.system-settings-status.success { color: var(--text-success); }
.system-settings-status.error { color: var(--text-error); }

.system-settings-actions {
  display: flex;
  gap: 8px;
}

.system-settings-cancel,
.system-settings-save {
  height: 30px;
  padding: 0 13px;
  border-radius: var(--radius-sm);
  font-size: 11px;
  cursor: pointer;
}

.system-settings-cancel {
  color: var(--text-secondary);
  border: 1px solid var(--border);
  background: var(--bg-elevated);
}

.system-settings-save {
  color: var(--load-btn-text);
  border: 1px solid transparent;
  background: linear-gradient(135deg, var(--accent-dim), var(--accent));
  font-weight: 600;
}

.system-settings-save:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

@media (max-width: 720px) {
  .system-settings-overlay { padding: 10px; }
  .system-settings-modal { width: 100%; height: 94vh; }
  .system-settings-content { grid-template-columns: 1fr; grid-template-rows: auto minmax(0, 1fr); }
  .system-settings-nav {
    flex-direction: row;
    overflow-x: auto;
    border-right: none;
    border-bottom: 1px solid var(--border-subtle);
  }
  .system-settings-nav-item { min-width: 135px; }
  .system-settings-storage { display: none; }
  .system-settings-main { padding: 14px; }
  .system-setting-card { grid-template-columns: 1fr; gap: 10px; }
  .plugin-args-grid { grid-template-columns: 1fr; }
  .system-setting-control { align-items: stretch; }
  .system-setting-switch { justify-content: flex-start; }
  .system-settings-live { display: none; }
}

@media (prefers-reduced-motion: reduce) {
  .system-settings-spinner { animation-duration: 1.6s; }
}
</style>
