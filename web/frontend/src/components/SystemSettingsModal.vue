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
              <div class="system-settings-subtitle">Volatility 执行、符号表与结果缓存</div>
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

            <div class="system-settings-storage">
              <span>保存位置</span>
              <code>{{ storagePath || '.zero/runtime_settings.json' }}</code>
            </div>
          </nav>

          <main v-if="activeCategory" class="system-settings-main">
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
import { computed, onMounted, reactive, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import { getRuntimeSettings, saveRuntimeSettings } from '../api'
import { useEscClose } from '../composables/useEscClose'

const emit = defineEmits(['close', 'saved'])
const categories = ref([])
const activeCategoryId = ref('')
const storagePath = ref('')
const loading = ref(true)
const saving = ref(false)
const loadError = ref('')
const saveStatus = ref({ type: '', text: '' })
const formValues = reactive({})
const originalValues = ref({})

useEscClose(() => true, () => emit('close'))

const activeCategory = computed(
  () => categories.value.find((category) => category.id === activeCategoryId.value) || null,
)

const dirty = computed(
  () => JSON.stringify(formValues) !== JSON.stringify(originalValues.value),
)

function assignForm(values) {
  for (const key of Object.keys(formValues)) delete formValues[key]
  Object.assign(formValues, values || {})
  originalValues.value = { ...(values || {}) }
}

async function loadSettings() {
  loading.value = true
  loadError.value = ''
  try {
    const data = await getRuntimeSettings()
    categories.value = data.categories || []
    storagePath.value = data.storage || ''
    assignForm(data.settings || {})
    if (!categories.value.some((category) => category.id === activeCategoryId.value)) {
      activeCategoryId.value = categories.value[0]?.id || ''
    }
  } catch (error) {
    loadError.value = error?.message || '读取运行配置失败'
  } finally {
    loading.value = false
  }
}

function resetActiveCategory() {
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
    const data = await saveRuntimeSettings({ ...formValues })
    assignForm(data.settings || formValues)
    saveStatus.value = { type: 'success', text: '设置已保存并应用' }
    emit('saved', data.settings || {})
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
  .system-setting-control { align-items: stretch; }
  .system-setting-switch { justify-content: flex-start; }
  .system-settings-live { display: none; }
}

@media (prefers-reduced-motion: reduce) {
  .system-settings-spinner { animation-duration: 1.6s; }
}
</style>
