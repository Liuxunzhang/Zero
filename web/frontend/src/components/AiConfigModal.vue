<template>
  <div class="modal-overlay" @click.self="$emit('close')">
    <div class="modal-container">
      <!-- Tab bar -->
      <div class="modal-tabs">
        <button
          class="modal-tab"
          :class="{ active: tab === 'profiles' }"
          @click="tab = 'profiles'"
        >模型配置</button>
        <button
          class="modal-tab"
          :class="{ active: tab === 'prompts' }"
          @click="tab = 'prompts'"
        >取证提示词</button>
        <button
          class="modal-tab"
          :class="{ active: tab === 'settings' }"
          @click="tab = 'settings'"
        >推理参数</button>
        <button class="modal-close-btn" @click="$emit('close')">关闭</button>
      </div>

      <!-- ═══ Profiles tab ═══ -->
      <div v-if="tab === 'profiles'" class="modal-body">
        <div class="section-desc">
          配置用于内存取证分析的模型。建议使用低温度、较长上下文，并避免把密钥写回源码配置。
        </div>

        <div class="persist-toggle-row">
          <label class="persist-toggle-label" title="开启后会同步非密钥配置到 config.py">
            <input type="checkbox" v-model="persistToConfig" @change="onPersistConfigChange" />
            <span>保存配置</span>
          </label>
          <span class="persist-toggle-hint">开启后同步非密钥字段到 config.py</span>
        </div>

        <!-- Profile list -->
        <div class="profile-list">
          <!-- config.py default -->
          <div
            class="profile-card"
            :class="{ active: !activeProfileId }"
            @click="selectProfile(null)"
          >
            <div class="profile-card-header">
              <span class="profile-card-dot" :class="{ active: !activeProfileId }"></span>
              <span class="profile-card-name">config.py 默认配置</span>
            </div>
            <div class="profile-card-info">
              <span class="profile-card-tag">{{ configModel }}</span>
            </div>
          </div>

          <!-- Saved profiles -->
          <div
            v-for="(p, idx) in profiles"
            :key="p.id"
            class="profile-card"
            :class="{ active: activeProfileId === p.id }"
            @click="selectProfile(p)"
          >
            <div class="profile-card-header">
              <span class="profile-card-dot" :class="{ active: activeProfileId === p.id }"></span>
              <span class="profile-card-name">{{ p.name }}</span>
              <button class="profile-card-edit" @click.stop="startEditProfile(p)" title="编辑"><AppIcon name="pencil" :size="12" /></button>
              <button class="profile-card-delete" @click.stop="removeProfile(idx)" title="删除"><AppIcon name="trash" :size="12" /></button>
            </div>
            <div class="profile-card-info">
              <span class="profile-card-tag">{{ p.model }}</span>
              <span class="profile-card-url">{{ p.base_url }}</span>
            </div>
          </div>
        </div>

        <!-- Add new profile form -->
        <div class="add-section">
          <div class="add-section-title">{{ editingProfileId ? '编辑配置' : '添加新配置' }}</div>
          <div class="form-grid">
            <div class="form-group">
              <label>名称</label>
              <input v-model="newProfile.name" placeholder="如: DeepSeek V3" />
            </div>
            <div class="form-group">
              <label>API 地址</label>
              <input v-model="newProfile.base_url" placeholder="https://api.example.com/v1" />
            </div>
            <div class="form-group">
              <label>API Key</label>
              <input v-model="newProfile.api_key" type="password" placeholder="sk-..." />
            </div>
            <div class="form-group">
              <label>模型名称</label>
              <input v-model="newProfile.model" placeholder="gpt-4o / deepseek-chat / ..." />
            </div>
          </div>
          <div class="profile-form-actions">
            <button class="add-btn" @click="saveProfile" :disabled="!canSaveProfile">
              {{ editingProfileId ? '保存配置' : '+ 添加配置' }}
            </button>
            <button v-if="editingProfileId" class="form-cancel-btn" @click="cancelEditProfile">取消编辑</button>
          </div>
          <div v-if="profileStatus.text" class="profile-save-status" :class="profileStatus.type">
            {{ profileStatus.text }}
          </div>
        </div>
      </div>

      <!-- ═══ Prompts tab ═══ -->
      <div v-if="tab === 'prompts'" class="modal-body">
        <div class="section-desc">
          选择或自定义取证提示词，控制证据引用、风险分级、过滤规则和下一步插件建议。
        </div>

        <!-- Prompt list -->
        <div class="prompt-list">
          <div
            v-for="p in prompts"
            :key="p.id"
            class="prompt-card"
            :class="{ active: activePromptId === p.id }"
          >
            <div class="prompt-card-header" @click="selectPrompt(p.id)">
              <span class="profile-card-dot" :class="{ active: activePromptId === p.id }"></span>
              <span class="prompt-card-name">{{ p.name }}</span>
              <span v-if="p.builtin" class="prompt-card-badge">内置</span>
              <button
                v-if="!p.builtin"
                class="profile-card-delete"
                @click.stop="removePrompt(p.id)"
                title="删除"
              ><AppIcon name="trash" :size="12" /></button>
            </div>
            <div class="prompt-card-preview" @click="selectPrompt(p.id)">
              {{ p.content.slice(0, 120) }}...
            </div>
            <button
              v-if="expandedPrompt !== p.id"
              class="prompt-expand-btn"
              @click="expandedPrompt = p.id"
            >展开全文</button>
            <div v-else class="prompt-full-content">
              <pre>{{ p.content }}</pre>
              <button class="prompt-expand-btn" @click="expandedPrompt = null">收起</button>
            </div>
          </div>
        </div>

        <!-- Add custom prompt -->
        <div class="add-section">
          <div class="add-section-title">添加自定义提示词</div>
          <div class="form-group full">
            <label>名称</label>
            <input v-model="newPrompt.name" placeholder="如: 网络连接分析" />
          </div>
          <div class="form-group full">
            <label>提示词内容</label>
            <textarea
              v-model="newPrompt.content"
              rows="6"
              placeholder="输入系统提示词..."
            />
          </div>
          <button class="add-btn" @click="addPrompt" :disabled="!canAddPrompt">
            + 添加提示词
          </button>
        </div>
      </div>

      <!-- ═══ Settings tab ═══ -->
      <div v-if="tab === 'settings'" class="modal-body">
        <div class="section-desc">
          面向内存取证场景调整推理参数：低温度、较长上下文、有限历史和可追踪压缩记忆。
        </div>

        <div class="add-section">
          <div class="add-section-title">推理参数</div>

          <div class="setting-row">
            <label class="setting-label">最大响应 token 数</label>
            <div class="setting-controls">
              <input
                v-model.number="aiSettings.ai_max_tokens"
                type="number"
                min="1"
                :disabled="aiMaxTokensIsMax"
                placeholder="如 4096"
              />
              <label class="setting-inline-toggle">
                <input type="checkbox" v-model="aiMaxTokensIsMax" />
                <span>max</span>
              </label>
            </div>
          </div>

          <div class="setting-row">
            <label class="setting-label">温度 (0-2)</label>
            <div class="setting-controls">
              <input v-model.number="aiSettings.ai_temperature" type="number" min="0" max="2" step="0.1" />
            </div>
          </div>

          <div class="setting-row">
            <label class="setting-label">最大历史轮数</label>
            <div class="setting-controls">
              <input v-model.number="aiSettings.ai_max_history" type="number" min="1" step="1" />
            </div>
          </div>

          <div class="setting-row">
            <label class="setting-label">传给 AI 的最大数据行数</label>
            <div class="setting-controls">
              <input
                v-model.number="aiSettings.ai_context_max_rows"
                type="number"
                min="1"
                :disabled="aiContextRowsIsMax"
                placeholder="如 200"
              />
              <label class="setting-inline-toggle">
                <input type="checkbox" v-model="aiContextRowsIsMax" />
                <span>max</span>
              </label>
            </div>
          </div>

          <div class="setting-row">
            <label class="setting-label">压缩记忆</label>
            <div class="setting-controls">
              <label class="setting-inline-toggle">
                <input type="checkbox" v-model="aiSettings.ai_memory_enabled" />
                <span>启用</span>
              </label>
            </div>
          </div>

          <div class="setting-row" v-if="aiSettings.ai_memory_enabled">
            <label class="setting-label">压缩记忆最大字符数</label>
            <div class="setting-controls">
              <input v-model.number="aiSettings.ai_memory_max_chars" type="number" min="500" step="100" />
            </div>
          </div>

          <div class="setting-hint" v-if="aiLimitHint">{{ aiLimitHint }}</div>
          <div class="setting-hint warning" v-if="aiMaxTokensIsMax || aiContextRowsIsMax">
            已启用 max，可能显著增加响应时延与成本。
          </div>

          <button class="add-btn" @click="saveSettings">保存参数设置</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import AppIcon from './AppIcon.vue'
