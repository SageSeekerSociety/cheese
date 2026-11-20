<script setup lang="ts">
// 幻灯片下面那一条：这一页的演讲者备注。
//
// 钉在阅读区的底部，不跟着幻灯片滚：幻灯片按剩下的高度缩放，所以宽而矮的面板里讲稿也一直
// 在屏上（以前它排在幻灯片下面，幻灯片先占满高度，讲稿就被挤到屏幕外）。上沿可以拖高矮，
// 拖到很矮就是收起；高度由宿主记住，换一份稿子也沿用。
//
// 讲稿照原稿的样子读：段落、大纲级别、项目符号和编号、加粗斜体。颜色和字号不照搬——那是
// 备注页上的排版，读的时候用不上。
import type { SlideNote, SlideNoteParagraph } from '@/lib/slideNotes'

import { computed, ref } from 'vue'

import { t } from '@/i18n'
import { hasNoteText, NOTES_MIN_PX } from '@/lib/slideNotes'

const props = withDefaults(
  defineProps<{
    /** 这一页的备注；没有就是 undefined。 */
    note?: SlideNote
    /** 整份稿子有没有任何一页写了备注：没有时说「这份稿子没有写」，而不是「这一页没有」。 */
    anyNotes: boolean
    page: number
    /** 这一条的高度（像素），由宿主记着；0 是撑满宿主给的空间（讲者视图）。 */
    height: number
    /** 可拖到的最高高度：宿主按阅读区的高度算。 */
    maxHeight: number
    /** 讲稿的字号（像素），讲者视图里比面板里大。 */
    fontSize?: number
    /** 能拖高矮、能收起；讲者视图里两样都不要。 */
    resizable?: boolean
  }>(),
  { note: undefined, fontSize: 13, resizable: true }
)
const emit = defineEmits<{ resize: [height: number]; close: [] }>()

/** 拖到比这还矮就收起。 */
const COLLAPSE_PX = 48

const filled = computed(() => hasNoteText(props.note))
/** 每一段前面的记号：编号按同一级别连续的那几段往下数，中间断开就重新数。 */
const marks = computed(() => {
  const counters: number[] = []
  return (props.note?.paragraphs ?? []).map((paragraph: SlideNoteParagraph) => {
    const level = paragraph.level ?? 0
    counters.length = level + 1
    if (paragraph.bullet === 'number') {
      counters[level] = (counters[level] ?? 0) + 1
      return `${counters[level]}.`
    }
    counters[level] = 0
    return paragraph.bullet === 'dot' ? '•' : ''
  })
})

const dragging = ref(false)
let startY = 0
let startHeight = 0
function clamp(value: number) {
  return Math.round(Math.min(props.maxHeight, Math.max(NOTES_MIN_PX, value)))
}
function down(event: PointerEvent) {
  if (!props.resizable || event.button !== 0) return
  event.preventDefault()
  dragging.value = true
  startY = event.clientY
  startHeight = props.height
  ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
}
function move(event: PointerEvent) {
  if (!dragging.value) return
  const wanted = startHeight + (startY - event.clientY)
  if (wanted < COLLAPSE_PX) return
  emit('resize', clamp(wanted))
}
function up(event: PointerEvent) {
  if (!dragging.value) return
  dragging.value = false
  if (startHeight + (startY - event.clientY) < COLLAPSE_PX) emit('close')
}
function key(event: KeyboardEvent) {
  const step = event.shiftKey ? 64 : 16
  const next = { ArrowUp: props.height + step, ArrowDown: props.height - step }[event.key]
  if (next === undefined) return
  event.preventDefault()
  emit('resize', clamp(next))
}
</script>

