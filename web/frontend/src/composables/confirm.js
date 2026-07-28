/**
 * Promise-based confirmation dialog, one instance app-wide.
 *
 * Usage from any component:
 *   import { confirmAction } from '../composables/confirm'
 *   if (!(await confirmAction({ title: '清空对话', message: '……无法恢复。' }))) return
 *
 * ConfirmDialog.vue (mounted once in App.vue) renders `confirmState` and calls
 * `resolveConfirm(true|false)`.
 */
import { reactive } from 'vue'

const defaults = {
  title: '确认操作',
  message: '',
  confirmText: '确认',
  cancelText: '取消',
  alternateText: '',
  alternateValue: 'alternate',
  danger: true,
}

export const confirmState = reactive({
  ...defaults,
  show: false,
  _resolve: null,
})

export function confirmAction(opts = {}) {
  // A second request while one is open cancels the first (last-write-wins is
  // fine here: openings are always user-initiated, never concurrent).
  if (confirmState._resolve) confirmState._resolve(false)
  Object.assign(confirmState, defaults, opts, { show: true })
  return new Promise((resolve) => {
    confirmState._resolve = resolve
  })
}

export function resolveConfirm(value) {
  confirmState.show = false
  const resolve = confirmState._resolve
  confirmState._resolve = null
  if (resolve) resolve(value)
}
