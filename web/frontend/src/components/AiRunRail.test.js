import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import AiRunRail from './AiRunRail.vue'

const run = {
  runId: 'run_1',
  status: 'completed',
  stopReason: 'stop',
  tools: {
    one: { status: 'complete' },
  },
  budget: {
    turns_used: 2,
    max_turns: 12,
  },
  context: {
    estimated_tokens: 25_000,
    context_window: 100_000,
  },
}

describe('AI evidence run rail', () => {
  it('renders as a single compact line and can expand', async () => {
    const wrapper = mount(AiRunRail, {
      props: { run, collapsed: true },
      global: { stubs: { AppIcon: true } },
    })

    expect(wrapper.classes()).toContain('collapsed')
    expect(wrapper.find('.run-rail-compact').exists()).toBe(true)
    expect(wrapper.find('.run-rail-stages').exists()).toBe(false)

    await wrapper.find('.run-rail-compact').trigger('click')
    expect(wrapper.emitted('update:collapsed')).toEqual([[false]])
  })

  it('exposes the full stage rail and can collapse it', async () => {
    const wrapper = mount(AiRunRail, {
      props: { run, collapsed: false },
      global: { stubs: { AppIcon: true } },
    })

    expect(wrapper.findAll('.run-rail-stages li')).toHaveLength(4)
    expect(wrapper.text()).toContain('本轮证据链已完成')
    expect(wrapper.text()).toContain('2/12')

    await wrapper.find('.run-rail-toggle').trigger('click')
    expect(wrapper.emitted('update:collapsed')).toEqual([[true]])
  })
})
