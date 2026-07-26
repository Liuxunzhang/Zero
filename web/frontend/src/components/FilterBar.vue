<template>
  <div class="filter-bar">
    <div class="filter-input-wrapper">
      <span class="filter-icon"><AppIcon name="search" :size="13" /></span>
      <input
        ref="inputRef"
        v-model="localFilter"
        class="filter-input"
        type="text"
        :placeholder="placeholder"
        @input="onInput"
        @focus="showSuggestions = true"
        @keydown.enter.prevent="handleEnter"
        @keydown.down.prevent="moveSuggestion(1)"
        @keydown.up.prevent="moveSuggestion(-1)"
        @keydown.tab.prevent="acceptSuggestion"
        @keydown.esc.prevent="clearFilter"
      />
      <div v-if="showSuggestions && suggestions.length" class="filter-suggest-menu">
        <button
          v-for="(item, idx) in suggestions"
          :key="`${item.type}-${item.value}`"
          class="filter-suggest-item"
          :class="{ active: idx === activeSuggestion }"
          type="button"
          @mousedown.prevent="applySuggestion(item)"
        >
          <span class="filter-suggest-type">{{ item.type }}</span>
          <span class="filter-suggest-value">{{ item.value }}</span>
          <span class="filter-suggest-desc">{{ item.desc }}</span>
        </button>
      </div>
    </div>

    <div class="filter-meta" v-if="store.imageLoaded">
      <span class="filter-result-count" v-if="store.hasData || store.currentPlugin">
        {{ store.visibleRows }} / {{ store.totalRows }} 行
      </span>
      <span class="filter-shortcut-hint">{{ filterHint }}</span>
    </div>

    <div class="filter-toolbar" v-if="store.imageLoaded">
      <label class="filter-page-size">
        <span>每页</span>
        <select
          class="filter-select"
          :value="store.pageSize"
          @change="updatePageSize"
        >
          <option v-for="size in pageSizeOptions" :key="size" :value="size">
            {{ size }}
          </option>
        </select>
      </label>

      <div class="filter-inline-actions">
        <button
          class="filter-action-btn"
          :disabled="!store.hasFilter"
          @click="clearFilter"
        >
          清空过滤
        </button>
        <button
          class="filter-action-btn"
          :disabled="!store.hasSort"
          @click="store.resetSort()"
        >
          重置排序
        </button>
        <button
          class="filter-action-btn"
          :disabled="!store.currentPlugin && !store.hasData"
          @click="store.fetchResults()"
        >
          刷新视图
        </button>
      </div>

      <div class="export-group" v-if="store.hasData">
        <button class="export-btn" @click="showExport = !showExport">
          <AppIcon name="download" :size="12" />
          导出
        </button>
        <div v-if="showExport" class="export-dropdown">
          <button class="export-option" @click="doExport('csv')">CSV</button>
          <button class="export-option" @click="doExport('json')">JSON</button>
          <button class="export-option" @click="doExport('txt')">TXT</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch, onMounted, onUnmounted } from 'vue'
import { useAppStore } from '../stores/app'
import AppIcon from './AppIcon.vue'

const store = useAppStore()
const pageSizeOptions = [50, 100, 200, 500, 1000]
const localFilter = ref('')
const showExport = ref(false)
const showSuggestions = ref(false)
const activeSuggestion = ref(0)
const inputRef = ref(null)
let debounceTimer = null

const operators = [
  { value: '-eq', desc: '等于' },
  { value: '-ne', desc: '不等于' },
  { value: '-gt', desc: '大于' },
  { value: '-lt', desc: '小于' },
  { value: '-ge', desc: '大于等于' },
  { value: '-le', desc: '小于等于' },
  { value: '-contain', desc: '包含' },
  { value: '-notcontain', desc: '不包含' },
  { value: '-match', desc: '正则匹配' },
  { value: '-startswith', desc: '前缀' },
  { value: '-endswith', desc: '后缀' },
]

const logicOperators = [
  { value: '&&', desc: '并且' },
  { value: '||', desc: '或者' },
]

const placeholder = '输入文本搜索，或 column -op value；按 Tab 补全'

const expressionState = computed(() => {
  const text = localFilter.value.trim()
  if (!text) return 'empty'
  if (!looksAdvanced(text)) return 'simple'
  return advancedExpressionLooksComplete(text) ? 'advanced' : 'incomplete'
})

const suggestions = computed(() => buildSuggestions(localFilter.value))

const filterHint = computed(() => {
  if (expressionState.value === 'incomplete') return '继续补全表达式 · Tab 选择建议'
  if (suggestions.value.length) return 'Tab 补全 · Enter 应用 · Esc 清空'
  return 'Enter 应用 · Esc 清空'
})

watch(() => store.filterText, (v) => {
  const next = v || ''
  if (localFilter.value !== next) {
    localFilter.value = next
  }
}, { immediate: true })

watch(suggestions, () => {
  activeSuggestion.value = 0
})

function onInput() {
  showSuggestions.value = true
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => {
    if (expressionState.value !== 'incomplete') {
      store.setFilter(localFilter.value.trim())
    }
  }, 300)
}

function applyNow() {
  clearTimeout(debounceTimer)
  if (expressionState.value !== 'incomplete') {
    store.setFilter(localFilter.value.trim())
    showSuggestions.value = false
  }
}

function handleEnter() {
  applyNow()
}