<template>
  <section
    class="slide-notes"
    :class="{ 'is-dragging': dragging }"
    :style="{ ...(height > 0 ? { height: `${height}px` } : { flex: '1 1 auto' }), '--notes-size': `${fontSize}px` }"
    data-testid="slides-notes"
    data-wheel-own
    :aria-label="t('slides.notes')"
  >
    <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- a separator that takes focus; arrow keys resize it -->
    <div
      v-if="resizable"
      class="slide-notes__handle"
      role="separator"
      aria-orientation="horizontal"
      :aria-label="t('slides.notesResize')"
      :aria-valuenow="height"
      :aria-valuemin="NOTES_MIN_PX"
      :aria-valuemax="maxHeight"
      tabindex="0"
      @pointerdown="down"
      @pointermove="move"
      @pointerup="up"
      @pointercancel="up"
      @keydown="key"
    />
    <header class="slide-notes__head">
      <span class="slide-notes__title">{{ t('slides.notes') }}</span>
      <span class="t-meta">{{ t('slides.notesPage', { page }) }}</span>
      <span class="slide-notes__spacer" />
      <slot name="tools" />
      <button
        v-if="resizable"
        type="button"
        class="slide-notes__close"
        :aria-label="t('slides.notesHide')"
        :title="t('slides.notesHide')"
        @click="emit('close')"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7.4 8.6 12 13.2l4.6-4.6L18 10l-6 6-6-6z" /></svg>
      </button>
    </header>
    <div class="slide-notes__body" tabindex="0">
      <template v-if="filled">
        <p
          v-for="(paragraph, i) in note?.paragraphs ?? []"
          :key="i"
          class="slide-notes__para"
          :class="{ 'has-mark': !!marks[i] }"
          :style="{ '--level': paragraph.level ?? 0 }"
        >
          <span v-if="marks[i]" class="slide-notes__mark" aria-hidden="true">{{ marks[i] }}</span>
          <span v-for="(run, j) in paragraph.runs" :key="j" :class="{ 'is-bold': run.bold, 'is-italic': run.italic }">{{
            run.text
          }}</span>
        </p>
      </template>
      <p v-else class="slide-notes__empty t-meta">
        {{ t(anyNotes ? 'slides.notesEmpty' : 'slides.notesNone') }}
      </p>
    </div>
  </section>
</template>

<style scoped>
.slide-notes {
  position: relative;
  display: flex;
  flex: none;
  flex-direction: column;
  min-height: 0;
  border-top: 1px solid var(--line);
  background: var(--surface);
}
.slide-notes__handle {
  position: absolute;
  top: -4px;
  right: 0;
  left: 0;
  height: 8px;
  cursor: row-resize;
  touch-action: none;
  z-index: 1;
}
.slide-notes__handle::before {
  content: '';
  position: absolute;
  top: 3px;
  right: 0;
  left: 0;
  height: 2px;
  background: transparent;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
/* 平时就看得见的一小截把手（--faint 是图标、分隔线那一档的灰，两套主题都和底色拉得开）；
   悬停、拖动时整条线加深，告诉读者这一整条都能拖。 */
.slide-notes__handle::after {
  content: '';
  position: absolute;
  top: 2px;
  left: 50%;
  width: 36px;
  height: 4px;
  border-radius: 2px;
  background: var(--faint);
  transform: translateX(-50%);
}
.slide-notes__handle:hover::before,
.slide-notes.is-dragging .slide-notes__handle::before,
.slide-notes__handle:focus-visible::before {
  background: var(--muted);
}
.slide-notes__handle:hover::after,
.slide-notes.is-dragging .slide-notes__handle::after,
.slide-notes__handle:focus-visible::after {
  background: var(--text);
}
.slide-notes__handle:focus-visible {
  outline: none;
}
.slide-notes__head {
  display: flex;
  flex: none;
  align-items: center;
  gap: 8px;
  padding: 6px 8px 2px 16px;
}
.slide-notes__title {
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12, 16px);
  color: var(--muted);
}
.slide-notes__spacer {
  flex: 1;
}
.slide-notes__close {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: var(--radius-sm);
  color: var(--muted);
}
.slide-notes__close:hover {
  background: var(--fill-2);
}
.slide-notes__close:focus-visible {
  outline: 2px solid var(--accent);
}
.slide-notes__close svg {
  width: 18px;
  height: 18px;
  fill: currentColor;
}
.slide-notes__body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 2px 16px 12px;
  overscroll-behavior: contain;
}
.slide-notes__body:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}
.slide-notes__para {
  margin: 0 0 6px;
  padding-left: calc(var(--level) * 18px);
  font-size: var(--notes-size);
  line-height: 1.6;
  color: var(--text);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.slide-notes__para.has-mark {
  position: relative;
  padding-left: calc(var(--level) * 18px + 18px);
}
.slide-notes__mark {
  position: absolute;
  left: calc(var(--level) * 18px);
  color: var(--muted);
}
.is-bold {
  font-weight: 600;
}
.is-italic {
  font-style: italic;
}
.slide-notes__empty {
  margin: 0;
  color: var(--faint);
}
</style>
