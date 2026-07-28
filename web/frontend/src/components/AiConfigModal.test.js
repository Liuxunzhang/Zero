import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  getAiProfiles: vi.fn(),
  getAiModelCatalog: vi.fn(),
  saveAiProfiles: vi.fn(),
  setActiveProfile: vi.fn(),
  getActiveProfile: vi.fn(),
  getAiPrompts: vi.fn(),
  saveAiPrompt: vi.fn(),
  deleteAiPrompt: vi.fn(),
  setActivePrompt: vi.fn(),
  getAiConfig: vi.fn(),
  getAiPersistConfig: vi.fn(),
  setAiPersistConfig: vi.fn(),
  getAiSettings: vi.fn(),
  saveAiSettings: vi.fn(),
  testAiProfile: vi.fn(),
}))

import {
  getActiveProfile,
  getAiConfig,
  getAiModelCatalog,
  getAiPersistConfig,
  getAiProfiles,
  getAiPrompts,
  getAiSettings,
} from '../api'
import AiConfigModal from './AiConfigModal.vue'

const flash = {
  id: 'deepseek-v4-flash',
  name: 'DeepSeek V4 Flash · 快速',
  provider: 'deepseek',
  credential_id: 'deepseek',
  base_url: 'https://api.deepseek.com',
  model: 'deepseek-v4-flash',
  protocol: 'openai_chat',
  context_window: 1_000_000,
  output_token_limit: 384_000,
  max_output_tokens: 8_192,
  temperature: 0.1,
  reasoning_level: 'off',
  agent_budget: { max_turns: 8, max_tool_calls: 12, max_seconds: 900 },
}

describe('AI configuration modal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getAiProfiles.mockResolvedValue({ profiles: [flash] })
    getAiModelCatalog.mockResolvedValue({ models: [flash] })
    getAiPrompts.mockResolvedValue({ prompts: [] })
    getAiConfig.mockResolvedValue({ model: flash.model, active_prompt_id: 'default' })
    getActiveProfile.mockResolvedValue({ profile: flash })
    getAiPersistConfig.mockResolvedValue({ persist_to_config_py: false })
    getAiSettings.mockResolvedValue({
      settings: {
        ai_max_tokens: 'auto',
        ai_temperature: 0.1,
        ai_context_max_rows: 500,
        ai_context_max_chars: 60000,
        ai_context_reserve_tokens: 32768,
        ai_context_recent_tokens: 64000,
        current_model: flash.model,
        model_token_limit: 384000,
      },
    })
  })

  it('shows effective profile limits and runtime-only settings', async () => {
    const wrapper = mount(AiConfigModal, {
      global: {
        stubs: { AppIcon: true },
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('8,192 输出')
    expect(wrapper.text()).toContain('off 推理')

    await wrapper.findAll('button').find((button) => button.text() === '推理参数').trigger('click')
    expect(wrapper.text()).toContain('Profile 推荐值')
    expect(wrapper.text()).toContain('输出预留 token')
    expect(wrapper.text()).not.toContain('最大历史轮数')
    expect(wrapper.text()).not.toContain('压缩记忆')
  })
})
