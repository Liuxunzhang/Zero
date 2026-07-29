import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  getRuntimeSettings: vi.fn(),
  saveRuntimeSettings: vi.fn(),
}))

import { getRuntimeSettings, saveRuntimeSettings } from '../api'
import SystemSettingsModal from './SystemSettingsModal.vue'

describe('System settings modal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getRuntimeSettings.mockResolvedValue({
      settings: { plugin_timeout_seconds: 600 },
      categories: [
        {
          id: 'runtime',
          label: '运行与超时',
          description: '运行配置',
          fields: [
            {
              key: 'plugin_timeout_seconds',
              label: '插件超时',
              description: '最大运行时间',
              type: 'integer',
              min: 1,
              max: 3600,
              default: 600,
              unit: '秒',
            },
          ],
        },
      ],
      storage: '.zero/runtime_settings.json',
    })
    saveRuntimeSettings.mockResolvedValue({
      settings: { plugin_timeout_seconds: 600 },
    })
  })

  it('edits plugin defaults inside System and saves them separately', async () => {
    const wrapper = mount(SystemSettingsModal, {
      props: {
        engineId: 'vol3',
        globalArgs: { pid: '120', regex: '' },
      },
      global: {
        stubs: { Teleport: true, AppIcon: true },
      },
    })
    await flushPromises()

    const argsTab = wrapper
      .findAll('.system-settings-nav-item')
      .find((button) => button.text().includes('插件参数'))
    await argsTab.trigger('click')

    const pidField = wrapper
      .findAll('.plugin-arg-field')
      .find((field) => field.text().includes('--pid'))
    expect(pidField.find('input').element.value).toBe('120')
    await pidField.find('input').setValue('369')
    await wrapper.find('.system-settings-save').trigger('click')
    await flushPromises()

    expect(saveRuntimeSettings).not.toHaveBeenCalled()
    expect(wrapper.emitted('update:globalArgs')[0][0]).toEqual({ pid: '369' })
  })
})
