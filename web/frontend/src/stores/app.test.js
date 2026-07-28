import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// The store imports many api functions at module load; stub them all.
vi.mock('../api', () => ({
  loadImage: vi.fn(),
  autoDownloadImageSymbols: vi.fn(),
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

import {
  loadImage as apiLoadImage,
  autoDownloadImageSymbols,
  getImageStatus,
  getEngineSettings,
} from '../api'
import { useAppStore } from './app'
import { confirmState, resolveConfirm } from '../composables/confirm'

describe('app store pagination & filter logic', () => {
  beforeEach(() => {
    vi.clearAllMocks()
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

  it('clearMessages removes the current log history', () => {
    const s = useAppStore()
    s.pushMessage('one')
    s.pushMessage('two', 'warning')
    expect(s.messages.length).toBe(2)
    s.clearMessages()
    expect(s.messages).toEqual([])
  })

  it('logs image loading before checking a local symbol', async () => {
    apiLoadImage.mockResolvedValue({ ok: true })
    getImageStatus.mockResolvedValue({ path: '/tmp/memory.raw' })
    getEngineSettings.mockResolvedValue({ settings: {} })
    autoDownloadImageSymbols.mockImplementation(async (_path, onProgress) => {
      const loadLog = useAppStore().messages.find((message) => message.text.includes('镜像已加载'))
      expect(loadLog).toBeTruthy()
      onProgress({ stage: 'detecting', percent: null })
      return {
        enabled: true,
        status: 'present',
        kernel: { release: '6.12.90+deb13.1-amd64' },
        local_matches: [{ path: 'debian-kernel.json.xz' }],
      }
    })

    const s = useAppStore()
    await s.loadImage('/tmp/memory.raw')

    expect(s.messages.map((message) => message.text)).toEqual([
      '[vol3] 镜像已加载: /tmp/memory.raw',
      '[vol3] 检测到 Linux 内核 6.12.90+deb13.1-amd64，匹配符号表已存在',
    ])
    expect(new Set(s.messages.map((message) => message.ts)).size).toBe(2)
    expect(s.symbolDownloadBusy).toBe(false)
    expect(s.symbolDownloadProgress).toBe(null)
  })

  it('waits for confirmation and forwards the gh-proxy choice', async () => {
    apiLoadImage.mockResolvedValue({ ok: true })
    getImageStatus.mockResolvedValue({ path: '/tmp/debian.raw' })
    getEngineSettings.mockResolvedValue({ settings: {} })
    autoDownloadImageSymbols
      .mockResolvedValueOnce({
        enabled: true,
        status: 'available',
        kernel: { release: '6.12.96+deb13-amd64', distro: 'debian' },
        repo: 'owner/repo',
        candidates: ['Debian/kernel.json.xz'],
      })
      .mockImplementationOnce(async (_path, onProgress) => {
        onProgress({ stage: 'downloading', percent: 100, total_files: 1 })
        return {
          enabled: true,
          status: 'downloaded',
          downloaded: [{ path: 'Debian/kernel.json.xz' }],
          skipped: [],
          failed: [],
          use_gh_proxy: true,
        }
      })

    const s = useAppStore()
    const loading = s.loadImage('/tmp/debian.raw')
    await vi.waitFor(() => expect(confirmState.show).toBe(true))
    expect(confirmState.alternateText).toBe('gh-proxy 下载')
    resolveConfirm('gh-proxy')
    await loading

    expect(autoDownloadImageSymbols).toHaveBeenNthCalledWith(
      2,
      '/tmp/debian.raw',
      expect.any(Function),
      {
        download: true,
        useGhProxy: true,
        paths: ['Debian/kernel.json.xz'],
        repo: 'owner/repo',
      },
    )
    expect(s.messages.some((message) => message.text.includes('已下载 1 个匹配符号表'))).toBe(true)
  })
})
