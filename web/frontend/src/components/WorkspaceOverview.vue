<template>
  <div class="workspace-overview" :class="{ expanded: expanded }">
    <button
      class="workspace-summary-strip"
      type="button"
      title="点击展开/收起工作区详情"
      @click="expanded = !expanded"
    >
      <span class="workspace-summary-item strong">{{ engineLabel }}</span>
      <span class="workspace-summary-item" :class="{ ready: store.imageLoaded }">
        {{ imageName }}
      </span>
      <span class="workspace-summary-item">{{ store.currentPlugin || '未选择插件' }}</span>
      <span class="workspace-summary-item">
        {{ store.hasData ? `${store.visibleRows}/${store.totalRows} 行` : '暂无结果' }}
      </span>
      <span class="workspace-summary-toggle">{{ expanded ? '收起' : '详情' }}</span>
    </button>

    <div v-if="expanded" class="workspace-card-grid">
      <div
        v-for="card in overviewCards"
        :key="card.label"
        class="workspace-card"
        :class="{ ready: card.ready }"
      >
        <div class="workspace-card-label">{{ card.label }}</div>
        <div class="workspace-card-value">{{ card.value }}</div>
        <div class="workspace-card-meta">{{ card.meta }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const expanded = ref(false)

const engineLabel = computed(() => {
  const item = (store.availableEngines || []).find((entry) => entry.engine_id === store.selectedEngine)
  return item?.display_name || store.selectedEngine
})

const imageName = computed(() => {
  const raw = String(store.imagePath || '').trim()
  if (!raw) return '未加载镜像'
  const parts = raw.split(/[\\/]/).filter(Boolean)
  return parts[parts.length - 1] || raw
})

const overviewCards = computed(() => ([
  {
    label: '当前引擎',
    value: engineLabel.value,
    meta: `平台 ${String(store.osFamily || 'linux').toUpperCase()}`,
    ready: true,
  },
  {
    label: '镜像状态',
    value: imageName.value,
    meta: store.imageLoaded ? '镜像已就绪，可直接运行插件' : '尚未加载，先选择内存镜像',
    ready: store.imageLoaded,
  },
  {
    label: '插件工作区',
    value: store.currentPlugin || '等待选择插件',
    meta: `${store.categoryCount} 个分类 · ${store.pluginCount} 个插件`,
    ready: Boolean(store.currentPlugin),
  },
  {
    label: '结果视图',
    value: store.hasData ? `${store.visibleRows} / ${store.totalRows} 行` : '暂无结果',
    meta: `过滤 ${store.hasFilter ? 'ON' : 'OFF'} · 排序 ${store.hasSort ? 'ON' : 'OFF'} · 每页 ${store.pageSize}`,
    ready: store.hasData,
  },
]))
</script>

<style scoped>
.workspace-overview {
  padding: 6px 16px 0;
  flex-shrink: 0;
}

.workspace-summary-strip {
  width: 100%;
  min-height: 30px;
  display: grid;
  grid-template-columns: auto minmax(120px, 1.4fr) minmax(120px, 1.1fr) auto auto;
  align-items: center;
  gap: 10px;
  padding: 5px 10px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, var(--bg-secondary) 92%, transparent);
  color: var(--text-secondary);
  cursor: pointer;
  text-align: left;
}

.workspace-summary-strip:hover {
  border-color: color-mix(in srgb, var(--accent-dim) 38%, var(--border-subtle));
  background: color-mix(in srgb, var(--bg-hover) 72%, var(--bg-secondary));
}

.workspace-summary-item {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}

.workspace-summary-item.strong {
  color: var(--accent-bright);
  font-weight: 700;
}

.workspace-summary-item.ready {
  color: var(--text-success);
}

.workspace-summary-toggle {
  justify-self: end;
  color: var(--text-muted);
  font-size: 11px;
}

.workspace-card-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  padding-top: 8px;
}

.workspace-card {
  padding: 10px 12px;
  background: color-mix(in srgb, var(--bg-secondary) 90%, transparent);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  min-width: 0;
  transition: border-color var(--transition-fast), transform var(--transition-fast), box-shadow var(--transition-fast);
}

.workspace-card.ready {
  border-color: color-mix(in srgb, var(--accent-dim) 35%, var(--border-subtle));
  box-shadow: 0 12px 24px color-mix(in srgb, var(--accent-glow) 45%, transparent);
}

.workspace-card:hover {
  transform: translateY(-1px);
  border-color: color-mix(in srgb, var(--accent-dim) 45%, var(--border));
}

.workspace-card-label {
  color: var(--text-muted);
  font-size: 11px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  margin-bottom: 5px;
}

.workspace-card-value {
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workspace-card-meta {
  margin-top: 4px;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.5;
}

@media (max-width: 960px) {
  .workspace-summary-strip {
    grid-template-columns: 1fr auto;
  }

  .workspace-summary-item:nth-child(n + 3):not(.workspace-summary-toggle) {
    display: none;
  }

  .workspace-card-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .workspace-card-grid {
    grid-template-columns: 1fr;
  }
}
</style>
