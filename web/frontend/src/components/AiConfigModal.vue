<template>
  <div class="ai-settings-overlay" @click.self="$emit('close')">
    <section class="ai-settings-shell" role="dialog" aria-modal="true" aria-labelledby="ai-settings-title">
      <header class="settings-header">
        <div class="settings-brand">
          <span class="settings-mark">Z/AI</span>
          <div>
            <span class="settings-kicker">AGENT CONTROL CENTER</span>
            <h2 id="ai-settings-title">取证智能体设置</h2>
          </div>
        </div>
        <div class="settings-header-status">
          <span class="status-dot"></span>
          <span>当前模型</span>
          <strong>{{ activeProfile?.name || configModel || '未配置' }}</strong>
        </div>
        <button type="button" class="settings-close" title="关闭设置" @click="$emit('close')">
          <AppIcon name="x" :size="16" />
        </button>
      </header>

      <div class="settings-layout">
        <aside class="settings-sidebar">
          <nav class="settings-nav" aria-label="AI 设置分类">
            <button
              v-for="item in navItems"
              :key="item.id"
              type="button"
              :class="{ active: tab === item.id }"
              @click="tab = item.id"
            >
              <span>{{ item.index }}</span>
              <div><b>{{ item.label }}</b><small>{{ item.caption }}</small></div>
            </button>
          </nav>

          <div class="sidebar-runtime">
            <span class="sidebar-label">RUNTIME SNAPSHOT</span>
            <strong>{{ activeProfile?.model || configModel || '—' }}</strong>
            <dl>
              <div><dt>策略</dt><dd>{{ activeProfile ? profileMode(activeProfile) : '默认' }}</dd></div>
              <div><dt>上下文</dt><dd>{{ formatCompact(activeProfile?.context_window) }}</dd></div>
              <div><dt>响应</dt><dd>{{ formatCompact(activeProfile?.max_output_tokens) }}</dd></div>
            </dl>
          </div>

          <label class="persist-control" title="仅同步非密钥字段，API Key 始终保存在私有凭据文件">
            <span>
              <b>同步启动配置</b>
              <small>不写入 API Key</small>
            </span>
            <input type="checkbox" v-model="persistToConfig" @change="onPersistConfigChange" />
            <i aria-hidden="true"></i>
          </label>
        </aside>

        <main class="settings-content">
          <div v-if="loading" class="settings-state">
            <span class="state-spinner"></span>
            <b>正在读取智能体配置</b>
            <small>加载模型、提示词与运行时参数…</small>
          </div>
          <div v-else-if="loadError" class="settings-state error">
            <b>配置加载失败</b>
            <small>{{ loadError }}</small>
            <button type="button" class="secondary-action" @click="loadData">重新加载</button>
          </div>

          <!-- Models -->
          <template v-else-if="tab === 'profiles'">
            <div class="content-heading">
              <div>
                <span class="content-index">01 / MODEL ROUTING</span>
                <h3>模型与执行策略</h3>
                <p>点击配置卡即可切换。Flash 面向高频分析，Pro 面向复杂证据链。</p>
              </div>
              <button type="button" class="primary-action" @click="openNewProfile">添加模型配置</button>
            </div>
            <div v-if="profileStatus.text && !showProfileEditor" class="model-status action-status" :class="profileStatus.type">
              {{ profileStatus.text }}
            </div>

            <section class="settings-section">
              <div class="section-heading">
                <div><b>可用配置</b><small>{{ profiles.length }} 个已保存配置</small></div>
                <span class="section-note">切换从下一次对话或 Agent 运行开始生效</span>
              </div>
              <div class="model-grid">
                <article
                  v-for="(profile, idx) in profiles"
                  :key="profile.id"
                  class="model-card"
                  :class="{
                    active: activeProfileId === profile.id,
                    pro: profile.reasoning_level && profile.reasoning_level !== 'off',
                    switching: switchingProfileId === profile.id,
                  }"
                >
                  <button type="button" class="model-select" @click="selectProfile(profile)">
                    <span class="model-card-top">
                      <span class="model-kind">{{ profileMode(profile) }}</span>
                      <span v-if="activeProfileId === profile.id" class="active-badge">正在使用</span>
                      <span v-else class="select-hint">{{ switchingProfileId === profile.id ? '切换中…' : '点击切换' }}</span>
                    </span>
                    <strong>{{ profile.name }}</strong>
                    <small class="model-description">{{ profileDescription(profile) }}</small>
                    <span class="model-metrics">
                      <span><b>{{ formatCompact(profile.context_window) }}</b><small>上下文</small></span>
                      <span><b>{{ formatCompact(profile.max_output_tokens) }}</b><small>响应</small></span>
                      <span><b>{{ profile.agent_budget?.max_tool_calls ?? 32 }}</b><small>工具</small></span>
                    </span>
                    <code>{{ profile.model }}</code>
                  </button>
                  <div class="model-actions">
                    <button type="button" @click="startEditProfile(profile)">
                      <AppIcon name="pencil" :size="12" /> 编辑
                    </button>
                    <button type="button" class="danger" @click="removeProfile(idx)">
                      <AppIcon name="trash" :size="12" /> 删除
                    </button>
                  </div>
                  <div v-if="profile.context_window_estimated" class="inline-warning">
                    上下文窗口为估算值，请按供应商文档确认。
                  </div>
                </article>

                <article class="model-card config-fallback" :class="{ active: !activeProfileId }">
                  <button type="button" class="model-select" @click="selectProfile(null)">
                    <span class="model-card-top">
                      <span class="model-kind">FALLBACK</span>
                      <span v-if="!activeProfileId" class="active-badge">正在使用</span>
                      <span v-else class="select-hint">点击切换</span>
                    </span>
                    <strong>config.py 默认配置</strong>
                    <small class="model-description">兼容旧配置；推荐优先使用上方可审计的 Profile。</small>
                    <code>{{ configModel || '未配置模型' }}</code>
                  </button>
                </article>
              </div>
            </section>

            <section v-if="showProfileEditor" class="settings-section profile-editor">
              <div class="section-heading">
                <div>
                  <b>{{ editingProfileId ? '编辑模型配置' : '创建模型配置' }}</b>
                  <small>密钥使用共享凭据 ID 安全复用，不会显示明文</small>
                </div>
                <button type="button" class="icon-text-action" @click="cancelEditProfile">收起编辑器</button>
              </div>

              <div class="catalog-picker">
                <label for="model-catalog">从内置目录填充</label>
                <select id="model-catalog" v-model="catalogSelection" @change="applyCatalog(catalogSelection)">
                  <option value="">手动配置</option>
                  <option v-for="(model, index) in modelCatalog" :key="model.model" :value="String(index)">
                    {{ model.name }} · {{ model.protocol }}
                  </option>
                </select>
                <span>选择预设后仍可调整所有字段</span>
              </div>

              <div class="editor-groups">
                <fieldset>
                  <legend><span>01</span> 连接与凭据</legend>
                  <div class="editor-grid">
                    <label><span>显示名称</span><input v-model="newProfile.name" placeholder="例如：DeepSeek 分析模型" /></label>
                    <label><span>供应商</span><input v-model="newProfile.provider" placeholder="deepseek / openai" /></label>
                    <label class="wide"><span>API 地址</span><input v-model="newProfile.base_url" placeholder="https://api.example.com/v1" /></label>
                    <label><span>共享凭据 ID</span><input v-model="newProfile.credential_id" placeholder="例如：deepseek" /></label>
                    <label>
                      <span>API Key</span>
                      <input v-model="newProfile.api_key" type="password" :placeholder="newProfile.has_api_key ? '已安全保存；留空保持不变' : 'sk-…'" />
                    </label>
                  </div>
                </fieldset>

                <fieldset>
                  <legend><span>02</span> 模型与限额</legend>
                  <div class="editor-grid">
                    <label><span>模型名称</span><input v-model="newProfile.model" placeholder="模型 API 标识" /></label>
                    <label>
                      <span>接口协议</span>
                      <select v-model="newProfile.protocol">
                        <option value="openai_responses">OpenAI Responses</option>
                        <option value="openai_chat">OpenAI-compatible Chat</option>
                        <option value="anthropic_messages">Anthropic Messages</option>
                        <option value="google_genai">Google GenerateContent</option>
                      </select>
                    </label>
                    <label><span>上下文窗口</span><input v-model.number="newProfile.context_window" type="number" min="4096" step="1024" /></label>
                    <label><span>推荐响应 token</span><input v-model.number="newProfile.max_output_tokens" type="number" min="1" step="1024" /></label>
                    <label><span>供应商输出硬上限</span><input v-model.number="newProfile.output_token_limit" type="number" min="1" step="1024" /></label>
                    <label><span>温度</span><input v-model.number="newProfile.temperature" type="number" min="0" max="2" step="0.1" placeholder="由供应商决定" /></label>
                    <label>
                      <span>Reasoning 档位</span>
                      <select v-model="newProfile.reasoning_level">
                        <option value="off">off</option>
                        <option value="low">low</option>
                        <option value="medium">medium</option>
                        <option value="high">high</option>
                        <option value="max">max</option>
                      </select>
                    </label>
                  </div>
                </fieldset>

                <fieldset>
                  <legend><span>03</span> Agent 执行预算</legend>
                  <div class="budget-grid">
                    <label><span>最大轮次</span><input v-model.number="newProfile.agent_budget.max_turns" type="number" min="1" max="100" /></label>
                    <label><span>最大工具调用</span><input v-model.number="newProfile.agent_budget.max_tool_calls" type="number" min="0" max="200" /></label>
                    <label><span>最长运行秒数</span><input v-model.number="newProfile.agent_budget.max_seconds" type="number" min="1" max="86400" /></label>
                  </div>
                  <div class="capability-row">
                    <label><input type="checkbox" v-model="newProfile.capabilities.tools" /><span>工具调用</span></label>
                    <label><input type="checkbox" v-model="newProfile.capabilities.reasoning" /><span>Reasoning</span></label>
                    <label><input type="checkbox" v-model="newProfile.capabilities.thinking_summary" /><span>思考摘要</span></label>
                  </div>
                </fieldset>
              </div>

              <div class="editor-footer">
                <span v-if="profileStatus.text" class="action-status" :class="profileStatus.type">{{ profileStatus.text }}</span>
                <div>
                  <button type="button" class="secondary-action" @click="testConnection" :disabled="!canSaveProfile || profileTesting">
                    {{ profileTesting ? '正在测试…' : '测试连接' }}
                  </button>
                  <button type="button" class="primary-action" @click="saveProfile" :disabled="!canSaveProfile">
                    {{ editingProfileId ? '保存更改' : '创建并启用' }}
                  </button>
                </div>
              </div>
            </section>
          </template>

          <!-- Prompts -->
          <template v-else-if="tab === 'prompts'">
            <div class="content-heading">
              <div>
                <span class="content-index">02 / ANALYSIS POLICY</span>
                <h3>取证分析策略</h3>
                <p>提示词决定证据门槛、风险措辞和 Agent 的补证方式。</p>
              </div>
              <button type="button" class="primary-action" @click="showPromptEditor = !showPromptEditor">
                {{ showPromptEditor ? '收起编辑器' : '新建策略' }}
              </button>
            </div>

            <section class="settings-section">
              <div class="prompt-grid">
                <article
                  v-for="prompt in prompts"
                  :key="prompt.id"
                  class="policy-card"
                  :class="{ active: activePromptId === prompt.id, expanded: expandedPrompt === prompt.id }"
                >
                  <button type="button" class="policy-select" @click="selectPrompt(prompt.id)">
                    <span class="policy-top">
                      <span>{{ prompt.builtin ? 'BUILT-IN' : 'CUSTOM' }}</span>
                      <b v-if="activePromptId === prompt.id">当前策略</b>
                    </span>
                    <strong>{{ prompt.name }}</strong>
                    <p>{{ prompt.content.slice(0, 150) }}{{ prompt.content.length > 150 ? '…' : '' }}</p>
                  </button>
                  <div class="policy-actions">
                    <button type="button" @click="expandedPrompt = expandedPrompt === prompt.id ? null : prompt.id">
                      {{ expandedPrompt === prompt.id ? '收起全文' : '查看全文' }}
                    </button>
                    <button v-if="!prompt.builtin" type="button" class="danger" @click="removePrompt(prompt.id)">删除</button>
                  </div>
                  <pre v-if="expandedPrompt === prompt.id">{{ prompt.content }}</pre>
                </article>
              </div>
            </section>

            <section v-if="showPromptEditor" class="settings-section prompt-editor">
              <div class="section-heading">
                <div><b>创建自定义策略</b><small>建议明确证据阈值、允许的工具动作与输出结构</small></div>
              </div>
              <label class="editor-field"><span>策略名称</span><input v-model="newPrompt.name" placeholder="例如：Linux 低误报研判" /></label>
              <label class="editor-field"><span>系统提示词</span><textarea v-model="newPrompt.content" rows="12" placeholder="输入分析约束与输出要求…" /></label>
              <div class="editor-footer">
                <span>保存后可立即设为当前策略</span>
                <button type="button" class="primary-action" @click="addPrompt" :disabled="!canAddPrompt">保存策略</button>
              </div>
            </section>
          </template>

          <!-- Runtime -->
          <template v-else>
            <div class="content-heading">
              <div>
                <span class="content-index">03 / RUNTIME TUNING</span>
                <h3>运行时参数</h3>
                <p>Profile 提供安全基线；这里仅覆盖当前运行时的输出和上下文策略。</p>
              </div>
            </div>

            <div class="runtime-summary">
              <div><span>当前模型</span><strong>{{ aiSettings.current_model || '—' }}</strong></div>
              <div><span>模型输出上限</span><strong>{{ formatCompact(aiSettings.model_token_limit) }}</strong></div>
              <div><span>当前输出策略</span><strong>{{ maxTokenModeLabel }}</strong></div>
            </div>

            <section class="settings-section runtime-section">
              <div class="section-heading">
                <div><b>响应控制</b><small>决定单次回答的长度与随机性</small></div>
              </div>
              <div class="setting-block">
                <div class="setting-copy">
                  <b>最大响应 token</b>
                  <small>推荐使用 Profile 自动值；MAX 适合需要完整长报告时临时开启。</small>
                </div>
                <div class="segmented-control">
                  <button type="button" :class="{ active: aiMaxTokensMode === 'auto' }" @click="aiMaxTokensMode = 'auto'">自动</button>
                  <button type="button" :class="{ active: aiMaxTokensMode === 'custom' }" @click="aiMaxTokensMode = 'custom'">自定义</button>
                  <button type="button" :class="{ active: aiMaxTokensMode === 'max' }" @click="aiMaxTokensMode = 'max'">MAX</button>
                </div>
                <input v-if="aiMaxTokensMode === 'custom'" class="compact-input" v-model.number="aiSettings.ai_max_tokens" type="number" min="1" />
              </div>
              <div class="setting-block">
                <div class="setting-copy">
                  <b>温度</b>
                  <small>低温度更稳定，适合可复核的取证结论。</small>
                </div>
                <div class="range-control">
                  <input v-model.number="aiSettings.ai_temperature" type="range" min="0" max="2" step="0.1" />
                  <input v-model.number="aiSettings.ai_temperature" type="number" min="0" max="2" step="0.1" />
                </div>
              </div>
              <div v-if="temperatureHint" class="inline-warning">{{ temperatureHint }}</div>
            </section>

            <section class="settings-section runtime-section">
              <div class="section-heading">
                <div><b>证据上下文</b><small>控制插件结果、近期对话与输出空间之间的配额</small></div>
              </div>
              <div class="context-settings-grid">
                <label>
                  <span>插件字符预算<small>单次附带的原始证据文本</small></span>
                  <input v-model.number="aiSettings.ai_context_max_chars" type="number" min="1000" step="1000" />
                </label>
                <label>
                  <span>最大数据行数<small>MAX 将传递所有可用行</small></span>
                  <div class="inline-input">
                    <input v-model.number="aiSettings.ai_context_max_rows" type="number" min="1" :disabled="aiContextRowsIsMax" />
                    <button type="button" :class="{ active: aiContextRowsIsMax }" @click="aiContextRowsIsMax = !aiContextRowsIsMax">MAX</button>
                  </div>
                </label>
                <label>
                  <span>输出预留 token<small>为最终回答保留的窗口空间</small></span>
                  <input v-model.number="aiSettings.ai_context_reserve_tokens" type="number" min="1024" step="1024" />
                </label>
                <label>
                  <span>近期上下文 token<small>压缩前保留的最近对话</small></span>
                  <input v-model.number="aiSettings.ai_context_recent_tokens" type="number" min="1024" step="1024" />
                </label>
              </div>
              <div class="runtime-hints">
                <span>{{ aiLimitHint }}</span>
                <strong v-if="aiMaxTokensMode === 'max' || aiContextRowsIsMax">MAX 会增加时延和调用成本</strong>
              </div>
            </section>

            <div class="save-bar">
              <span class="action-status" :class="settingsStatus.type">{{ settingsStatus.text || '更改只影响后续运行' }}</span>
              <button type="button" class="primary-action" @click="saveSettings" :disabled="settingsSaving">
                {{ settingsSaving ? '正在保存…' : '保存运行时参数' }}
              </button>
            </div>
          </template>
        </main>
      </div>
    </section>
  </div>
