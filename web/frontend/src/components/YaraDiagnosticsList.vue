<template>
  <div v-if="items.length" class="yd-list">
    <button
      v-for="(item, index) in items"
      :key="`${item.code}-${index}`"
      :class="item.severity || 'error'"
      @click="$emit('jump', item)"
    >
      <strong>{{ item.severity === 'success' ? '✓' : item.severity === 'warning' ? '!' : '×' }}</strong>
      <code>{{ item.code || item.severity }}</code>
      <span>{{ item.message }}</span>
      <small v-if="item.file">{{ item.file }}:{{ item.line || 1 }}:{{ item.column || 1 }}</small>
    </button>
  </div>
</template>

<script setup>
defineProps({ items: { type: Array, default: () => [] } })
defineEmits(['jump'])
</script>

<style scoped>
.yd-list{display:grid;gap:4px;padding:8px}.yd-list button{width:100%;display:grid;grid-template-columns:16px max-content 1fr max-content;align-items:center;gap:8px;padding:6px 8px;border:0;border-radius:5px;background:var(--bg-tertiary);color:var(--text-secondary);font-size:11px;text-align:left;cursor:pointer}.yd-list strong{color:var(--text-error)}.yd-list .warning strong{color:var(--text-warning)}.yd-list .success strong{color:var(--text-success)}.yd-list code,.yd-list small{color:var(--text-muted);font-size:10px}
</style>
