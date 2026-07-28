<template>
  <Teleport to="body">
    <div
      v-if="confirmState.show"
      class="confirm-overlay"
      @click.self="resolveConfirm(false)"
    >
      <div class="confirm-box" role="alertdialog" aria-modal="true">
        <div class="confirm-head">
          <span class="confirm-icon" :class="{ danger: confirmState.danger }">
            <AppIcon name="alert-triangle" :size="18" />
          </span>
          <span class="confirm-title">{{ confirmState.title }}</span>
        </div>
        <p v-if="confirmState.message" class="confirm-message">{{ confirmState.message }}</p>
        <div class="confirm-actions">
          <button class="confirm-cancel-btn" @click="resolveConfirm(false)">
            {{ confirmState.cancelText }}
          </button>
          <button
            v-if="confirmState.alternateText"
            class="confirm-alternate-btn"
            @click="resolveConfirm(confirmState.alternateValue)"
          >
            {{ confirmState.alternateText }}
          </button>
          <button
            ref="confirmBtnRef"
            class="confirm-ok-btn"
            :class="{ danger: confirmState.danger }"
            @click="resolveConfirm(true)"
          >
            {{ confirmState.confirmText }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import AppIcon from './AppIcon.vue'
import { confirmState, resolveConfirm } from '../composables/confirm'
import { useEscClose } from '../composables/useEscClose'

const confirmBtnRef = ref(null)

// The dialog always opens last, so it sits on top of the Esc stack.
useEscClose(() => confirmState.show, () => resolveConfirm(false))

watch(() => confirmState.show, (show) => {
  if (show) nextTick(() => confirmBtnRef.value?.focus())
})
</script>

<style scoped>
.confirm-overlay {
  position: fixed;
  inset: 0;
  z-index: 10000; /* above every modal (.modal-overlay is 9000) */
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--overlay-backdrop);
  backdrop-filter: blur(2px);
}

.confirm-box {
  width: min(380px, calc(100vw - 48px));
  padding: 18px 20px 16px;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-xl);
}

.confirm-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.confirm-icon {
  display: flex;
  color: var(--text-warning);
}

.confirm-icon.danger {
  color: var(--text-error);
}

.confirm-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.confirm-message {
  margin: 10px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-secondary);
}

.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}

.confirm-cancel-btn,
.confirm-alternate-btn,
.confirm-ok-btn {
  height: 30px;
  padding: 0 14px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.confirm-cancel-btn {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  color: var(--text-secondary);
}

.confirm-alternate-btn {
  background: transparent;
  border: 1px solid var(--accent);
  color: var(--accent);
}

.confirm-cancel-btn:hover,
.confirm-alternate-btn:hover {
  color: var(--text-primary);
  border-color: var(--border-focus);
}

.confirm-ok-btn {
  background: var(--accent-dim);
  border: 1px solid transparent;
  color: var(--load-btn-text);
}

.confirm-ok-btn.danger {
  background: color-mix(in srgb, var(--text-error) 85%, black 15%);
  color: #fff;
}

.confirm-ok-btn:hover {
  filter: brightness(1.15);
}
</style>