</template>

<script setup>
import AppIcon from './AppIcon.vue'
import { confirmAction } from '../composables/confirm'
import { useEscClose } from '../composables/useEscClose'
import { ref, computed, nextTick, onMounted } from 'vue'
import {
  getAiProfiles, getAiModelCatalog, saveAiProfiles, setActiveProfile, getActiveProfile,
  getAiPrompts, saveAiPrompt, deleteAiPrompt, setActivePrompt,
  getAiConfig, getAiPersistConfig, setAiPersistConfig, getAiSettings, saveAiSettings,
  testAiProfile,
} from '../api'

const emit = defineEmits(['close', 'config-changed'])

useEscClose(() => true, () => emit('close'))

const tab = ref('profiles')
const navItems = [
  { id: 'profiles', index: '01', label: '模型策略', caption: '切换与配置' },
  { id: 'prompts', index: '02', label: '分析策略', caption: '证据与措辞' },
  { id: 'settings', index: '03', label: '运行参数', caption: '输出与上下文' },
]
const profiles = ref([])
const modelCatalog = ref([])
const prompts = ref([])
const activeProfileId = ref(null)
const activePromptId = ref('default')
const configModel = ref('')
const expandedPrompt = ref(null)
const persistToConfig = ref(true)
const editingProfileId = ref('')
const profileStatus = ref({ type: '', text: '' })
const profileTesting = ref(false)
const settingsStatus = ref({ type: '', text: '' })
const settingsSaving = ref(false)
const switchingProfileId = ref('')
const showProfileEditor = ref(false)
const showPromptEditor = ref(false)
const catalogSelection = ref('')
const loading = ref(true)
const loadError = ref('')
const aiSettings = ref({
  ai_max_tokens: 8192,
  ai_temperature: 0.1,
  ai_context_max_rows: 500,
  ai_context_max_chars: 60000,
  ai_context_reserve_tokens: 32768,
  ai_context_recent_tokens: 64000,
  model_token_limit: null,
  current_model: '',
})
const aiMaxTokensMode = ref('auto')
const aiContextRowsIsMax = ref(false)

