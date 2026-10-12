<script setup lang="ts">
import { computed } from 'vue'
const props = withDefaults(defineProps<{ gap?: number; align?: 'start' | 'center' | 'end' | 'stretch' }>(), {
  gap: 12,
  align: 'end',
})
const ALIGN = { start: 'flex-start', center: 'center', end: 'flex-end', stretch: 'stretch' }
const style = computed(() => ({ gap: `${props.gap}px`, alignItems: ALIGN[props.align] ?? 'flex-end' }))
</script>
<template>
  <div class="row" :style="style"><slot /></div>
</template>
<style>
*,
*::before,
*::after {
  box-sizing: border-box;
}
:host {
  display: block;
  margin: 8px 0;
}
.row {
  display: flex;
  flex-wrap: wrap;
  text-align: start;
}
</style>
