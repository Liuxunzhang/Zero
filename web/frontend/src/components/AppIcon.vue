<template>
  <svg
    class="app-icon"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    v-html="markup"
  />
</template>

<script setup>
import { computed } from 'vue'
import { icons } from '../icons'

const props = defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 14 },
})

// v-html is safe here: the markup comes from the static local registry,
// never from user input.
const markup = computed(() => {
  const m = icons[props.name]
  if (!m && import.meta.env.DEV) console.warn(`[AppIcon] unknown icon: ${props.name}`)
  return m || ''
})
</script>