const newProfile = ref({
  name: '',
  provider: '',
  credential_id: '',
  base_url: '',
  api_key: '',
  model: '',
  protocol: 'openai_chat',
  context_window: 65536,
  output_token_limit: 4096,
  max_output_tokens: 4096,
  temperature: null,
  reasoning_level: 'off',
  agent_budget: { max_turns: 16, max_tool_calls: 32, max_seconds: 1800 },
  capabilities: { tools: true, reasoning: true, thinking_summary: true, usage: true },
})
const newPrompt = ref({ name: '', content: '' })

const canSaveProfile = computed(() =>
  newProfile.value.name.trim() && newProfile.value.model.trim()
)
const canAddPrompt = computed(() =>
  newPrompt.value.name.trim() && newPrompt.value.content.trim()
)
const activeProfile = computed(() =>
  profiles.value.find(profile => profile.id === activeProfileId.value) || null
)
const maxTokenModeLabel = computed(() => ({
  auto: 'Profile 自动',
  custom: `${Number(aiSettings.value.ai_max_tokens || 0).toLocaleString()} token`,
  max: '供应商 MAX',
}[aiMaxTokensMode.value] || aiMaxTokensMode.value))

const aiLimitHint = computed(() => {
  const model = aiSettings.value.current_model || '当前模型'
  const limit = aiSettings.value.model_token_limit
  if (!limit) return `${model}: 未配置上限映射，max 将使用 provider 默认限制`
  return `${model}: max token 上限约 ${limit.toLocaleString()}`
})
const temperatureHint = computed(() =>
  aiSettings.value.current_model === 'deepseek-v4-pro'
    ? 'DeepSeek V4 Pro 的思考模式会忽略 temperature，以 reasoning effort 为准。'
    : ''
)

