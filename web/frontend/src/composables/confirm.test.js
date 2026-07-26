import { describe, it, expect } from 'vitest'
import { confirmState, confirmAction, resolveConfirm } from './confirm'

describe('confirmAction', () => {
  it('opens with merged options and resolves true on confirm', async () => {
    const p = confirmAction({ title: '删除', message: 'x', confirmText: '删' })
    expect(confirmState.show).toBe(true)
    expect(confirmState.title).toBe('删除')
    expect(confirmState.confirmText).toBe('删')

    resolveConfirm(true)
    expect(confirmState.show).toBe(false)
    await expect(p).resolves.toBe(true)
  })

  it('resolves false on cancel', async () => {
    const p = confirmAction({ title: 'y' })
    resolveConfirm(false)
    await expect(p).resolves.toBe(false)
  })

  it('resets defaults between calls', async () => {
    const p1 = confirmAction({ title: 'a', danger: false })
    expect(confirmState.danger).toBe(false)
    resolveConfirm(true)
    await p1

    const p2 = confirmAction({ title: 'b' }) // danger omitted -> back to default
    expect(confirmState.danger).toBe(true)
    resolveConfirm(true)
    await p2
  })

  it('opening a second dialog cancels the first', async () => {
    const first = confirmAction({ title: 'first' })
    const second = confirmAction({ title: 'second' })

    await expect(first).resolves.toBe(false)
    expect(confirmState.title).toBe('second')

    resolveConfirm(true)
    await expect(second).resolves.toBe(true)
  })
})
