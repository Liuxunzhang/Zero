import { describe, expect, it } from 'vitest'
import { initialRunState, reduceRunEvent } from './aiRuns'

function event(seq, type, data = {}) {
  return {
    version: 1,
    seq,
    run_id: 'run_1',
    conversation_id: 'conv_1',
    type,
    data,
  }
}

describe('AI run event reducer', () => {
  it('merges text exactly once by sequence', () => {
    const state = initialRunState()
    expect(reduceRunEvent(state, event(1, 'text_delta', { text: '证据' }))).toBe(true)
    expect(reduceRunEvent(state, event(1, 'text_delta', { text: '重复' }))).toBe(false)
    expect(state.text).toBe('证据')
    expect(state.lastSeq).toBe(1)
  })

  it('tracks tool progress and result handles', () => {
    const state = initialRunState()
    reduceRunEvent(state, event(1, 'tool_start', {
      tool_call_id: 'call_1',
      tool_name: 'run_plugin',
      arguments: { plugin_name: 'pslist.PsList' },
    }))
    reduceRunEvent(state, event(2, 'tool_progress', {
      tool_call_id: 'call_1',
      stage: 'running',
      percent: 42,
    }))
    reduceRunEvent(state, event(3, 'tool_end', {
      tool_call_id: 'call_1',
      is_error: false,
      details: { result_id: 'res_1', total: 10 },
    }))
    expect(state.tools.call_1.status).toBe('complete')
    expect(state.tools.call_1.progress.percent).toBe(42)
    expect(state.tools.call_1.result.details.result_id).toBe('res_1')
  })

  it('exposes retry, compaction, usage and budgets', () => {
    const state = initialRunState()
    reduceRunEvent(state, event(1, 'retry', { attempt: 1, delay_seconds: 2 }))
    reduceRunEvent(state, event(2, 'compaction', { utilization: 0.5, compacted: true }))
    reduceRunEvent(state, event(3, 'usage', { input_tokens: 100, output_tokens: 20 }))
    reduceRunEvent(state, event(4, 'turn_end', { budget: { turns_used: 1 } }))
    expect(state.retry.attempt).toBe(1)
    expect(state.compaction.compacted).toBe(true)
    expect(state.context.usage.output_tokens).toBe(20)
    expect(state.budget.turns_used).toBe(1)
  })

  it('marks cancelled partial responses terminal', () => {
    const state = initialRunState()
    reduceRunEvent(state, event(1, 'run_start'))
    reduceRunEvent(state, event(2, 'text_delta', { text: 'partial' }))
    reduceRunEvent(state, event(3, 'run_end', { status: 'cancelled' }))
    expect(state.status).toBe('cancelled')
    expect(state.text).toBe('partial')
  })
})
