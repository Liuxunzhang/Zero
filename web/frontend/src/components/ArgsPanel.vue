<template>
  <Teleport to="body">
    <div v-if="show" class="args-overlay" @click.self="$emit('close')">
      <div class="args-panel">
        <!-- Header -->
        <div class="args-header">
          <div class="args-title">
            <span class="args-icon">CFG</span>
            <span>取证参数默认值</span>
            <span class="engine-badge">{{ engineId }}</span>
          </div>
          <button class="close-btn" @click="$emit('close')">关闭</button>
        </div>

        <!-- Body -->
        <div class="args-body">
          <!-- dump_dir special section -->
          <div class="section">
            <div class="section-title">
              <span class="section-icon">OUT</span>
              导出目录
              <span class="section-tip">适用于 dlldump / procdump / memdump 等导出类插件</span>
            </div>
            <div class="field-row">
              <label>dump_dir
                <span class="param-flag">--dump-dir</span>
              </label>
              <input
                type="text"
                v-model="localArgs.dump_dir"
                placeholder="e.g. saved_results/vol3/dumps"
                class="field-input"
              />
              <p class="field-hint">
                相对路径基于项目根目录解析；目录不存在时会自动创建。
              </p>
            </div>
          </div>

          <!-- Process filtering -->
          <div class="section">
            <div class="section-title">
              <span class="section-icon">PROC</span>
              进程过滤
              <span class="section-tip">预填 pid / offset，用于 handles、malfind、dlldump 等插件</span>
            </div>
            <div class="two-col">
              <div class="field-row">
                <label>pid
                  <span class="param-flag">--pid</span>
                </label>
                <input
                  type="text"
                  v-model="localArgs.pid"
                  placeholder="进程 PID（整数）"
                  class="field-input"
                />
              </div>
              <div class="field-row">
                <label>offset
                  <span class="param-flag">--offset</span>
                </label>
                <input
                  type="text"
                  v-model="localArgs.offset"
                  placeholder="EPROCESS 偏移（十六进制）"
                  class="field-input"
                />
              </div>
              <div class="field-row">
                <label>base
                  <span class="param-flag">--base</span>
                </label>
                <input
                  type="text"
                  v-model="localArgs.base"
                  placeholder="基地址（十六进制）"
                  class="field-input"
                />
              </div>
              <div class="field-row">
                <label>name
                  <span class="param-flag">--name</span>
                </label>
                <input
                  type="text"
                  v-model="localArgs.name"
                  placeholder="对象名称"
                  class="field-input"
                />
              </div>
            </div>
          </div>

          <!-- Registry & Search -->
          <div class="section">
            <div class="section-title">
              <span class="section-icon">REG</span>
              注册表 / 搜索
              <span class="section-tip">预填 key / regex，用于 printkey、dumpfiles、dlldump 等插件</span>
            </div>
            <div class="field-row">
              <label>key
                <span class="param-flag">--key</span>
              </label>
              <input
                type="text"
                v-model="localArgs.key"
                placeholder="SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
                class="field-input"
              />
              <p class="field-hint">注册表键路径，不需要 HKEY_LOCAL_MACHINE 前缀。</p>
            </div>
            <div class="field-row">
              <label>regex
                <span class="param-flag">--regex</span>
              </label>
              <input
                type="text"
                v-model="localArgs.regex"
                placeholder="正则表达式，如 \.pdf$"
                class="field-input"
              />
            </div>
          </div>

          <!-- Usage notes -->
          <div class="usage-note">
            <span class="usage-icon">NOTE</span>
            这些默认值用于重复验证同一镜像中的进程、偏移、导出目录和注册表路径。
            运行插件前仍可覆盖；当前插件不支持的字段会被忽略。
          </div>
        </div>

        <!-- Footer -->
        <div class="args-footer">
          <button class="btn-reset" @click="resetArgs">重置</button>
          <div class="footer-right">
            <button class="btn-cancel" @click="$emit('close')">取消</button>
            <button class="btn-save" @click="saveArgs">保存默认值</button>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { reactive, watch } from 'vue'