async function loadData() {
  loading.value = true
  loadError.value = ''
  try {
    const [profData, catalogData, promptData, cfgData, activeData, persistData, settingsData] = await Promise.all([
      getAiProfiles(),
      getAiModelCatalog(),
      getAiPrompts(),
      getAiConfig(),
      getActiveProfile(),
      getAiPersistConfig(),
      getAiSettings(),
    ])
    profiles.value = profData.profiles || []
    modelCatalog.value = catalogData.models || []
    prompts.value = promptData.prompts || []
    configModel.value = cfgData.model || ''
    activePromptId.value = cfgData.active_prompt_id || 'default'
    persistToConfig.value = !!persistData.persist_to_config_py
    if (activeData.profile) {
      activeProfileId.value = activeData.profile.id || null
    } else {
      activeProfileId.value = null
    }
    const s = settingsData.settings || {}
    aiMaxTokensMode.value = ['auto', 'max'].includes(s.ai_max_tokens)
      ? s.ai_max_tokens
      : 'custom'
    aiContextRowsIsMax.value = s.ai_context_max_rows === 'max'
    aiSettings.value = {
      ai_max_tokens: typeof s.ai_max_tokens === 'number' ? s.ai_max_tokens : 8192,
      ai_temperature: s.ai_temperature ?? 0.1,
      ai_context_max_rows: s.ai_context_max_rows === 'max' ? 500 : (s.ai_context_max_rows || 500),
      ai_context_max_chars: s.ai_context_max_chars || 60000,
      ai_context_reserve_tokens: s.ai_context_reserve_tokens || 32768,
      ai_context_recent_tokens: s.ai_context_recent_tokens || 64000,
      model_token_limit: s.model_token_limit ?? null,
      current_model: s.current_model || cfgData.model || '',
    }
  } catch (e) {
    loadError.value = e?.message || '无法读取 AI 配置'
    console.error('Failed to load AI config:', e)
  } finally {
    loading.value = false
  }
}

