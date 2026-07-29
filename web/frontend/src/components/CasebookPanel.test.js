import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import CasebookPanel from './CasebookPanel.vue'

describe('Casebook panel', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('keeps findings and notes in one tabbed workspace', async () => {
    const wrapper = mount(CasebookPanel, {
      props: { show: true },
      global: {
        stubs: { Teleport: true, AppIcon: true },
      },
    })

    expect(wrapper.text()).toContain('取证工作簿')
    expect(wrapper.findAll('.casebook-tab')).toHaveLength(2)

    await wrapper.findAll('.casebook-tab')[1].trigger('click')
    const textarea = wrapper.find('.notes-textarea')
    await textarea.setValue('PID 369 需要继续验证')
    await textarea.trigger('input')

    expect(localStorage.getItem('zero-notepad')).toBe('PID 369 需要继续验证')
    expect(wrapper.text()).toContain('自动保存')
  })
})