function clearFilter() {
  clearTimeout(debounceTimer)
  localFilter.value = ''
  showSuggestions.value = false
  store.setFilter('')
}

function updatePageSize(event) {
  store.setPageSize(Number(event.target.value))
}

function doExport(fmt) {
  store.doExport(fmt)
  showExport.value = false
}

function handleClickOutside(e) {
  if (showExport.value && !e.target.closest('.export-group')) {
    showExport.value = false
  }
  if (!e.target.closest('.filter-input-wrapper')) {
    showSuggestions.value = false
  }
}

function looksAdvanced(text) {
  return /\s-[a-zA-Z_]*\b/.test(text) || /&&|\|\|/.test(text)
}

function advancedExpressionLooksComplete(text) {
  const segments = text.split(/&&|\|\|/).map(s => s.trim()).filter(Boolean)
  if (!segments.length) return false
  return segments.every(segment => {
    const parts = splitTokens(segment)
    return parts.length >= 3 && /^-[a-zA-Z]+$/.test(parts[1]) && parts.slice(2).join(' ').trim().length > 0
  })
}

function splitTokens(text) {
  const matches = String(text || '').match(/"[^"\\]*(?:\\.[^"\\]*)*"|\S+/g)
  return matches || []
}

function quoteIfNeeded(value) {
  const text = String(value || '')
  if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(text)) return text
  return '"' + text.replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"'
}

function quoteValue(value) {
  const text = String(value ?? '')
  if (/^-?\d+(?:\.\d+)?$/.test(text)) return text
  if (/^[A-Za-z0-9_./:\\-]+$/.test(text)) return text
  return '"' + text.replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"'
}

function currentExpressionSegment(text) {
  const match = String(text || '').match(/(?:^|.*(?:&&|\|\|))\s*([^&|]*)$/)
  return match ? match[1] : text
}

function replaceCurrentSegment(nextSegment) {
  const text = localFilter.value
  const match = text.match(/^(.*(?:&&|\|\|)\s*)[^&|]*$/)
  localFilter.value = (match ? match[1] : '') + nextSegment
}

function currentColumnSampleValues(column) {
  const idx = store.columns.findIndex(col => String(col).toLowerCase() === String(column).toLowerCase())
  if (idx < 0) return []
  const seen = new Set()
  const values = []
  for (const row of store.rows || []) {
    const value = row?.[idx]
    const text = String(value ?? '').trim()
    if (!text || seen.has(text)) continue
    seen.add(text)
    values.push(text)
    if (values.length >= 8) break
  }
  return values
}

function buildSuggestions(text) {
  if (!store.imageLoaded) return []
  const segment = currentExpressionSegment(text).trimStart()
  const tokens = splitTokens(segment)
  const lowerSegment = segment.toLowerCase()
  const current = tokens[tokens.length - 1] || ''
  const currentLower = current.toLowerCase()

  if (!segment || tokens.length <= 1 && !lowerSegment.includes(' -')) {
    const query = currentLower.replace(/^"/, '')
    return (store.columns || [])
      .filter(col => String(col).toLowerCase().includes(query))
      .slice(0, 10)
      .map(col => ({ type: '列', value: col, desc: '按此列过滤', insert: `${quoteIfNeeded(col)} ` }))
  }

  if (
    (tokens.length === 1 && /\s$/.test(segment))
    || (tokens.length === 2 && current.startsWith('-') && !/\s$/.test(segment))
  ) {
    const query = current.startsWith('-') ? currentLower : ''
    return operators
      .filter(op => op.value.includes(query))
      .map(op => ({ type: '操作', value: op.value, desc: op.desc, insert: `${tokens[0]} ${op.value} ` }))
  }

  if (tokens.length >= 2 && tokens.length <= 3 && !/\s(&&|\|\|)\s*$/.test(text)) {
    const column = tokens[0].replace(/^"|"$/g, '')
    const typedValue = tokens.length === 3 ? tokens[2].replace(/^"|"$/g, '').toLowerCase() : ''
    const samples = currentColumnSampleValues(column)
      .filter(value => value.toLowerCase().includes(typedValue))
      .slice(0, 8)
    if (samples.length) {
      return samples.map(value => ({
        type: '值',
        value,
        desc: column,
        insert: `${tokens[0]} ${tokens[1]} ${quoteValue(value)} `,
      }))
    }
  }

  if (advancedExpressionLooksComplete(segment)) {
    return logicOperators.map(op => ({
      type: '逻辑',
      value: op.value,
      desc: op.desc,
      insert: `${segment} ${op.value} `,
    }))
  }

  return []
}

function applySuggestion(item) {
  if (!item) return
  replaceCurrentSegment(item.insert)
  showSuggestions.value = true
  inputRef.value?.focus()
  clearTimeout(debounceTimer)
  if (expressionState.value !== 'incomplete') {
    debounceTimer = setTimeout(() => store.setFilter(localFilter.value.trim()), 200)
  }
}

function acceptSuggestion() {
  if (suggestions.value.length) {
    applySuggestion(suggestions.value[activeSuggestion.value] || suggestions.value[0])
  }
}

function moveSuggestion(delta) {
  if (!suggestions.value.length) {
    showSuggestions.value = true
    return
  }
  showSuggestions.value = true
  const total = suggestions.value.length
  activeSuggestion.value = (activeSuggestion.value + delta + total) % total
}

onMounted(() => document.addEventListener('click', handleClickOutside))
onUnmounted(() => document.removeEventListener('click', handleClickOutside))
</script>