import { ref, computed, onMounted } from 'vue'
import {
  getAiProfiles, saveAiProfiles, setActiveProfile, getActiveProfile,
  getAiPrompts, saveAiPrompt, deleteAiPrompt, setActivePrompt,
  getAiConfig, getAiPersistConfig, setAiPersistConfig, getAiSettings, saveAiSettings,
} from '../api'

const emit = defineEmits(['close', 'config-changed'])

const tab = ref('profiles')
const profiles = ref([])
const prompts = ref([])
const activeProfileId = ref(null)
const activePromptId = ref('default')
const configModel = ref('')
const expandedPrompt = ref(null)
const persistToConfig = ref(true)
const editingProfileId = ref('')
const profileStatus = ref({ type: '', text: '' })
const aiSettings = ref({
  ai_max_tokens: 4096,
  ai_temperature: 0.1,
  ai_max_history: 12,
  ai_context_max_rows: 500,
  ai_memory_enabled: true,
  ai_memory_max_chars: 10000,
  model_token_limit: null,
  current_model: '',
})
const aiMaxTokensIsMax = ref(false)
const aiContextRowsIsMax = ref(false)

const newProfile = ref({ name: '', base_url: '', api_key: '', model: '' })
const newPrompt = ref({ name: '', content: '' })

const canSaveProfile = computed(() =>
  newProfile.value.name.trim() && newProfile.value.base_url.trim() && newProfile.value.model.trim()
)
const canAddPrompt = computed(() =>
  newPrompt.value.name.trim() && newPrompt.value.content.trim()
)

