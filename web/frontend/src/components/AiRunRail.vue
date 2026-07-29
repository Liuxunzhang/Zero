<template>
  <section
    class="run-rail"
    :class="{ active: streaming, collapsed }"
    aria-label="智能体运行阶段"
  >
    <button
      v-if="collapsed"
      type="button"
      class="run-rail-compact"
      title="展开证据链"
      @click="$emit('update:collapsed', false)"
    >
      <span class="compact-label"><i :class="stateClass"></i>证据链</span>
      <span class="compact-line" aria-hidden="true">
        <i :style="{ width: `${progressPercent}%` }"></i>
      </span>
      <strong>{{ phaseLabel }}</strong>
      <span class="compact-metrics">{{ turnLabel }} 轮 · {{ toolLabel }} 工具</span>
      <AppIcon name="chevron-down" :size="12" />
    </button>
    <template v-else>
      <div class="run-rail-head">
      <div>
        <span class="run-rail-kicker">EVIDENCE RUN</span>
        <strong>{{ phaseLabel }}</strong>
      </div>
        <div class="run-rail-head-actions">
          <span class="run-rail-state" :class="stateClass">
            <i></i>{{ stateLabel }}
          </span>
          <button
            type="button"
            class="run-rail-toggle"
            title="将证据链收起为一条线"
            @click="$emit('update:collapsed', true)"
          ><AppIcon name="chevron-up" :size="12" /></button>
        </div>
      </div>
      <ol class="run-rail-stages">
        <li
          v-for="(stage, index) in stages"
          :key="stage.key"
          :class="{ complete: index < phaseIndex, current: index === phaseIndex }"
        >
          <span class="run-rail-node">{{ index + 1 }}</span>
          <span>{{ stage.label }}</span>
        </li>
      </ol>
      <div class="run-rail-metrics">
        <span><b>{{ contextLabel }}</b> 上下文</span>
        <span><b>{{ turnLabel }}</b> 轮次</span>
        <span><b>{{ toolLabel }}</b> 工具</span>
        <span v-if="continuations"><b>{{ continuations }}</b> 次续写</span>
      </div>
    </template>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  run: { type: Object, required: true },
  streaming: { type: Boolean, default: false },
  collapsed: { type: Boolean, default: false },
})

defineEmits(['update:collapsed'])

const stages = [
  { key: 'context', label: '装载证据' },
  { key: 'reasoning', label: '分析研判' },
  { key: 'tools', label: '工具取证' },
  { key: 'answer', label: '形成结论' },
]

const tools = computed(() => Object.values(props.run.tools || {}))
const runningTools = computed(() => tools.value.filter(tool => tool.status === 'running').length)
const completeTools = computed(() => tools.value.filter(tool => tool.status === 'complete').length)

const phaseIndex = computed(() => {
  if (!props.streaming) return props.run.runId ? 3 : 0
  if (runningTools.value) return 2
  if (props.run.continuations > 0 || props.run.text) return 3
  if (props.run.lastEventType === 'context' || props.run.lastEventType === 'compaction') return 0
  return 1
})

const phaseLabel = computed(() => {
  if (props.run.continuations > 0 && props.streaming) return '输出达到上限，正在无缝续写'
  if (runningTools.value) return `正在执行 ${runningTools.value} 个取证工具`
  if (props.streaming && props.run.text) return '正在整理证据与结论'
  if (props.streaming) return '正在分析当前证据'
  if (props.run.stopReason === 'length') return '输出未完整结束'
  if (props.run.status === 'failed') return '运行失败'
  if (props.run.runId) return '本轮证据链已完成'
  return '等待分析任务'
})

const stateLabel = computed(() => {
  if (props.streaming) return '运行中'
  if (props.run.stopReason === 'length') return '未完整'
  if (props.run.status === 'failed') return '失败'
  if (props.run.runId) return '已完成'
  return '待命'
})

const stateClass = computed(() => {
  if (props.streaming) return 'running'
  if (props.run.stopReason === 'length' || props.run.status === 'failed') return 'warning'
  return props.run.runId ? 'complete' : ''
})

const contextLabel = computed(() => {
  const context = props.run.context || {}
  const used = Number(context.estimated_tokens || context.usage?.input_tokens || 0)
  const window = Number(context.context_window || 0)
  if (!used) return '—'
  if (!window) return used.toLocaleString()
  return `${Math.round((used / window) * 100)}%`
})

const turnLabel = computed(() => {
  const budget = props.run.budget || {}
  return `${budget.turns_used || 0}/${budget.max_turns || 16}`
})

const toolLabel = computed(() => `${completeTools.value}/${tools.value.length || 0}`)
const continuations = computed(() => Number(props.run.continuations || 0))
const progressPercent = computed(() => Math.max(8, Math.round(((phaseIndex.value + 1) / stages.length) * 100)))
</script>