const props = defineProps({
  show: Boolean,
  engineId: { type: String, default: 'vol3' },
  modelValue: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['close', 'update:modelValue'])

const EMPTY = () => ({
  dump_dir: '',
  pid: '',
  offset: '',
  base: '',
  name: '',
  key: '',
  regex: '',
})

const localArgs = reactive(EMPTY())

// Sync when panel opens
watch(() => props.show, (val) => {
  if (val) Object.assign(localArgs, EMPTY(), props.modelValue || {})
})

function saveArgs() {
  const clean = {}
  for (const [k, v] of Object.entries(localArgs)) {
    if (String(v).trim()) clean[k] = String(v).trim()
  }
  emit('update:modelValue', clean)
  emit('close')
}

function resetArgs() {
  Object.assign(localArgs, EMPTY())
}
</script>

<style scoped>
.args-overlay {
  position: fixed;
  inset: 0;
  background: color-mix(in srgb, var(--bg-primary) 55%, transparent);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.args-panel {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 12px;
  width: 560px;
  max-width: 96vw;
  max-height: 85vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 16px 48px color-mix(in srgb, #000 18%, transparent);
}

.args-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border-subtle);
  flex-shrink: 0;
}

.args-title {
  font-weight: 600;
  font-size: 0.95rem;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 8px;
}

.args-icon { font-size: 1rem; }

.engine-badge {
  background: var(--accent-glow);
  color: var(--accent-bright);
  font-size: 0.7rem;
  padding: 2px 8px;
  border-radius: 4px;
  font-family: monospace;
  font-weight: 500;
}

.close-btn {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 1rem;
  padding: 2px 6px;
  border-radius: 4px;
  line-height: 1;
}
.close-btn:hover { color: var(--text-primary); }

.args-body {
  padding: 16px 18px;
  overflow-y: auto;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.section {
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  padding: 12px 14px;
  background: var(--bg-tertiary);
}

.section-title {
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.4px;
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.section-icon { font-size: 0.9rem; }

.section-tip {
  font-size: 0.7rem;
  color: var(--text-muted);
  font-weight: 400;
  text-transform: none;
  letter-spacing: 0;
}

.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px 14px;
}

.field-row {
  margin-bottom: 4px;
}

label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.78rem;
  color: var(--text-secondary);
  margin-bottom: 4px;
  font-weight: 500;
}

.param-flag {
  font-family: monospace;
  font-size: 0.7rem;
  color: var(--accent-bright);
  background: var(--bg-elevated);
  border: 1px solid var(--border-subtle);
  padding: 1px 5px;
  border-radius: 3px;
  margin-left: auto;
}

.field-input {
  width: 100%;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: 5px;
  color: var(--text-primary);
  padding: 6px 10px;
  font-size: 0.82rem;
  font-family: var(--font-mono, monospace);
  box-sizing: border-box;
  transition: border-color 0.15s;
}
.field-input:focus {
  outline: none;
  border-color: var(--accent);
}
.field-input::placeholder {
  color: var(--text-muted);
  font-family: var(--font-sans);
  font-size: 0.78rem;
}

.field-hint {
  font-size: 0.7rem;
  color: var(--text-muted);
  margin: 3px 0 0;
}

.usage-note {
  font-size: 0.75rem;
  color: var(--text-muted);
  background: var(--bg-tertiary);
  border: 1px solid var(--border-subtle);
  border-radius: 6px;
  padding: 8px 12px;
  display: flex;
  gap: 6px;
  align-items: flex-start;
  line-height: 1.5;
}

.usage-icon { flex-shrink: 0; font-size: 0.85rem; }

.args-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 18px;
  border-top: 1px solid var(--border-subtle);
  flex-shrink: 0;
}

.footer-right {
  display: flex;
  gap: 8px;
}

.btn-reset {
  background: none;
  border: 1px solid var(--border);
  color: var(--text-muted);
  padding: 6px 14px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.82rem;
}
.btn-reset:hover {
  color: var(--text-secondary);
  border-color: var(--text-secondary);
}

.btn-cancel {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  color: var(--text-secondary);
  padding: 6px 14px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.82rem;
}
.btn-cancel:hover { background: var(--bg-hover); }

.btn-save {
  background: var(--accent);
  border: none;
  color: var(--load-btn-text);
  padding: 6px 16px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.82rem;
  font-weight: 600;
}
.btn-save:hover { background: var(--accent-bright); }
</style>
