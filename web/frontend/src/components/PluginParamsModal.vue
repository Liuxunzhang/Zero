<template>
  <Teleport to="body">
    <div v-if="show" class="modal-overlay" @click.self="cancel">
      <div class="modal-box" :class="{ 'with-help': showHelp && doc }">
        <div class="modal-header">
          <span class="modal-title">
            <span class="plugin-badge">{{ engineId }}</span>
            {{ pluginName }}
          </span>
          <div class="header-actions">
            <button
              v-if="doc"
              class="help-btn"
              :class="{ active: showHelp }"
              @click="toggleHelp"
              :title="showHelp ? '隐藏插件说明' : '查看插件说明'"
            >说明</button>
            <button class="close-btn" @click="cancel">关闭</button>
          </div>
        </div>

        <div class="modal-layout">
          <!-- Left: parameter form -->
          <div class="modal-body">
            <p v-if="!args.length" class="no-args">
              <span class="no-args-icon"><AppIcon name="check-circle" :size="15" /></span>
              此插件无需额外参数，直接运行即可。
            </p>

            <form v-else @submit.prevent="confirm">
              <div v-for="arg in args" :key="arg.name" class="field-row">
                <label :for="arg.name" :class="{ required: arg.required }">
                  <span class="arg-name">{{ arg.name }}</span>
                  <span v-if="arg.required" class="req-badge">必填</span>
                  <code class="flag-badge">{{ arg.flag }}</code>
                  <span v-if="isPreFilled(arg)" class="prefill-badge" title="来自全局参数配置">自动填充</span>
                </label>

                <!-- bool type uses checkbox -->
                <input
                  v-if="arg.arg_type === 'bool'"
                  :id="arg.name"
                  type="checkbox"
                  v-model="form[arg.name]"
                  class="field-checkbox"
                />

                <!-- select / choice type uses dropdown -->
                <select
                  v-else-if="(arg.arg_type === 'select' || arg.arg_type === 'choice') && arg.options && arg.options.length"
                  :id="arg.name"
                  v-model="form[arg.name]"
                  class="field-input"
                >
                  <option value="">— 不指定 —</option>
                  <option v-for="opt in arg.options" :key="opt" :value="opt">{{ opt }}</option>
                </select>

                <!-- everything else uses text input -->
                <input
                  v-else
                  :id="arg.name"
                  type="text"
                  v-model="form[arg.name]"
                  :placeholder="arg.placeholder || arg.flag"
                  class="field-input"
                  :class="{ prefilled: isPreFilled(arg) && form[arg.name] }"
                />

                <p class="field-help">{{ arg.help }}</p>
              </div>
            </form>

            <p v-if="error" class="field-error">错误: {{ error }}</p>
          </div>

          <!-- Right: plugin help panel -->
          <div v-if="showHelp" class="help-panel">
            <div v-if="loadingDoc" class="help-loading">加载说明中...</div>
            <div v-else-if="doc" class="help-content">
              <div class="help-purpose">
                <div class="help-section-label">用途</div>
                <p>{{ doc.purpose }}</p>
              </div>

              <div v-if="doc.use_cases && doc.use_cases.length" class="help-section">
                <div class="help-section-label">常见使用场景</div>
                <ul>
                  <li v-for="(uc, i) in doc.use_cases" :key="i">{{ uc }}</li>
                </ul>
              </div>

              <div v-if="doc.key_params && Object.keys(doc.key_params).length" class="help-section">
                <div class="help-section-label">关键参数说明</div>
                <div v-for="(desc, param) in doc.key_params" :key="param" class="help-param">
                  <code class="help-param-name">{{ param }}</code>
                  <span class="help-param-desc">{{ desc }}</span>
                </div>
              </div>

              <div v-if="doc.example" class="help-section">
                <div class="help-section-label">示例命令</div>
                <pre class="help-example">{{ doc.example }}</pre>
              </div>

              <div v-if="doc.output_desc" class="help-section">
                <div class="help-section-label">输出说明</div>
                <p>{{ doc.output_desc }}</p>
              </div>

              <div v-if="doc.notes" class="help-section help-notes">
                <div class="help-section-label">注意事项</div>
                <p>{{ doc.notes }}</p>
              </div>
            </div>
            <div v-else class="help-empty">暂无此插件的详细说明。</div>
          </div>
        </div>

        <div class="modal-footer">
          <button class="btn-cancel" @click="cancel">取消</button>
          <button class="btn-run" @click="confirm" :disabled="missingRequired" :title="missingRequired ? '必填参数未填写' : ''">
            运行 {{ pluginName }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import AppIcon from './AppIcon.vue'
import { useEscClose } from '../composables/useEscClose'
import { computed, ref, watch, reactive } from 'vue'
import { getPluginDocs } from '../api'
import { useAppStore } from '../stores/app'

const props = defineProps({
  show: Boolean,
  pluginName: { type: String, default: '' },
  engineId: { type: String, default: 'vol3' },
  args: { type: Array, default: () => [] },
  // Global args defaults (from ArgsPanel) — used to pre-fill form
  globalArgs: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['confirm', 'cancel'])

useEscClose(() => props.show, () => emit('cancel'))

const missingRequired = computed(() =>
  props.args.some((arg) => {
    if (!arg.required) return false
    const v = form[arg.name]
    return v === undefined || v === null || String(v).trim() === ''
  }),
)
const store = useAppStore()

const form = reactive({})
const error = ref('')
const showHelp = ref(false)
const loadingDoc = ref(false)
const doc = ref(null)

// Track which fields were pre-filled from globalArgs (for badge display)
const preFilled = reactive({})

// Reset form when plugin/show changes; pre-fill from globalArgs
watch(() => [props.pluginName, props.show], () => {
  error.value = ''
  Object.keys(form).forEach(k => delete form[k])
  Object.keys(preFilled).forEach(k => delete preFilled[k])

  if (props.args) {
    const globals = props.globalArgs || store.globalArgs || {}
    props.args.forEach(a => {
      const globalVal = globals[a.name]
      const hasGlobal = globalVal !== undefined && globalVal !== null && String(globalVal).trim() !== ''
      if (a.arg_type === 'bool') {
        form[a.name] = hasGlobal ? String(globalVal).toLowerCase() === 'true' : (a.default ?? false)
        if (hasGlobal) preFilled[a.name] = true
      } else {
        form[a.name] = hasGlobal ? String(globalVal).trim() : (a.default ?? '')
        if (hasGlobal) preFilled[a.name] = true
      }
    })
  }
}, { immediate: true })

function isPreFilled(arg) {
  return !!preFilled[arg.name]
}

async function toggleHelp() {
  showHelp.value = !showHelp.value
  if (showHelp.value && doc.value === null && props.pluginName) {
    loadingDoc.value = true
    try {
      const res = await getPluginDocs(props.pluginName, props.engineId)
      doc.value = res?.doc && Object.keys(res.doc).length ? res.doc : null
    } catch (_e) {
      doc.value = null
    } finally {
      loadingDoc.value = false
    }
  }
}

// Reset doc when plugin changes
watch(() => props.pluginName, () => {
  doc.value = null
  showHelp.value = false
})

function validate() {
  for (const arg of props.args) {
    if (arg.required) {
      const v = form[arg.name]
      if (v === undefined || v === null || String(v).trim() === '') {
        error.value = `"${arg.name}" 为必填参数 (${arg.flag})`
        return false
      }
    }
    if (arg.arg_type === 'int' && form[arg.name] !== '' && form[arg.name] !== undefined) {
      if (isNaN(Number(form[arg.name]))) {
        error.value = `"${arg.name}" 必须为整数`
        return false
      }
    }
  }
  error.value = ''
  return true
}

function confirm() {
  if (!validate()) return
  const params = {}
  for (const arg of props.args) {
    const v = form[arg.name]
    if (arg.arg_type === 'bool') {
      if (props.engineId === 'yarax') params[arg.name] = Boolean(v)
      else if (v) params[arg.name] = true
    } else if (v !== '' && v !== undefined && v !== null) {
      params[arg.name] = v
    }
  }
  emit('confirm', params)
}

function cancel() {
  error.value = ''
  emit('cancel')
}
</script>

<style scoped>
.modal-overlay {
  position: fixed;
  inset: 0;
  background: color-mix(in srgb, var(--bg-primary) 55%, transparent);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-box {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 10px;
  width: 480px;
  max-width: 95vw;
  max-height: 84vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 18px 48px color-mix(in srgb, #000 26%, transparent);
  transition: width 0.2s ease;
}

.modal-box.with-help {
  width: 860px;
  max-width: 96vw;
}

.modal-layout {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.modal-title {
  font-weight: 600;
  font-size: 1rem;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 8px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.plugin-badge {
  background: var(--bg-elevated);
  color: var(--accent-bright);
  font-size: 0.72rem;
  padding: 2px 7px;
  border-radius: 4px;
  font-family: monospace;
}

.help-btn {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  color: var(--text-secondary);
  font-size: 0.75rem;
  padding: 3px 10px;
  border-radius: 5px;
  cursor: pointer;
  transition: all 0.15s;
}
.help-btn:hover { background: var(--bg-hover); color: var(--text-primary); }
.help-btn.active {
  background: var(--accent-glow);
  border-color: var(--accent);
  color: var(--accent-bright);
}

.close-btn {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 1rem;
  padding: 2px 6px;
  border-radius: 4px;
}
.close-btn:hover { color: var(--text-primary); }

.modal-body {
  padding: 16px 18px;
  overflow-y: auto;
  flex: 0 0 auto;
  width: 100%;
  min-width: 0;
}

.with-help .modal-body {
  flex: 0 0 380px;
  border-right: 1px solid var(--border);
  width: 380px;
}

.no-args {
  color: var(--text-secondary);
  font-size: 0.9rem;
  text-align: center;
  padding: 20px 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.no-args-icon {
  color: var(--text-success);
  display: flex;
}

.field-row {
  margin-bottom: 14px;
}

label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.82rem;
  color: var(--text-secondary);
  margin-bottom: 4px;
}

label.required { color: var(--text-primary); }

.arg-name { font-weight: 500; }

.req-badge {
  background: color-mix(in srgb, var(--text-error) 18%, transparent);
  color: var(--text-error);
  font-size: 0.68rem;
  padding: 1px 5px;
  border-radius: 3px;
}

.flag-badge {
  color: var(--accent-bright);
  font-size: 0.75rem;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  padding: 1px 5px;
  border-radius: 3px;
  margin-left: auto;
}

.prefill-badge {
  font-size: 0.65rem;
  color: var(--text-success);
  background: color-mix(in srgb, var(--text-success) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--text-success) 28%, transparent);
  padding: 1px 5px;
  border-radius: 3px;
}

.field-input {
  width: 100%;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 5px;
  color: var(--text-primary);
  padding: 7px 10px;
  font-size: 0.88rem;
  box-sizing: border-box;
  transition: border-color 0.15s;
}
.field-input:focus {
  outline: none;
  border-color: var(--border-focus);
}
.field-input.prefilled {
  border-color: color-mix(in srgb, var(--text-success) 32%, var(--border));
  background: color-mix(in srgb, var(--text-success) 8%, var(--bg-primary));
}

.field-checkbox {
  width: 18px;
  height: 18px;
  cursor: pointer;
  accent-color: var(--accent);
}

.field-help {
  font-size: 0.74rem;
  color: var(--text-muted);
  margin: 3px 0 0;
  line-height: 1.4;
}

.field-error {
  color: var(--text-error);
  font-size: 0.82rem;
  margin-top: 8px;
  background: color-mix(in srgb, var(--text-error) 14%, transparent);
  padding: 7px 10px;
  border-radius: 5px;
}

/* ── Help Panel ──────────────────────── */

.help-panel {
  flex: 1;
  overflow-y: auto;
  padding: 16px 18px;
  min-width: 0;
}

.help-loading {
  color: var(--text-muted);
  font-size: 0.85rem;
  text-align: center;
  padding: 24px 0;
}

.help-empty {
  color: var(--text-muted);
  font-size: 0.85rem;
  text-align: center;
  padding: 24px 0;
}

.help-content {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.help-purpose {
  background: color-mix(in srgb, var(--accent-glow) 65%, var(--bg-elevated));
  border: 1px solid color-mix(in srgb, var(--accent) 24%, var(--border));
  border-radius: 7px;
  padding: 10px 12px;
}

.help-purpose p {
  margin: 0;
  color: var(--text-primary);
  font-size: 0.85rem;
  line-height: 1.55;
}

.help-section {
  border-top: 1px solid var(--border-subtle);
  padding-top: 10px;
}

.help-notes {
  background: color-mix(in srgb, var(--text-warning) 10%, var(--bg-elevated));
  border: 1px solid color-mix(in srgb, var(--text-warning) 30%, var(--border));
  border-radius: 7px;
  padding: 10px 12px;
  margin-top: 4px;
}

.help-section-label {
  font-size: 0.68rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--text-muted);
  margin-bottom: 6px;
}

.help-section ul {
  margin: 0;
  padding: 0 0 0 14px;
  list-style: disc;
}

.help-section ul li {
  color: var(--text-secondary);
  font-size: 0.8rem;
  line-height: 1.6;
  padding-left: 4px;
}

.help-section p {
  margin: 0;
  color: var(--text-secondary);
  font-size: 0.81rem;
  line-height: 1.55;
}

.help-param {
  margin-bottom: 6px;
}

.help-param-name {
  display: block;
  font-size: 0.75rem;
  color: var(--accent-bright);
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  padding: 1px 6px;
  border-radius: 3px;
  margin-bottom: 2px;
}

.help-param-desc {
  font-size: 0.78rem;
  color: var(--text-secondary);
  line-height: 1.45;
}

.help-example {
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 8px 10px;
  font-size: 0.76rem;
  font-family: monospace;
  color: var(--text-success);
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
}

/* ── Footer ──────────────────────────── */

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 12px 18px;
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}

.btn-cancel {
  background: var(--bg-elevated);
  border: none;
  color: var(--text-secondary);
  padding: 7px 16px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.88rem;
}
.btn-cancel:hover { background: var(--bg-hover); color: var(--text-primary); }

.btn-run {
  background: var(--accent);
  border: none;
  color: var(--load-btn-text);
  padding: 7px 18px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.88rem;
  font-weight: 600;
}
.btn-run:hover { background: var(--accent-bright); }
.btn-run:disabled { opacity: 0.5; cursor: not-allowed; }

</style>
