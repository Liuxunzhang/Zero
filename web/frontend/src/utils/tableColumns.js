const DEFAULT_MIN_WIDTH = 72
const DEFAULT_MAX_WIDTH = 360
const CELL_HORIZONTAL_PADDING = 20
const SORT_INDICATOR_WIDTH = 18
const CHARACTER_WIDTH = 7.25
const RESIZE_MIN_WIDTH = 48
const RESIZE_MAX_WIDTH = 1200

function isWideCharacter(codePoint) {
  return (
    codePoint >= 0x1100 && (
      codePoint <= 0x115f
      || codePoint === 0x2329
      || codePoint === 0x232a
      || (codePoint >= 0x2e80 && codePoint <= 0xa4cf && codePoint !== 0x303f)
      || (codePoint >= 0xac00 && codePoint <= 0xd7a3)
      || (codePoint >= 0xf900 && codePoint <= 0xfaff)
      || (codePoint >= 0xfe10 && codePoint <= 0xfe19)
      || (codePoint >= 0xfe30 && codePoint <= 0xfe6f)
      || (codePoint >= 0xff00 && codePoint <= 0xff60)
      || (codePoint >= 0xffe0 && codePoint <= 0xffe6)
      || (codePoint >= 0x1f300 && codePoint <= 0x1faff)
      || (codePoint >= 0x20000 && codePoint <= 0x3fffd)
    )
  )
}

function visualTextUnits(value, limit) {
  let units = 0
  for (const character of String(value ?? '')) {
    units += isWideCharacter(character.codePointAt(0)) ? 2 : 1
    if (units >= limit) return limit
  }
  return units
}

/**
 * Calculates widths from the complete result page, rather than from the
 * virtual rows currently mounted in the DOM. This keeps the browser from
 * resizing columns as different rows enter the viewport during scrolling.
 */
export function calculateColumnWidths(
  columns = [],
  rows = [],
  { minWidth = DEFAULT_MIN_WIDTH, maxWidth = DEFAULT_MAX_WIDTH } = {},
) {
  const maxUnits = Math.ceil((maxWidth - CELL_HORIZONTAL_PADDING) / CHARACTER_WIDTH)

  return columns.map((column, columnIndex) => {
    let width = (
      visualTextUnits(column, maxUnits) * CHARACTER_WIDTH
      + CELL_HORIZONTAL_PADDING
      + SORT_INDICATOR_WIDTH
    )

    for (const row of rows) {
      const cellWidth = (
        visualTextUnits(row?.[columnIndex], maxUnits) * CHARACTER_WIDTH
        + CELL_HORIZONTAL_PADDING
      )
      width = Math.max(width, cellWidth)
      if (width >= maxWidth) break
    }

    return Math.round(Math.min(maxWidth, Math.max(minWidth, width)))
  })
}

export function clampColumnWidth(
  width,
  minWidth = RESIZE_MIN_WIDTH,
  maxWidth = RESIZE_MAX_WIDTH,
) {
  const numericWidth = Number(width)
  if (!Number.isFinite(numericWidth)) return minWidth
  return Math.round(Math.min(maxWidth, Math.max(minWidth, numericWidth)))
}