function formatCompact(value) {
  const number = Number(value || 0)
  if (!number) return '—'
  if (number >= 1_000_000) return `${(number / 1_000_000).toFixed(number % 1_000_000 ? 1 : 0)}M`
  if (number >= 1_000) return `${(number / 1_000).toFixed(number % 1_000 ? 1 : 0)}K`
  return number.toLocaleString()
}

function profileMode(profile) {
  if (!profile) return 'DEFAULT'
  return profile.reasoning_level && profile.reasoning_level !== 'off'
    ? 'PRO / REASONING'
    : 'FLASH / FAST'
}

function profileDescription(profile) {
  if (profile.reasoning_level && profile.reasoning_level !== 'off') {
    return '复杂证据链、深度交叉验证与长任务'
  }
  return '常规研判、结果解释与高频快速分析'
}

function applyCatalog(index) {
  if (index === '') return
  const model = modelCatalog.value[Number(index)]
  if (!model) return
  newProfile.value = {
    ...newProfile.value,
    ...model,
    api_key: newProfile.value.api_key,
  }
}

async function saveSettings() {
  const payload = {
    ai_max_tokens: aiMaxTokensMode.value === 'custom'
      ? Number(aiSettings.value.ai_max_tokens || 8192)
      : aiMaxTokensMode.value,
    ai_temperature: Number(aiSettings.value.ai_temperature ?? 0.1),
    ai_context_max_rows: aiContextRowsIsMax.value ? 'max' : Number(aiSettings.value.ai_context_max_rows || 500),
    ai_context_max_chars: Number(aiSettings.value.ai_context_max_chars || 60000),
    ai_context_reserve_tokens: Number(aiSettings.value.ai_context_reserve_tokens || 32768),
    ai_context_recent_tokens: Number(aiSettings.value.ai_context_recent_tokens || 64000),
  }
  settingsSaving.value = true
  settingsStatus.value = { type: '', text: '' }
  try {
    const data = await saveAiSettings(payload)
    const s = data.settings || {}
    aiMaxTokensMode.value = ['auto', 'max'].includes(s.ai_max_tokens)
      ? s.ai_max_tokens
      : 'custom'
    aiContextRowsIsMax.value = s.ai_context_max_rows === 'max'
    aiSettings.value = {
      ...aiSettings.value,
      ai_max_tokens: typeof s.ai_max_tokens === 'number' ? s.ai_max_tokens : aiSettings.value.ai_max_tokens,
      ai_temperature: s.ai_temperature,
      ai_context_max_rows: s.ai_context_max_rows === 'max' ? aiSettings.value.ai_context_max_rows : s.ai_context_max_rows,
      ai_context_max_chars: s.ai_context_max_chars || aiSettings.value.ai_context_max_chars,
      ai_context_reserve_tokens: s.ai_context_reserve_tokens || aiSettings.value.ai_context_reserve_tokens,
      ai_context_recent_tokens: s.ai_context_recent_tokens || aiSettings.value.ai_context_recent_tokens,
      model_token_limit: s.model_token_limit ?? aiSettings.value.model_token_limit,
      current_model: s.current_model || aiSettings.value.current_model,
    }
    settingsStatus.value = { type: 'success', text: '运行时参数已保存，将从下一次运行生效。' }
    emit('config-changed')
  } catch (e) {
    settingsStatus.value = { type: 'error', text: e?.message || '参数保存失败' }
    console.error('Failed to save AI settings:', e)
  } finally {
    settingsSaving.value = false
  }
}

