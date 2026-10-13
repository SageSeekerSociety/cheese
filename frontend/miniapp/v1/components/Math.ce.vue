<script setup lang="ts">
import { computed } from 'vue'
import katex from 'katex'
const props = defineProps<{ tex?: string; display?: boolean }>()
const html = computed(() =>
  katex.renderToString(props.tex ?? '', { displayMode: !!props.display, throwOnError: false })
)
</script>
<template>
  <!-- KaTeX output of the app's own formula; the app already runs any script it likes in its sandbox. -->
  <!-- eslint-disable-next-line vue/no-v-html -->
  <span v-html="html" />
</template>
