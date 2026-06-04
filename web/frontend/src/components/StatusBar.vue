<template>
  <div class="statusbar">
    <div class="statusbar-messages">
      <span
        v-if="lastMessage"
        :class="`msg-${lastMessage.severity}`"
      >
        <span style="opacity: 0.5">{{ lastMessage.ts }}</span>
        {{ lastMessage.text }}
      </span>
      <span v-else style="color: var(--text-muted)">就绪</span>
    </div>

    <div v-if="store.imageLoaded" class="statusbar-context">
      <span class="statusbar-chip" :class="{ active: store.hasFilter }">
        过滤 {{ store.hasFilter ? 'ON' : 'OFF' }}
      </span>
      <span class="statusbar-chip" :class="{ active: store.hasSort }">
        排序 {{ store.hasSort ? 'ON' : 'OFF' }}
      </span>
      <span class="statusbar-chip">
        每页 {{ store.pageSize }}
      </span>
    </div>

    <div
      v-if="store.pluginBusy"
      class="progress-bar-track"
      :class="{ 'progress-indeterminate': store.progress <= 0 }"
    >
      <div
        class="progress-bar-fill"
        :style="{ width: store.progress > 0 ? store.progress + '%' : '0%' }"
      ></div>
    </div>

    <button
      v-if="store.pluginBusy"
      class="page-btn"
      @click="store.cancelRunningPlugin()"
      title="取消执行"
    >停止</button>

    <div v-if="store.hasData" class="statusbar-pagination">
      <button
        class="page-btn"
        :disabled="store.page <= 1"
        @click="store.goToPage(store.page - 1)"
      >上一页</button>
      <span class="page-info">
        第 {{ store.page }} / {{ store.totalPages }} 页 · 当前 {{ store.visibleRows }} 行 · 总计 {{ store.totalRows }} 行
      </span>
      <button
        class="page-btn"
        :disabled="store.page >= store.totalPages"
        @click="store.goToPage(store.page + 1)"
      >下一页</button>

      <div v-if="store.totalPages > 1" class="page-jump">
        <span class="page-jump-label">跳转</span>
        <input
          v-model="jumpPage"
          class="page-jump-input"
          type="number"
          min="1"
          :max="store.totalPages"
          @keydown.enter="goJumpPage"
        />
        <button class="page-btn page-jump-btn" @click="goJumpPage">GO</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const jumpPage = ref('')

const lastMessage = computed(() => {
  const msgs = store.messages
  return msgs.length ? msgs[msgs.length - 1] : null
})

watch(() => store.page, (p) => {
  jumpPage.value = String(p || 1)
}, { immediate: true })

function goJumpPage() {
  const target = Number(jumpPage.value)
  store.goToPage(target)
  jumpPage.value = String(store.page || 1)
}
</script>
