/**
 * Escape-closes the topmost open layer (modal / panel / popover).
 *
 * One module-level stack + one document listener: each open layer registers,
 * Esc closes only the most recently opened one. Inputs that handle Esc
 * themselves (filter clear, search clear, rename cancel) win by calling
 * preventDefault — we skip defaultPrevented events.
 *
 *   useEscClose(() => props.show, () => emit('close'))     // prop-driven
 *   useEscClose(() => true, () => emit('close'))            // v-if-mounted
 */
import { watch, onBeforeUnmount } from 'vue'

const stack = []
let installed = false

function onKey(e) {
  if (e.key !== 'Escape' || e.defaultPrevented || !stack.length) return
  e.preventDefault()
  stack[stack.length - 1].close()
}

export function useEscClose(isOpen, close) {
  const entry = { close }
  const sync = (open) => {
    const i = stack.indexOf(entry)
    if (open && i < 0) stack.push(entry)
    else if (!open && i >= 0) stack.splice(i, 1)
  }
  if (!installed) {
    document.addEventListener('keydown', onKey)
    installed = true
  }
  watch(isOpen, sync, { immediate: true })
  onBeforeUnmount(() => sync(false))
}