const aiLimitHint = computed(() => {
  const model = aiSettings.value.current_model || '当前模型'
  const limit = aiSettings.value.model_token_limit
  if (!limit) return `${model}: 未配置上限映射，max 将使用 provider 默认限制`
  return `${model}: max token 上限约 ${limit.toLocaleString()}`
})

async function loadData() {
  try {
    const [profData, promptData, cfgData, activeData, persistData, settingsData] = await Promise.all([
      getAiProfiles(),
      getAiPrompts(),
      getAiConfig(),
      getActiveProfile(),
      getAiPersistConfig(),
      getAiSettings(),
    ])
    profiles.value = profData.profiles || []
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
    aiMaxTokensIsMax.value = s.ai_max_tokens === 'max'
    aiContextRowsIsMax.value = s.ai_context_max_rows === 'max'
    aiSettings.value = {
      ai_max_tokens: s.ai_max_tokens === 'max' ? 4096 : (s.ai_max_tokens || 4096),
      ai_temperature: s.ai_temperature ?? 0.1,
      ai_max_history: s.ai_max_history || 12,
      ai_context_max_rows: s.ai_context_max_rows === 'max' ? 500 : (s.ai_context_max_rows || 500),
      ai_memory_enabled: s.ai_memory_enabled !== false,
      ai_memory_max_chars: s.ai_memory_max_chars || 10000,
      model_token_limit: s.model_token_limit ?? null,
      current_model: s.current_model || cfgData.model || '',
    }
  } catch (e) {
    console.error('Failed to load AI config:', e)
  }
}

async function saveSettings() {
  const payload = {
    ai_max_tokens: aiMaxTokensIsMax.value ? 'max' : Number(aiSettings.value.ai_max_tokens || 4096),
    ai_temperature: Number(aiSettings.value.ai_temperature ?? 0.1),
    ai_max_history: Number(aiSettings.value.ai_max_history || 12),
    ai_context_max_rows: aiContextRowsIsMax.value ? 'max' : Number(aiSettings.value.ai_context_max_rows || 500),
    ai_memory_enabled: !!aiSettings.value.ai_memory_enabled,
    ai_memory_max_chars: Number(aiSettings.value.ai_memory_max_chars || 10000),
  }
  try {
    const data = await saveAiSettings(payload)
    const s = data.settings || {}
    aiMaxTokensIsMax.value = s.ai_max_tokens === 'max'
    aiContextRowsIsMax.value = s.ai_context_max_rows === 'max'
    aiSettings.value = {
      ...aiSettings.value,
      ai_max_tokens: s.ai_max_tokens === 'max' ? aiSettings.value.ai_max_tokens : s.ai_max_tokens,
      ai_temperature: s.ai_temperature,
      ai_max_history: s.ai_max_history,
      ai_context_max_rows: s.ai_context_max_rows === 'max' ? aiSettings.value.ai_context_max_rows : s.ai_context_max_rows,
      ai_memory_enabled: s.ai_memory_enabled !== false,
      ai_memory_max_chars: s.ai_memory_max_chars || aiSettings.value.ai_memory_max_chars,
      model_token_limit: s.model_token_limit ?? aiSettings.value.model_token_limit,
      current_model: s.current_model || aiSettings.value.current_model,
    }
    emit('config-changed')
  } catch (e) {
    console.error('Failed to save AI settings:', e)
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
  try {
    await setActiveProfile(profile)
    activeProfileId.value = profile?.id || null
    emit('config-changed')
  } catch (e) {
    console.error('Failed to set profile:', e)
  }
}

function startEditProfile(profile) {
  profileStatus.value = { type: '', text: '' }
  editingProfileId.value = profile.id || ''
  newProfile.value = {
    id: profile.id || '',
    name: profile.name || '',
    base_url: profile.base_url || '',
    api_key: profile.api_key || '',
    model: profile.model || '',
  }
}

function cancelEditProfile() {
  editingProfileId.value = ''
  newProfile.value = { name: '', base_url: '', api_key: '', model: '' }
}

async function saveProfile() {
  if (!canSaveProfile.value) return
  const draft = { ...newProfile.value }
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
  profiles.value.splice(idx, 1)
  try {
    await saveAiProfiles(profiles.value)
    if (activeProfileId.value === removed.id) {
      await selectProfile(null)
    }
    if (editingProfileId.value === removed.id) {
      cancelEditProfile()
    }
  } catch (e) {
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
  } catch (e) {
    console.error('Failed to save prompt:', e)
  }
}

async function removePrompt(id) {
  try {
    await deleteAiPrompt(id)
    await loadData()
  } catch (e) {
    console.error('Failed to delete prompt:', e)
  }
}

onMounted(loadData)
</script>
