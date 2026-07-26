import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// The store imports many api functions at module load; stub them all.
vi.mock('../api', () => ({
  loadImage: vi.fn(),
  getImageStatus: vi.fn(),
  getPlugins: vi.fn(),
  getPluginArgs: vi.fn(),
  reloadPlugins: vi.fn(),
  getResults: vi.fn(),
  exportResults: vi.fn(),
  clearCache: vi.fn(),
  cancelPlugin: vi.fn(),
  createPluginSocket: vi.fn(() => ({
    send: vi.fn(() => true),
    close: vi.fn(),
    ready: true,
    state: 'open',
    onOpen: vi.fn(),
    onOpenOnce: vi.fn(),
  })),
  listEngines: vi.fn(),
  getEngineSettings: vi.fn(),
}))

import { useAppStore } from './app'

describe('app store pagination & filter logic', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  function seededStore() {
    const s = useAppStore()
    const st = s.engineStates.vol3
    st.imageLoaded = true
    st.currentPlugin = 'linux.pslist.PsList'
    st.totalPages = 5
    st.page = 3
    return { s, st }
  }

  it('goToPage clamps to [1, totalPages]', () => {
    const { s, st } = seededStore()
    s.goToPage(99)
    expect(st.page).toBe(5)
    s.goToPage(-4)
    expect(st.page).toBe(1)
  })

  it('goToPage ignores non-finite input', () => {
    const { s, st } = seededStore()
    s.goToPage('abc')
    expect(st.page).toBe(1) // NaN -> clamped to 1
  })

  it('setFilter resets page to 1 and stores text', () => {
    const { s, st } = seededStore()
    s.setFilter('sshd')
    expect(st.filterText).toBe('sshd')
    expect(st.page).toBe(1)
  })

  it('setFilter is a no-op when text is unchanged', () => {
    const { s, st } = seededStore()
    s.setFilter('x')
    st.page = 4
    s.setFilter('x') // same value should not reset page
    expect(st.page).toBe(4)
  })

  it('setPageSize accepts only whitelisted sizes', () => {
    const { s, st } = seededStore()
    s.setPageSize(500)
    expect(st.pageSize).toBe(500)
    expect(st.page).toBe(1)
    st.page = 2
    s.setPageSize(333) // not in PAGE_SIZE_OPTIONS
    expect(st.pageSize).toBe(500) // unchanged
    expect(st.page).toBe(2)
  })

  it('toggleSort cycles asc -> desc and resets page', () => {
    const { s, st } = seededStore()
    s.toggleSort('PID')
    expect(st.sortColumn).toBe('PID')
    expect(st.sortDesc).toBe(false)
    expect(st.page).toBe(1)
    s.toggleSort('PID')
    expect(st.sortDesc).toBe(true)
  })

  it('pushMessage caps history at 50', () => {
    const s = useAppStore()
    for (let i = 0; i < 60; i++) s.pushMessage('m' + i)
    expect(s.messages.length).toBe(50)
    expect(s.messages[s.messages.length - 1].text).toBe('m59')
  })
})
