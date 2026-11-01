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
/** 一行 = 一张 16:9 的缩略图（112×63）+ 下面 8px 的缝。和样式里那两个数是同一组数。 */
const rowHeight = 71
const thumbWidth = 112
const thumbHeight = 63
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
/** 焦点在轨道里时，方向键在缩略图之间走：上下左右都是相邻页，Home / End 是首末页。
 *
 *  这一条和阅读器那条是分工而不是重复：焦点落在哪，键就归哪。轨道里挪焦点等于换页，
 *  所以不必再让外面的处理函数翻一遍（`PreviewSlides.keyboard` 看到焦点在轨道里就退出）。 */
function keydown(event: KeyboardEvent) {
  const target = event.target
  if (!(target instanceof HTMLElement)) return
  const button = target.closest<HTMLElement>('.slide-rail__page')
  if (!button) return
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return
  const page = Number(button.dataset.slidePage)
  if (!Number.isFinite(page)) return
  const next = {
    ArrowDown: page + 1,
    ArrowRight: page + 1,
    ArrowUp: page - 1,
    ArrowLeft: page - 1,
    Home: 1,
    End: props.count,
  }[event.key]
  if (next === undefined) return
  event.preventDefault()
  event.stopPropagation()
  const wanted = Math.min(props.count, Math.max(1, next))
  if (wanted === props.current) {
    focusPage(wanted)
    return
  }
  emit('select', wanted)
  void nextTick(() => focusPage(wanted))
}
async function focusPage(page: number) {
  await nextTick()
  const el = rail.value?.querySelector<HTMLElement>(`[data-slide-page="${page}"]`)
  if (el) el.focus()
}
watch(
  [pages, () => props.revision],
  async () => {
    const mine = ++generation
    for (const host of hosts.values()) props.cancelPaint(host)
    await nextTick()
    if (mine !== generation) return
    for (const page of pages.value) {
      const host = hosts.get(page)
      if (host) void props.paint(page, host, { width: thumbWidth, height: thumbHeight })
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
  <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- the keys are handled for the buttons inside (focus is on a thumbnail, the handler moves between them); the nav itself takes no focus -->
  <nav ref="rail" class="slide-rail" :aria-label="t('slides.thumbnails')" @scroll="scroll" @keydown="keydown">
    <div :style="{ height: `${start * rowHeight}px` }" />
    <button
      v-for="page in pages"
      :key="page"
      type="button"
      class="slide-rail__page"
      :data-slide-page="page"
      :aria-label="t('slides.goPage', { page })"
      :aria-current="current === page ? 'page' : undefined"
      @click="emit('select', page)"
    >
      <span class="slide-rail__thumb">
        <span :ref="(el) => setHost(page, el)" class="slide-rail__image" />
        <span class="slide-rail__no" aria-hidden="true">{{ page }}</span>
      </span>
    </button>
    <div :style="{ height: `${Math.max(0, count - start - pages.length) * rowHeight}px` }" />
  </nav>
</template>

<style scoped>
.slide-rail {
  width: 132px;
  flex: none;
  min-height: 0;
  padding: 10px;
  overflow: auto;
  border-right: 1px solid var(--line);
  background: var(--surface);
}
/* 一行就是一张缩略图，下面留 8px 的缝（外面没有 gap：虚拟列表那两段占位高必须正好
   等于一行，多出一点就会越滚越偏）。 */
.slide-rail__page {
  display: block;
  width: 100%;
  height: 71px;
  padding: 0 0 8px;
  color: var(--muted);
}
.slide-rail__thumb {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  aspect-ratio: 16 / 9;
  overflow: hidden;
  border: 1px solid var(--line-2);
  border-radius: 6px;
  background: var(--canvas);
}
.slide-rail__page:hover .slide-rail__thumb {
  border-color: var(--accent);
}
.slide-rail__page[aria-current='page'] .slide-rail__thumb {
  outline: 2px solid var(--accent);
  outline-offset: -1px;
}
.slide-rail__page:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
  border-radius: 6px;
}
.slide-rail__image {
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}
.slide-rail__image :deep(canvas) {
  display: block;
}
/* 页码压在缩略图右下角：白底的一小片，幻灯片本身是什么颜色都读得出来。 */
.slide-rail__no {
  position: absolute;
  right: 3px;
  bottom: 2px;
  padding: 0 3px;
  border-radius: 3px;
  background: var(--surface);
  color: var(--muted);
  font-size: 10px;
  line-height: 14px;
  font-variant-numeric: tabular-nums;
}
</style>