async function onPersistConfigChange() {
  const next = !!persistToConfig.value
  try {
    const data = await setAiPersistConfig(next)
    persistToConfig.value = !!data.persist_to_config_py
    emit('config-changed')
  } catch (e) {
    persistToConfig.value = !next
    console.error('Failed to update persist config:', e)
  }
}

async function selectProfile(profile) {
  const targetId = profile?.id || '__config__'
  if (switchingProfileId.value || activeProfileId.value === (profile?.id || null)) return
  switchingProfileId.value = targetId
  profileStatus.value = { type: '', text: '' }
  try {
    await setActiveProfile(profile)
    activeProfileId.value = profile?.id || null
    aiSettings.value.current_model = profile?.model || configModel.value
    aiSettings.value.model_token_limit = profile?.output_token_limit || null
    profileStatus.value = {
      type: 'success',
      text: `已切换到 ${profile?.name || 'config.py 默认配置'}，从下一次运行生效。`,
    }
    emit('config-changed')
  } catch (e) {
    profileStatus.value = { type: 'error', text: e?.message || '模型切换失败' }
    console.error('Failed to set profile:', e)
  } finally {
    switchingProfileId.value = ''
  }
}

function startEditProfile(profile) {
  profileStatus.value = { type: '', text: '' }
  editingProfileId.value = profile.id || ''
  showProfileEditor.value = true
  catalogSelection.value = ''
  newProfile.value = {
    id: profile.id || '',
    name: profile.name || '',
    provider: profile.provider || '',
    credential_id: profile.credential_id || profile.id || '',
    base_url: profile.base_url || '',
    api_key: profile.api_key || '',
    has_api_key: !!profile.has_api_key,
    model: profile.model || '',
    protocol: profile.protocol || 'openai_chat',
    context_window: profile.context_window || 65536,
    output_token_limit: profile.output_token_limit || 4096,
    max_output_tokens: profile.max_output_tokens || 4096,
    temperature: profile.temperature ?? null,
    reasoning_level: profile.reasoning_level || 'off',
    agent_budget: {
      max_turns: 16, max_tool_calls: 32, max_seconds: 1800,
      ...(profile.agent_budget || {}),
    },
    capabilities: {
      tools: true, reasoning: true, thinking_summary: true, usage: true,
      ...(profile.capabilities || {}),
    },
  }
  nextTick(() => document.querySelector('.profile-editor')?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
}

function openNewProfile() {
  cancelEditProfile()
  profileStatus.value = { type: '', text: '' }
  showProfileEditor.value = true
  nextTick(() => document.querySelector('.profile-editor')?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
}

function cancelEditProfile() {
  editingProfileId.value = ''
  showProfileEditor.value = false
  catalogSelection.value = ''
  newProfile.value = {
    name: '', provider: '', credential_id: '', base_url: '', api_key: '', model: '',
    protocol: 'openai_chat', context_window: 65536,
    output_token_limit: 4096, max_output_tokens: 4096, temperature: null,
    reasoning_level: 'off',
    agent_budget: { max_turns: 16, max_tool_calls: 32, max_seconds: 1800 },
    capabilities: { tools: true, reasoning: true, thinking_summary: true, usage: true },
  }
}

async function testConnection() {
  if (!canSaveProfile.value || profileTesting.value) return
  profileTesting.value = true
  profileStatus.value = { type: '', text: '' }
  try {
    const data = await testAiProfile(profilePayload(newProfile.value))
    profileStatus.value = {
      type: data.ok ? 'success' : 'error',
      text: data.ok ? `连接成功：${data.protocol} / ${data.model}` : '模型未返回文本。',
    }
  } catch (error) {
    profileStatus.value = { type: 'error', text: error?.message || '连接测试失败' }
  } finally {
    profileTesting.value = false
  }
}

function profilePayload(profile) {
  return {
    ...profile,
    temperature: profile.temperature === '' ? null : profile.temperature,
    agent_budget: {
      max_turns: Number(profile.agent_budget?.max_turns || 16),
      max_tool_calls: Number(profile.agent_budget?.max_tool_calls ?? 32),
      max_seconds: Number(profile.agent_budget?.max_seconds || 1800),
    },
  }
}

async function saveProfile() {
  if (!canSaveProfile.value) return
  const draft = profilePayload(newProfile.value)
  const targetId = editingProfileId.value || ''
  const nextProfiles = [...profiles.value]

  if (targetId) {
    const idx = nextProfiles.findIndex((p) => p.id === targetId)
    if (idx >= 0) {
      nextProfiles[idx] = { ...nextProfiles[idx], ...draft }
    }
  } else {
    nextProfiles.push(draft)
  }

  try {
    const data = await saveAiProfiles(nextProfiles)
    const savedProfiles = data.profiles || []
    profiles.value = savedProfiles
    const saved = targetId
      ? savedProfiles.find((p) => p.id === targetId)
      : savedProfiles[savedProfiles.length - 1]
    if (saved) {
      await selectProfile(saved)
    }
    profileStatus.value = {
      type: 'success',
      text: persistToConfig.value
        ? '已保存，并将非密钥字段同步写入 config.py。'
        : '已保存到 .zero/ai/profiles.json。',
    }
    cancelEditProfile()
  } catch (e) {
    profileStatus.value = { type: 'error', text: e?.message || '保存配置失败' }
    console.error('Failed to save profile:', e)
  }
}

async function removeProfile(idx) {
  const removed = profiles.value[idx]
  const ok = await confirmAction({
    title: '删除模型配置',
    message: `将删除配置「${removed?.name || removed?.model || removed?.id}」。`,
    confirmText: '删除',
  })
  if (!ok) return
  const nextProfiles = profiles.value.filter((_profile, profileIndex) => profileIndex !== idx)
  try {
    const data = await saveAiProfiles(nextProfiles)
    profiles.value = data.profiles || nextProfiles
    if (activeProfileId.value === removed.id) {
      await selectProfile(null)
    }
    if (editingProfileId.value === removed.id) {
      cancelEditProfile()
    }
    profileStatus.value = { type: 'success', text: `已删除 ${removed?.name || '模型配置'}。` }
  } catch (e) {
    profileStatus.value = { type: 'error', text: e?.message || '删除模型配置失败' }
    console.error('Failed to remove profile:', e)
  }
}

async function selectPrompt(id) {
  try {
    await setActivePrompt(id)
    activePromptId.value = id
    emit('config-changed')
  } catch (e) {
    console.error('Failed to set prompt:', e)
  }
}

async function addPrompt() {
  if (!canAddPrompt.value) return
  try {
    await saveAiPrompt(newPrompt.value)
    newPrompt.value = { name: '', content: '' }
    await loadData()
    tab.value = 'prompts'
    showPromptEditor.value = false
  } catch (e) {
    console.error('Failed to save prompt:', e)
  }
}

async function removePrompt(id) {
  const ok = await confirmAction({
    title: '删除提示词',
    message: '将永久删除该自定义提示词。',
    confirmText: '删除',
  })
  if (!ok) return
  try {
    await deleteAiPrompt(id)
    await loadData()
  } catch (e) {
    console.error('Failed to delete prompt:', e)
  }
}

onMounted(loadData)
</script>

<style scoped src="./AiConfigModal.css"></style>