<style scoped>
.run-rail {
  --rail-accent: var(--accent, #42a5f5);
  padding: 11px 14px 10px;
  border-bottom: 1px solid var(--border-subtle, #1d2b42);
  background:
    linear-gradient(110deg, color-mix(in srgb, var(--rail-accent) 7%, transparent), transparent 45%),
    var(--bg-secondary);
}

.run-rail.active {
  --rail-accent: #39c6c8;
}

.run-rail.collapsed {
  height: 31px;
  padding: 0;
}

.run-rail-compact {
  width: 100%;
  height: 30px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 12px;
  border: 0;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  text-align: left;
}

.run-rail-compact:hover {
  background: color-mix(in srgb, var(--rail-accent) 6%, transparent);
}

.compact-label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex: none;
  color: var(--text-secondary);
  font: 650 9px/1 var(--font-mono, monospace);
}

.compact-label i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--text-muted);
}

.compact-label i.running { background: #39c6c8; }
.compact-label i.complete { background: var(--text-success, #46c780); }
.compact-label i.warning { background: var(--text-warning, #e5a84b); }

.compact-line {
  position: relative;
  width: 46px;
  height: 1px;
  flex: none;
  overflow: hidden;
  background: var(--border);
}

.compact-line i {
  position: absolute;
  inset: 0 auto 0 0;
  background: var(--rail-accent);
}

.run-rail-compact strong {
  min-width: 0;
  overflow: hidden;
  flex: 1;
  color: var(--text-secondary);
  font-size: 9px;
  font-weight: 550;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.compact-metrics {
  flex: none;
  font: 500 8px/1 var(--font-mono, monospace);
  white-space: nowrap;
}

.run-rail-head,
.run-rail-metrics,
.run-rail-stages {
  display: flex;
  align-items: center;
}

.run-rail-head {
  justify-content: space-between;
  gap: 12px;
}

.run-rail-head-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.run-rail-toggle {
  width: 23px;
  height: 23px;
  display: grid;
  place-items: center;
  border: 1px solid var(--border-subtle);
  border-radius: 4px;
  background: var(--bg-elevated);
  color: var(--text-muted);
  cursor: pointer;
}

.run-rail-toggle:hover {
  border-color: var(--rail-accent);
  color: var(--text-primary);
}

.run-rail-head > div {
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.run-rail-kicker {
  color: var(--rail-accent);
  font: 700 9px/1 var(--font-mono, monospace);
  letter-spacing: .14em;
}

.run-rail-head strong {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 11px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-rail-state {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex: none;
  color: var(--text-muted);
  font-size: 10px;
}

.run-rail-state i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.run-rail-state.running { color: #39c6c8; }
.run-rail-state.complete { color: var(--text-success, #46c780); }
.run-rail-state.warning { color: var(--text-warning, #e5a84b); }
.run-rail-state.running i { animation: rail-pulse 1.4s ease-in-out infinite; }

.run-rail-stages {
  position: relative;
  justify-content: space-between;
  margin: 10px 0 8px;
  padding: 0;
  list-style: none;
}

.run-rail-stages::before {
  position: absolute;
  top: 7px;
  left: 7px;
  right: 7px;
  height: 1px;
  background: var(--border);
  content: "";
}

.run-rail-stages li {
  position: relative;
  z-index: 1;
  display: grid;
  justify-items: center;
  gap: 4px;
  min-width: 54px;
  color: var(--text-muted);
  font-size: 9px;
}

.run-rail-node {
  display: grid;
  place-items: center;
  width: 15px;
  height: 15px;
  border: 1px solid var(--border);
  border-radius: 50%;
  background: var(--bg-secondary);
  color: var(--text-muted);
  font: 700 8px/1 var(--font-mono, monospace);
}

.run-rail-stages li.complete,
.run-rail-stages li.current {
  color: var(--text-secondary);
}

.run-rail-stages li.complete .run-rail-node {
  border-color: color-mix(in srgb, var(--rail-accent) 55%, var(--border));
  background: color-mix(in srgb, var(--rail-accent) 14%, var(--bg-secondary));
  color: var(--rail-accent);
}

.run-rail-stages li.current .run-rail-node {
  border-color: var(--rail-accent);
  background: var(--rail-accent);
  color: #061113;
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--rail-accent) 13%, transparent);
}

.run-rail-metrics {
  gap: 12px;
  color: var(--text-muted);
  font-size: 9px;
}

.run-rail-metrics span {
  white-space: nowrap;
}

.run-rail-metrics b {
  color: var(--text-secondary);
  font-family: var(--font-mono, monospace);
  font-weight: 600;
}

@keyframes rail-pulse {
  50% { opacity: .35; box-shadow: 0 0 0 4px color-mix(in srgb, currentColor 13%, transparent); }
}

@media (max-width: 420px) {
  .run-rail-kicker { display: none; }
  .run-rail-metrics { gap: 8px; overflow-x: auto; }
  .compact-line,
  .compact-metrics { display: none; }
}

@media (prefers-reduced-motion: reduce) {
  .run-rail-state.running i { animation: none; }
}
</style>
