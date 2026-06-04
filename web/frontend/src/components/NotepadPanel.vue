<template>
  <Teleport to="body">
    <div v-if="show" class="notepad-overlay" @click.self="$emit('close')">
      <div class="notepad-panel">
        <div class="notepad-header">
          <div class="notepad-title">
            <span class="notepad-icon">记</span>
            <span>取证记事本</span>
          </div>
          <button class="notepad-close-btn" @click="$emit('close')">关闭</button>
        </div>

        <div class="notepad-body">
          <textarea
            ref="textareaRef"
            v-model="noteText"
            class="notepad-textarea"
            placeholder="记录镜像背景、可疑进程、偏移、命令、结论和下一步验证..."
            spellcheck="false"
            @input="saveNote"
          ></textarea>
        </div>

        <div class="notepad-footer">
          <span class="notepad-status">{{ statusText }}</span>
          <button class="notepad-clear-btn" :disabled="!noteText" @click="clearNote">清空</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'

const props = defineProps({
  show: Boolean,
  storageKey: { type: String, default: 'zero-notepad' },
})

defineEmits(['close'])

const noteText = ref('')
const textareaRef = ref(null)
const savedAt = ref('')

const statusText = computed(() => {
  const count = noteText.value.length
  return savedAt.value ? `${count} 字 · 已保存 ${savedAt.value}` : `${count} 字`
})

watch(() => props.show, async (visible) => {
  if (!visible) return
  loadNote()
  await nextTick()
  textareaRef.value?.focus()
})

function loadNote() {
  try {
    noteText.value = localStorage.getItem(props.storageKey) || ''
  } catch {
    noteText.value = ''
  }
}

function saveNote() {
  try {
    localStorage.setItem(props.storageKey, noteText.value)
    savedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  } catch {
    savedAt.value = '失败'
  }
}

function clearNote() {
  noteText.value = ''
  saveNote()
}
</script>

<style scoped>
.notepad-overlay {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: color-mix(in srgb, var(--bg-primary) 52%, transparent);
  z-index: 1000;
}

.notepad-panel {
  width: min(720px, 96vw);
  height: min(620px, 86vh);
  display: flex;
  flex-direction: column;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 18px 54px color-mix(in srgb, #000 22%, transparent);
  overflow: hidden;
}

.notepad-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 48px;
  padding: 0 16px;
  border-bottom: 1px solid var(--border-subtle);
  flex-shrink: 0;
}

.notepad-title {
  display: flex;
  align-items: center;
  gap: 9px;
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 700;
}

.notepad-icon {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
  color: var(--accent-bright);
  font-size: 13px;
}

.notepad-close-btn,
.notepad-clear-btn {
  height: 28px;
  padding: 0 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
}

.notepad-close-btn:hover,
.notepad-clear-btn:hover:not(:disabled) {
  border-color: var(--accent-dim);
  color: var(--text-primary);
  background: var(--bg-hover);
}

.notepad-clear-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.notepad-body {
  flex: 1;
  min-height: 0;
  padding: 14px;
}

.notepad-textarea {
  width: 100%;
  height: 100%;
  resize: none;
  padding: 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  outline: none;
  background: var(--bg-primary);
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.65;
}

.notepad-textarea:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-glow);
}

.notepad-textarea::placeholder {
  color: var(--text-muted);
}

.notepad-footer {
  min-height: 42px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 14px;
  border-top: 1px solid var(--border-subtle);
  flex-shrink: 0;
}

.notepad-status {
  color: var(--text-muted);
  font-size: 12px;
  font-family: var(--font-mono);
}
</style>
