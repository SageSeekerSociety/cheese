<script setup lang="ts">
import type { SlidePaintOptions } from '@/composables/useSlidesPdf'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { t } from '@/i18n'

const props = defineProps<{
  count: number
  current: number
  revision: number
  paint: (page: number, host: HTMLElement, options: SlidePaintOptions) => Promise<void>
  cancelPaint: (host: HTMLElement) => void
}>()
const emit = defineEmits<{ select: [page: number] }>()
const rail = ref<HTMLElement | null>(null)
const start = ref(0)
const rowHeight = 112
const budget = 8
const pages = computed(() =>
  Array.from({ length: Math.min(budget, Math.max(0, props.count - start.value)) }, (_, i) => start.value + i + 1)
)
const hosts = new Map<number, HTMLElement>()
let generation = 0

function setHost(page: number, el: unknown) {
  const previous = hosts.get(page)
  if (previous && previous !== el) props.cancelPaint(previous)
  if (el instanceof HTMLElement) hosts.set(page, el)
  else hosts.delete(page)
}
function scroll() {
  start.value = Math.min(
    Math.max(0, props.count - budget),
    Math.max(0, Math.floor((rail.value?.scrollTop ?? 0) / rowHeight))
  )
}
watch(
  () => props.current,
  (page) => {
    const el = rail.value
    if (!el) return
    const top = (page - 1) * rowHeight
    if (top < el.scrollTop || top + rowHeight > el.scrollTop + el.clientHeight) {
      el.scrollTop = top
      scroll()
    }
  }
)
watch(
  [pages, () => props.revision],
  async () => {
    const mine = ++generation
    for (const host of hosts.values()) props.cancelPaint(host)
    await nextTick()
    if (mine !== generation) return
    for (const page of pages.value) {
      const host = hosts.get(page)
      if (host) void props.paint(page, host, { width: 112, height: 76 })
    }
  },
  { immediate: true, flush: 'post' }
)
onBeforeUnmount(() => {
  generation += 1
  for (const host of hosts.values()) props.cancelPaint(host)
})
</script>

<template>
  <nav ref="rail" class="slide-rail" :aria-label="t('slides.thumbnails')" @scroll="scroll">
    <div :style="{ height: `${start * rowHeight}px` }" />
    <button
      v-for="page in pages"
      :key="page"
      type="button"
      class="slide-rail__page"
      :aria-label="t('slides.goPage', { page })"
      :aria-current="current === page ? 'page' : undefined"
      @click="emit('select', page)"
    >
      <span class="slide-rail__image"><span :ref="(el) => setHost(page, el)" /></span>
      <span class="t-meta">{{ page }}</span>
    </button>
    <div :style="{ height: `${Math.max(0, count - start - pages.length) * rowHeight}px` }" />
  </nav>
</template>

<style scoped>
.slide-rail {
  width: 144px;
  flex: none;
  min-height: 0;
  overflow: auto;
  border-right: 1px solid var(--line);
  background: var(--canvas);
}
.slide-rail__page {
  width: 100%;
  height: 112px;
  padding: 8px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  color: var(--muted);
}
.slide-rail__page:hover {
  background: var(--fill);
}
.slide-rail__page[aria-current='page'] {
  color: var(--accent-ink);
  background: var(--fill-2);
}
.slide-rail__page:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}
.slide-rail__image {
  width: 120px;
  height: 80px;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  border: 1px solid var(--line);
  background: var(--surface);
}
.slide-rail__image :deep(canvas) {
  display: block;
}
</style>
