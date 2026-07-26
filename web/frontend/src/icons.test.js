import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { icons } from './icons'

const here = dirname(fileURLToPath(import.meta.url))

function walk(dir) {
  const out = []
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) out.push(...walk(full))
    else if (entry.name.endsWith('.vue')) out.push(full)
  }
  return out
}

// Every AppIcon usage in the app must reference a registered icon, otherwise it
// renders as an invisible empty <svg> with no error at runtime.
describe('icon registry completeness', () => {
  const files = walk(here)
  const used = new Set()
  // Static: name="foo"
  const staticRe = /<AppIcon\b[^>]*\sname="([a-z-]+)"/g
  // Dynamic ternaries: :name="cond ? 'a' : 'b'" and chained
  const dynRe = /<AppIcon\b[^>]*\s:name="([^"]+)"/g
  const literalRe = /'([a-z-]+)'/g

  for (const f of files) {
    const src = readFileSync(f, 'utf8')
    let m
    while ((m = staticRe.exec(src))) used.add(m[1])
    while ((m = dynRe.exec(src))) {
      let lit
      while ((lit = literalRe.exec(m[1]))) used.add(lit[1])
    }
  }

  it('found icon usages to check', () => {
    expect(used.size).toBeGreaterThan(5)
  })

  it('every referenced icon exists in the registry', () => {
    const missing = [...used].filter((name) => !(name in icons))
    expect(missing, `missing icons: ${missing.join(', ')}`).toEqual([])
  })
})
