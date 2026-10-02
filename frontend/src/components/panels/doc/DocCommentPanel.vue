<script setup lang="ts">
import type { SendDocComment } from '../../../composables/useDocCommentDraft'
import type { Block } from '../../../cx_types'
import type { DocThreadActions, DocThreadState } from '../../../lib/docThreadTypes'

import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'

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
  aiOpened?: boolean
}>()
const emit = defineEmits<{
  (e: 'update:openId', id: string | null): void
  (e: 'locate-node', id: string): void
  (e: 'posted'): void
  (e: 'open-ai'): void
  (e: 'close-ai'): void
}>()

const WIDTH_KEY = 'cheese:docs:comments-width'
const root = ref<HTMLElement | null>(null)
const dockId = useId()
const tabs = ref<HTMLElement | null>(null)
const aside = ref<HTMLElement | null>(null)
const commentsRef = ref<InstanceType<typeof DocComments> | null>(null)
const opened = ref(false)
const activeTool = ref<'comments' | 'ai'>('comments')
const busy = ref(false)
const paneWidth = ref(0)
const preferred = ref(340)
const floating = ref(false)
try {
  const saved = Number(localStorage.getItem(WIDTH_KEY))
  if (Number.isFinite(saved) && saved > 0) preferred.value = saved
} catch {
  /* Width storage is optional. */
}
const canDock = computed(() => paneWidth.value >= 688)
const docked = computed(() => canDock.value && !floating.value)
const compact = computed(() => paneWidth.value <= 480)
const max = computed(() => Math.max(0, Math.min(560, paneWidth.value - 400)))
const min = computed(() => Math.min(280, max.value))
function clamp(value: number) {
  return Math.round(Math.max(min.value, Math.min(max.value, value)))
}
const width = computed(() =>
  docked.value ? clamp(preferred.value) : compact.value ? paneWidth.value : Math.min(340, paneWidth.value - 24)
)
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
  if (busy.value && activeTool.value === 'comments') return false
  context++
  finishResize(false)
  opened.value = false
  if (props.aiOpened) emit('close-ai')
  if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true })
  return true
}
function toggle() {
  if (opened.value && activeTool.value === 'comments') close()
  else void showComments()
}
async function showComments(inFloatingWindow?: boolean) {
  if (inFloatingWindow !== undefined) floating.value = inFloatingWindow
  activeTool.value = 'comments'
  return show()
}
async function showAi(inFloatingWindow?: boolean) {
  if (inFloatingWindow !== undefined) floating.value = inFloatingWindow
  activeTool.value = 'ai'
  return show()
}
function activateAi() {
  void showAi()
  if (!props.aiOpened) emit('open-ai')
}
function tabKey(event: KeyboardEvent) {
  if (event.isComposing || event.altKey || event.ctrlKey || event.metaKey) return
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  const next =
    event.key === 'Home' ? 'ai' : event.key === 'End' ? 'comments' : activeTool.value === 'ai' ? 'comments' : 'ai'
  if (next === 'ai') activateAi()
  else void showComments()
  void nextTick(() => tabs.value?.querySelector<HTMLElement>('[aria-selected="true"]')?.focus())
}
async function open(target: { anchorId: string | null; quote: string }) {
  if (await showComments(!!target.anchorId || !!target.quote)) commentsRef.value?.open(target)
}
async function locate(id: string) {
  if (await showComments(true)) commentsRef.value?.locate(id)
}
function switchSurface() {
  finishResize(false)
  floating.value = !floating.value
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
    activeTool.value = 'comments'
    floating.value = false
    returnFocus = null
    emit('update:openId', null)
  },
  { flush: 'sync' }
)
watch(
  () => props.aiOpened,
  (value) => {
    if (value) void showAi()
    else if (activeTool.value === 'ai') close()
  },
  { immediate: true }
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
watch(
  docked,
  (value) => {
    if (!value) finishResize(false)
  },
  { flush: 'sync' }
)
onBeforeUnmount(() => {
  disposed = true
  context++
  finishResize(false)
  observer?.disconnect()
  if (frame) cancelAnimationFrame(frame)
  window.removeEventListener('resize', measure)
})
defineExpose({ open, locate, toggle, close, showAi, opened, busy, activeTool })
</script>

<template>
  <div ref="root" class="doc-reading" :class="{ 'is-resizing': resizing }">
    <slot />
    <aside
      v-show="opened"
      ref="aside"
      class="doc-comment-panel"
      :class="{ 'doc-comment-panel--drawer': !docked, 'doc-comment-panel--compact': !docked && compact }"
      data-comments-panel
      :data-doc-tool="activeTool"
      :data-comments-drawer="!docked ? '' : undefined"
      :role="docked ? 'complementary' : 'dialog'"
      :aria-label="activeTool === 'ai' ? t('work.room.docAi.title') : t('work.room.comments.title')"
      tabindex="-1"
      :style="{ width: `${width}px` }"
      @keydown="onEscape"
    >
      <div
        v-if="docked"
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
        <div
          v-if="$slots.ai"
          ref="tabs"
          class="doc-tool-tabs"
          role="tablist"
          :aria-label="t('work.room.docTools.title')"
          @keydown="tabKey"
        >
          <button
            :id="`${dockId}-ai-tab`"
            type="button"
            role="tab"
            :aria-selected="activeTool === 'ai'"
            :aria-controls="`${dockId}-ai-content`"
            :tabindex="activeTool === 'ai' ? 0 : -1"
            @click="activateAi"
          >
            {{ t('work.room.docAi.title') }}
          </button>
          <button
            :id="`${dockId}-comments-tab`"
            type="button"
            role="tab"
            :aria-selected="activeTool === 'comments'"
            :aria-controls="`${dockId}-comments-content`"
            :tabindex="activeTool === 'comments' ? 0 : -1"
            @click="showComments()"
          >
            {{ t('work.room.comments.title') }} <span class="t-meta">{{ comments.length }}</span>
          </button>
        </div>
        <span v-else
          >{{ t('work.room.comments.title') }} <span class="t-meta">{{ comments.length }}</span></span
        >
        <div class="doc-comment-panel__window-actions">
          <button
            v-if="canDock"
            type="button"
            class="doc-comment-panel__close"
            :aria-label="t(docked ? 'work.room.docTools.float' : 'work.room.docTools.dock')"
            :title="t(docked ? 'work.room.docTools.float' : 'work.room.docTools.dock')"
            @click="switchSurface"
          >
            <v-icon size="18">{{ docked ? 'mdi-dock-window' : 'mdi-dock-right' }}</v-icon>
          </button>
          <button
            type="button"
            class="doc-comment-panel__close"
            :disabled="busy && activeTool === 'comments'"
            :aria-label="t('work.room.docTools.backToDocument')"
            :title="
              busy && activeTool === 'comments'
                ? t('work.room.comments.waitForSend')
                : t('work.room.docTools.backToDocument')
            "
            @click="close"
          >
            <v-icon size="18">mdi-close</v-icon>
          </button>
        </div>
      </header>
      <div
        v-show="activeTool === 'ai'"
        :id="`${dockId}-ai-content`"
        class="doc-tool-content"
        role="tabpanel"
        :aria-labelledby="`${dockId}-ai-tab`"
      >
        <slot name="ai" />
      </div>
      <div
        v-show="activeTool === 'comments'"
        :id="`${dockId}-comments-content`"
        class="doc-tool-content"
        :role="$slots.ai ? 'tabpanel' : undefined"
        :aria-labelledby="$slots.ai ? `${dockId}-comments-tab` : undefined"
      >
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
      </div>
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
  padding: 0;
  border-inline-start: 1px solid var(--line);
  background: var(--surface);
  outline: none;
}
.doc-comment-panel--drawer {
  position: absolute;
  inset: 12px 12px auto auto;
  height: min(640px, calc(100% - 24px));
  max-height: calc(100% - 24px);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--raised);
  z-index: 22;
  box-shadow: var(--shadow-2);
}
.doc-comment-panel--compact {
  inset: 0 0 auto;
  height: min(640px, 100%);
  max-height: 100%;
  border-radius: 0;
  border-inline: 0;
}
.doc-comment-panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  flex: 0 0 auto;
  min-height: 48px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--line);
}
.doc-tool-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.doc-tool-tabs button {
  padding: 8px;
  color: var(--muted);
  border-radius: var(--radius-sm);
}
.doc-tool-tabs button[aria-selected='true'] {
  color: var(--ink);
  background: var(--fill);
}
.doc-tool-tabs button:focus-visible,
.doc-comment-panel__close:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.doc-tool-content {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
  min-width: 0;
  overflow: hidden;
}
.doc-comment-panel__window-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 4px;
}
.doc-comment-panel__close {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: 0 0 auto;
  justify-content: center;
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
</style>
