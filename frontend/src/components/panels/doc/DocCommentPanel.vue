<script setup lang="ts">
import type { SendDocComment } from '../../../composables/useDocCommentDraft'
import type { Block } from '../../../cx_types'
import type { DocThreadActions, DocThreadState } from '../../../lib/docThreadTypes'

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import DocComments from './DocComments.vue'

import { t } from '@/i18n'

const props = defineProps<{
  topicId: string | null
  author?: string
  threadState?: DocThreadState
  threadActions?: DocThreadActions
  sendComment?: SendDocComment
  comments: Block[]
  anchorNodes: Block[]
  openId: string | null
  quoteState?: (id: string) => 'unique' | 'missing' | 'ambiguous'
}>()
const emit = defineEmits<{
  (e: 'update:openId', id: string | null): void
  (e: 'locate-node', id: string): void
  (e: 'posted'): void
}>()

const WIDTH_KEY = 'cheese:docs:comments-width'
const root = ref<HTMLElement | null>(null)
const aside = ref<HTMLElement | null>(null)
const commentsRef = ref<InstanceType<typeof DocComments> | null>(null)
const opened = ref(false)
const busy = ref(false)
const paneWidth = ref(0)
const preferred = ref(340)
try {
  const saved = Number(localStorage.getItem(WIDTH_KEY))
  if (Number.isFinite(saved) && saved > 0) preferred.value = saved
} catch {
  /* Width storage is optional. */
}
const docked = computed(() => paneWidth.value >= 688)
const max = computed(() => Math.max(0, docked.value ? Math.min(560, paneWidth.value - 400) : paneWidth.value - 16))
const min = computed(() => Math.min(280, max.value))
function clamp(value: number) {
  return Math.round(Math.max(min.value, Math.min(max.value, value)))
}
const width = computed(() => clamp(preferred.value))
function commit(value: number) {
  preferred.value = clamp(value)
  try {
    localStorage.setItem(WIDTH_KEY, String(preferred.value))
  } catch {
    /* Keep it in memory. */
  }
}

let observer: ResizeObserver | null = null
let frame = 0
let disposed = false
let context = 0
function measure() {
  if (frame || disposed) return
  frame = requestAnimationFrame(() => {
    frame = 0
    if (!disposed) paneWidth.value = root.value?.getBoundingClientRect().width ?? 0
  })
}
onMounted(() => {
  measure()
  if (typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(measure)
    if (root.value) observer.observe(root.value)
  }
  window.addEventListener('resize', measure)
})
let returnFocus: HTMLElement | null = null
async function show() {
  const captured = context
  if (!opened.value) {
    returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    opened.value = true
    await nextTick()
    if (disposed || captured !== context || !opened.value) return false
    aside.value?.focus({ preventScroll: true })
  }
  return !disposed && captured === context && opened.value
}
function close() {
  if (busy.value) return false
  context++
  finishResize(false)
  opened.value = false
  if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true })
  return true
}
function toggle() {
  if (opened.value) close()
  else void show()
}
async function open(target: { anchorId: string | null; quote: string }) {
  if (await show()) commentsRef.value?.open(target)
}
async function locate(id: string) {
  if (await show()) commentsRef.value?.locate(id)
}
function onEscape(e: KeyboardEvent) {
  if (e.key !== 'Escape' || e.defaultPrevented || e.isComposing) return
  e.preventDefault()
  e.stopPropagation()
  close()
}
watch(
  () => [props.topicId, props.author],
  () => {
    context++
    finishResize(false)
    opened.value = false
    returnFocus = null
    emit('update:openId', null)
  },
  { flush: 'sync' }
)

type Drag = {
  id: number
  x: number
  width: number
  preferred: number
  element: HTMLElement
  cursor: string
  userSelect: string
}
let drag: Drag | null = null
const resizing = ref(false)
function startResize(e: PointerEvent) {
  if (e.button !== 0 || drag || !opened.value) return
  const element = e.currentTarget as HTMLElement
  e.preventDefault()
  try {
    element.setPointerCapture(e.pointerId)
  } catch {
    return
  }
  drag = {
    id: e.pointerId,
    x: e.clientX,
    width: width.value,
    preferred: preferred.value,
    element,
    cursor: document.body.style.cursor,
    userSelect: document.body.style.userSelect,
  }
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  resizing.value = true
}
function moveResize(e: PointerEvent) {
  if (drag?.id === e.pointerId) preferred.value = clamp(drag.width + drag.x - e.clientX)
}
function finishResize(save: boolean) {
  const previous = drag
  if (!previous) return
  drag = null
  resizing.value = false
  if (save) commit(width.value)
  else preferred.value = previous.preferred
  document.body.style.cursor = previous.cursor
  document.body.style.userSelect = previous.userSelect
  if (previous.element.hasPointerCapture(previous.id)) previous.element.releasePointerCapture(previous.id)
}
function endResize(e: PointerEvent, save: boolean) {
  if (drag?.id === e.pointerId) finishResize(save)
}
function resizeKey(e: KeyboardEvent) {
  if (e.metaKey || e.ctrlKey || e.altKey || e.isComposing || drag) return
  const step = e.shiftKey ? 32 : 8
  const next =
    e.key === 'ArrowLeft'
      ? width.value + step
      : e.key === 'ArrowRight'
        ? width.value - step
        : e.key === 'Home'
          ? min.value
          : e.key === 'End'
            ? max.value
            : e.key === 'Enter'
              ? width.value >= max.value
                ? min.value
                : max.value
              : null
  if (next === null) return
  e.preventDefault()
  commit(next)
}
onBeforeUnmount(() => {
  disposed = true
  context++
  finishResize(false)
  observer?.disconnect()
  if (frame) cancelAnimationFrame(frame)
  window.removeEventListener('resize', measure)
})
defineExpose({ open, locate, toggle, close, opened, busy })
</script>

