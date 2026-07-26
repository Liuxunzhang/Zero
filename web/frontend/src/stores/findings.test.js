import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useFindingsStore, findingKey } from './findings'

const ROW = ['1234', 'sshd', '/usr/sbin/sshd']

describe('findings store', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('adds a finding and reports membership', () => {
    const s = useFindingsStore()
    s.setImage('/img/a.raw')
    expect(s.add({ plugin: 'p', columns: ['PID', 'C', 'A'], row: ROW })).toBe(true)
    expect(s.items.length).toBe(1)
    expect(s.has('p', ROW)).toBe(true)
  })

  it('deduplicates the same row', () => {
    const s = useFindingsStore()
    s.setImage('/img/a.raw')
    s.add({ plugin: 'p', columns: [], row: ROW })
    expect(s.add({ plugin: 'p', columns: [], row: ROW })).toBe(false)
    expect(s.items.length).toBe(1)
  })

  it('removes and updates notes', () => {
    const s = useFindingsStore()
    s.setImage('/img/a.raw')
    s.add({ plugin: 'p', columns: [], row: ROW })
    const id = s.items[0].id
    s.updateNote(id, 'suspicious')
    expect(s.items[0].note).toBe('suspicious')
    s.remove(id)
    expect(s.items.length).toBe(0)
  })

  it('isolates findings per image', () => {
    const s = useFindingsStore()
    s.setImage('/img/a.raw')
    s.add({ plugin: 'p', columns: [], row: ROW })
    s.setImage('/img/b.raw')
    expect(s.items.length).toBe(0) // b has none
    s.add({ plugin: 'p', columns: [], row: ['9', 'x', 'y'] })
    s.setImage('/img/a.raw')
    expect(s.items.length).toBe(1) // a's finding restored from storage
    expect(s.items[0].row).toEqual(ROW)
  })

  it('persists across store instances', () => {
    const s1 = useFindingsStore()
    s1.setImage('/img/a.raw')
    s1.add({ plugin: 'p', columns: [], row: ROW })

    setActivePinia(createPinia())
    const s2 = useFindingsStore()
    s2.setImage('/img/a.raw')
    expect(s2.items.length).toBe(1)
  })

  it('findingKey is stable and row-sensitive', () => {
    expect(findingKey('p', ['a', 'b'])).toBe(findingKey('p', ['a', 'b']))
    expect(findingKey('p', ['a', 'b'])).not.toBe(findingKey('p', ['a', 'c']))
  })
})
