<template>
  <div class="statusbar">
    <div
      ref="messagesRef"
      class="statusbar-messages statusbar-messages-clickable"
      title="查看消息日志"
      @click="showLog = !showLog"
    >
      <AppIcon :name="showLog ? 'chevron-down' : 'chevron-up'" :size="11" class="statusbar-log-caret" />
      <span
        v-if="lastMessage"
        class="statusbar-last-message"
        :class="`msg-${lastMessage.severity}`"
      >
        <span style="opacity: 0.5">{{ lastMessage.ts }}</span>
        {{ lastMessage.text }}
      </span>
      <span v-else style="color: var(--text-muted)">就绪</span>
      <span v-if="errorCount" class="statusbar-error-badge" title="错误消息数">
        <AppIcon name="alert-triangle" :size="10" />
        {{ errorCount }}
      </span>

      <!-- Message log popover -->
      <div v-if="showLog" class="statusbar-log" @click.stop>
        <div class="statusbar-log-head">
          <span>消息日志（最近 {{ store.messages.length }} 条）</span>
          <div class="statusbar-log-head-actions">
            <button
              class="statusbar-log-expand"
              title="放大并打开完整日志"
              @click="openFullLog"
            >
              <AppIcon name="maximize-2" :size="11" />
              <span>完整日志</span>
            </button>
            <button class="statusbar-log-close" @click="showLog = false" title="关闭">
              <AppIcon name="x" :size="12" />
            </button>
          </div>
        </div>
        <div ref="logBodyRef" class="statusbar-log-body">
          <div
            v-for="(msg, i) in logMessages"
            :key="`${msg.ts}-${i}`"
            class="statusbar-log-row"
            :class="`msg-${msg.severity}`"
          >
            <span class="statusbar-log-ts">{{ msg.ts }}</span>
            <span class="statusbar-log-text">{{ msg.text }}</span>
          </div>
          <div v-if="!store.messages.length" class="statusbar-log-empty">暂无消息</div>
        </div>
      </div>
    </div>

    <div v-if="store.imageLoaded" class="statusbar-context">
      <span class="statusbar-chip" :class="{ active: store.hasFilter }">
        过滤 {{ store.hasFilter ? '开' : '关' }}
      </span>
      <span class="statusbar-chip" :class="{ active: store.hasSort }">
        排序 {{ store.hasSort ? '开' : '关' }}
      </span>
      <span class="statusbar-chip">
        每页 {{ store.pageSize }}
      </span>
      <span v-if="store.resultTruncated" class="statusbar-chip truncated" title="结果达到服务端行数上限">
        已截断
      </span>
    </div>

    <div v-if="store.pluginBusy" class="statusbar-running">
      <span class="statusbar-running-dot"></span>
      正在加载插件
      <strong>{{ runningPluginName }}</strong>
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
      class="page-btn page-btn-danger"
      :disabled="cancelling"
      @click="doCancel"
      title="取消执行"
    >{{ cancelling ? '停止中...' : '停止' }}</button>

    <button
      v-if="!store.pluginBusy && (store.currentPlugin || store.lastRunPayload?.plugin)"
      class="page-btn"
      @click="store.forceRerunCurrentPlugin()"
      title="忽略结果缓存，强制重新运行当前插件"
    >强制重跑</button>

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
        <button class="page-btn page-jump-btn" @click="goJumpPage">前往</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch, onMounted, onUnmounted } from 'vue'
import { useAppStore } from '../stores/app'
import AppIcon from './AppIcon.vue'
import { useEscClose } from '../composables/useEscClose'

const store = useAppStore()
const emit = defineEmits(['open-log'])
const jumpPage = ref('')
const showLog = ref(false)
const cancelling = ref(false)
const messagesRef = ref(null)
const logBodyRef = ref(null)

useEscClose(() => showLog.value, () => { showLog.value = false })

const lastMessage = computed(() => {
  const msgs = store.messages
  return msgs.length ? msgs[msgs.length - 1] : null
})

const logMessages = computed(() => store.messages)

const errorCount = computed(
  () => store.messages.filter((m) => m.severity === 'error').length,
)

const runningPluginName = computed(() => {
  const plugin = String(store.runningPlugin || store.currentPlugin || '').trim()
  if (!plugin) return 'unknown'
  return plugin.split('.').filter(Boolean).pop()?.toLowerCase() || plugin.toLowerCase()
})

watch(() => store.page, (p) => {
  jumpPage.value = String(p || 1)
}, { immediate: true })

watch(
  [showLog, () => store.messages.length],
  async ([visible]) => {
    if (!visible) return
    await nextTick()
    if (logBodyRef.value) {
      logBodyRef.value.scrollTop = logBodyRef.value.scrollHeight
    }
  },
)

function goJumpPage() {
  const target = Number(jumpPage.value)
  store.goToPage(target)
  jumpPage.value = String(store.page || 1)
}

function openFullLog() {
  showLog.value = false
  emit('open-log')
}

async function doCancel() {
  if (cancelling.value) return
  cancelling.value = true
  try {
    await store.cancelRunningPlugin()
  } finally {
    cancelling.value = false
  }
}

function onDocClick(e) {
  if (showLog.value && messagesRef.value && !messagesRef.value.contains(e.target)) {
    showLog.value = false
  }
}

onMounted(() => document.addEventListener('click', onDocClick))
onUnmounted(() => document.removeEventListener('click', onDocClick))
</script>