<template>
  <div ref="root" class="doc-reading" :class="{ 'is-resizing': resizing }">
    <slot />
    <button
      v-if="opened && !docked"
      type="button"
      class="doc-comment-scrim"
      :aria-label="t('work.room.comments.closePanel')"
      :disabled="busy"
      :title="busy ? t('work.room.comments.waitForSend') : t('work.room.comments.closePanel')"
      @click="close"
    />
    <aside
      v-show="opened"
      ref="aside"
      class="doc-comment-panel"
      :class="{ 'doc-comment-panel--drawer': !docked }"
      data-comments-panel
      :data-comments-drawer="!docked ? '' : undefined"
      :role="docked ? 'complementary' : 'dialog'"
      :aria-label="t('work.room.comments.title')"
      tabindex="-1"
      :style="{ width: `${width}px` }"
      @keydown="onEscape"
    >
      <div
        class="doc-comment-panel__resize"
        role="separator"
        tabindex="0"
        aria-orientation="vertical"
        :aria-label="t('work.room.comments.resize')"
        :aria-valuemin="min"
        :aria-valuemax="max"
        :aria-valuenow="width"
        @pointerdown="startResize"
        @pointermove="moveResize"
        @pointerup="endResize($event, true)"
        @pointercancel="endResize($event, false)"
        @lostpointercapture="endResize($event, false)"
        @dblclick="commit(340)"
        @keydown="resizeKey"
      />
      <header class="doc-comment-panel__head">
        <span
          >{{ t('work.room.comments.title') }} <span class="t-meta">{{ comments.length }}</span></span
        >
        <button
          type="button"
          class="doc-comment-panel__close"
          :disabled="busy"
          :aria-label="t('work.room.comments.closePanel')"
          :title="busy ? t('work.room.comments.waitForSend') : t('work.room.comments.closePanel')"
          @click="close"
        >
          <v-icon size="18">mdi-close</v-icon>
        </button>
      </header>
      <DocComments
        ref="commentsRef"
        :topic-id="topicId"
        :author="author"
        :send-comment="sendComment"
        :thread-state="threadState"
        :thread-actions="threadActions"
        :comments="comments"
        :anchor-nodes="anchorNodes"
        :open-id="openId"
        :quote-state="quoteState"
        @update:open-id="emit('update:openId', $event)"
        @busy="busy = $event"
        @locate-node="emit('locate-node', $event)"
        @posted="emit('posted')"
      />
      <p v-if="busy" role="status" class="doc-comment-panel__status">{{ t('work.room.comments.waitForSend') }}</p>
    </aside>
  </div>
</template>

<style scoped>
.doc-reading {
  position: relative;
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.doc-comment-panel {
  box-sizing: border-box;
  position: relative;
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  max-width: 100%;
  padding: 12px;
  border-inline-start: 1px solid var(--line);
  background: var(--surface);
  outline: none;
}
.doc-comment-panel--drawer {
  position: absolute;
  inset: 0 0 0 auto;
  z-index: 22;
  box-shadow: var(--shadow-2);
}
.doc-comment-panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
}
.doc-comment-panel__close {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-sm);
  color: var(--muted);
}
.doc-comment-panel__close:hover:not(:disabled) {
  background: var(--fill);
}
.doc-comment-panel__close:disabled {
  opacity: 0.5;
  cursor: default;
}
.doc-comment-panel__resize {
  position: absolute;
  inset: 0 auto 0 -4px;
  width: 8px;
  z-index: 2;
  cursor: col-resize;
  touch-action: none;
}
.doc-comment-panel__resize:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}
.doc-comment-panel__status {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 12px;
}
.doc-comment-scrim {
  position: absolute;
  inset: 0;
  z-index: 21;
  background: color-mix(in srgb, var(--ink) 8%, transparent);
}
</style>
