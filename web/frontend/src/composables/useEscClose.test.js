import { describe, it, expect, vi, beforeEach } from 'vitest'
import { defineComponent, ref, h } from 'vue'
import { mount } from '@vue/test-utils'
import { useEscClose } from './useEscClose'

// A component that registers useEscClose with a controllable open ref.
function makeLayer(closeSpy) {
  const open = ref(true)
  const comp = defineComponent({
    setup() {
      useEscClose(() => open.value, closeSpy)
      return () => h('div')
    },
  })
  return { comp, open }
}

function pressEsc() {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', cancelable: true }))
}

describe('useEscClose', () => {
  beforeEach(() => {
    // Clear any residual state by pressing Esc until nothing responds.
    for (let i = 0; i < 10; i++) pressEsc()
  })

  it('closes only the topmost (last-opened) layer', () => {
    const closeA = vi.fn()
    const closeB = vi.fn()
    const a = makeLayer(closeA)
    mount(a.comp)
    const b = makeLayer(closeB)
    mount(b.comp)

    pressEsc()
    expect(closeB).toHaveBeenCalledTimes(1)
    expect(closeA).not.toHaveBeenCalled()
  })

  it('falls through to the next layer after the top one closes', async () => {
    const closeA = vi.fn()
    const closeB = vi.fn()
    const a = makeLayer(closeA)
    mount(a.comp)
    const b = makeLayer(closeB)
    mount(b.comp)

    pressEsc()
    b.open.value = false // top layer closed; it deregisters
    await Promise.resolve()

    pressEsc()
    expect(closeA).toHaveBeenCalledTimes(1)
  })

  it('skips events already handled (defaultPrevented)', () => {
    const close = vi.fn()
    const layer = makeLayer(close)
    mount(layer.comp)

    const e = new KeyboardEvent('keydown', { key: 'Escape', cancelable: true })
    e.preventDefault()
    document.dispatchEvent(e)
    expect(close).not.toHaveBeenCalled()
  })

  it('deregisters on unmount', () => {
    const close = vi.fn()
    const layer = makeLayer(close)
    const wrapper = mount(layer.comp)
    wrapper.unmount()

    pressEsc()
    expect(close).not.toHaveBeenCalled()
  })
})
