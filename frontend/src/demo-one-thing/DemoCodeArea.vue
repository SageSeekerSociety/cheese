<script setup lang="ts">
// The code editing surface shared by 改代码 (DemoCodeEdit) and a 题目's editor
// (ProblemCenter): a line-number gutter and a plain textarea in the read-only
// view's type and rhythm.
//
// A plain textarea, not the product's Monaco editor: Monaco needs web workers
// and several MB, and the demo page runs as one file in a null-origin sandbox.
import { computed } from 'vue'

defineProps<{ label: string }>()
const text = defineModel<string>({ required: true })
const lines = computed(() => text.value.split('\n'))
</script>

<template>
  <div class="ot-carea">
    <div
      class="ot-carea__gutter"
      :style="{ width: `calc(${String(lines.length).length}ch + 20px)` }"
      aria-hidden="true"
    >
      <span v-for="(_, i) in lines" :key="i">{{ i + 1 }}</span>
    </div>
    <textarea
      v-model="text"
      class="ot-carea__text"
      :rows="lines.length"
      wrap="off"
      spellcheck="false"
      autocomplete="off"
      :aria-label="label"
    />
  </div>
</template>

<style scoped>
/* The same type and rhythm as the read-only view (ChangesDiff): 12px mono, 1.55, gutter, 12px gaps. */
.ot-carea {
  display: flex;
  flex: 1;
  align-items: flex-start;
  min-height: 0;
  padding: 6px 0;
  overflow: auto;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.55;
}

.ot-carea__gutter {
  display: flex;
  flex: none;
  flex-direction: column;
  color: var(--faint);
  text-align: right;
  font-variant-numeric: tabular-nums;
  user-select: none;
}

.ot-carea__text {
  flex: 1;
  min-width: 0;
  padding: 0 12px 0 0;
  /* The read-only view prints a diff marker column (a space) before each line: start where its text starts. */
  margin: 0 0 0 19px;
  overflow-x: auto;
  overflow-y: hidden;
  font: inherit;
  line-height: inherit;
  color: var(--text);
  white-space: pre;
  resize: none;
  background: none;
  border: 0;
  outline: none;
  caret-color: var(--ink);
}
</style>
