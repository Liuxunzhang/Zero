<template>
  <div class="filter-bar">
    <div class="filter-input-wrapper">
      <span class="filter-icon">F</span>
      <input
        ref="inputRef"
        v-model="localFilter"
        class="filter-input"
        type="text"
        :placeholder="placeholder"
        @input="onInput"
        @keydown.enter="applyNow"
        @keydown.esc.prevent="clearFilter"
      />
    </div>

    <div class="filter-meta" v-if="store.imageLoaded">
      <span class="filter-result-count" v-if="store.hasData || store.currentPlugin">
        {{ store.visibleRows }} / {{ store.totalRows }} 行
      </span>
      <span class="filter-shortcut-hint">Enter 应用 · Esc 清空</span>
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
import { ref, watch, onMounted, onUnmounted } from 'vue'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const pageSizeOptions = [50, 100, 200, 500, 1000]
const localFilter = ref('')
const showExport = ref(false)
const inputRef = ref(null)
let debounceTimer = null

const placeholder = 'Filter: 文本搜索 或 column -op value (如 pid -eq 123)'

watch(() => store.filterText, (v) => {
  const next = v || ''
  if (localFilter.value !== next) {
    localFilter.value = next
  }
}, { immediate: true })

function onInput() {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => {
    store.setFilter(localFilter.value)
  }, 300)
}

function applyNow() {
  clearTimeout(debounceTimer)
  store.setFilter(localFilter.value)
}

function clearFilter() {
  clearTimeout(debounceTimer)
  localFilter.value = ''
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
}

onMounted(() => document.addEventListener('click', handleClickOutside))
onUnmounted(() => document.removeEventListener('click', handleClickOutside))
</script>
