import { describe, expect, it } from 'vitest'
import { calculateColumnWidths } from './tableColumns'

describe('calculateColumnWidths', () => {
  it('uses every result row so virtual scrolling cannot change the widths', () => {
    const columns = ['PID', 'Name']
    const rows = [
      [1, 'init'],
      [2, 'a-process-name-that-only-appears-below-the-viewport'],
    ]

    const widths = calculateColumnWidths(columns, rows)

    expect(widths[0]).toBe(72)
    expect(widths[1]).toBeGreaterThan(300)
    expect(calculateColumnWidths(columns, rows)).toEqual(widths)
  })

  it('keeps narrow and very long values inside the configured bounds', () => {
    const widths = calculateColumnWidths(
      ['A', 'Long'],
      [[1, 'x'.repeat(1000)]],
      { minWidth: 80, maxWidth: 240 },
    )

    expect(widths).toEqual([80, 240])
  })

  it('allows extra width for CJK characters', () => {
    const latin = calculateColumnWidths(['Value'], [['abcd']])[0]
    const cjk = calculateColumnWidths(['Value'], [['取证分析']])[0]

    expect(cjk).toBeGreaterThan(latin)
  })
})
