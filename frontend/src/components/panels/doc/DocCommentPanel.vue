<script setup lang="ts">
// 评论栏这一列：够宽时贴在正文右边，窄时盖在正文上面；宽度能拖。里面是 DocComments。
import type { SendDocComment } from '../../../composables/useDocCommentDraft'
import type { CommentSpot } from '../../../lib/docCommentSpots'
import type { DocThreadActions, DocThreadState, ThreadPlace } from '../../../lib/docThreadTypes'

import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'

import DocComments from './DocComments.vue'

import { t } from '@/i18n'
import { vRovingTabs } from '@/lib/rovingTabs'

// 「未解决 / 已解决」两格页签切换的就是下面那张批注列表。
const listId = `doc-comments-${useId()}`

const props = defineProps<{
  topicId: string | null
  author: string
  threadState: DocThreadState
  threadActions: DocThreadActions
  sendComment?: SendDocComment
  openId: string | null
  /** 这一串评的那几个字在正文里的哪。 */
  placeOf: (id: string) => ThreadPlace
  agentName: string
  mentionNames: Record<string, string>
  nameOf: (handle: string) => string
  /** handle 读成他挑过的头像地址；他没挑过、或不在名册上时给空串，画首字母。 */
  avatarOf?: (handle: string) => string
  writable: boolean
}>()
const emit = defineEmits<{
  (e: 'update:openId', id: string | null): void
  (e: 'locate', id: string): void
}>()

const filter = ref<'open' | 'resolved'>('open')
const counts = computed(() => ({
  open: props.threadState.threads.filter((thread) => thread.state === 'open').length,
  resolved: props.threadState.threads.filter((thread) => thread.state === 'resolved').length,
}))

const WIDTH_KEY = 'cheese:docs:comments-width'
const root = ref<HTMLElement | null>(null)
const aside = ref<HTMLElement | null>(null)
const commentsRef = ref<InstanceType<typeof DocComments> | null>(null)
const opened = ref(false)
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
  if (busy.value) return false
  context++
  finishResize(false)
  opened.value = false
  if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true })
  return true
}
function toggle() {
  if (opened.value) close()
  else void showComments()
}
async function showComments(inFloatingWindow?: boolean) {
  if (inFloatingWindow !== undefined) floating.value = inFloatingWindow
  return show()
}
async function open(spot: CommentSpot, prefill?: string) {
  filter.value = 'open'
  if (await showComments(!!spot.quote)) commentsRef.value?.open(spot, prefill)
}
/** 对整篇写评论。 */
function writeOnDocument() {
  void open({ quote: '', from: 0, to: 0, rel: null })
}
async function locate(id: string) {
  const thread = props.threadState.threads.find((item) => item.comment.id === id)
  if (thread) filter.value = thread.state
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
    floating.value = false
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
defineExpose({ open, locate, toggle, close, opened, busy })
</script>

<template>
  <div ref="root" class="doc-pane" :class="{ 'is-resizing': resizing }">
    <slot />
    <Transition name="doc-comment-panel">
      <aside
        v-show="opened"
        ref="aside"
        class="doc-comment-panel"
        :class="{ 'doc-comment-panel--drawer': !docked, 'doc-comment-panel--compact': !docked && compact }"
        data-comments-panel
        :data-comments-drawer="!docked ? '' : undefined"
        :role="docked ? 'complementary' : 'dialog'"
        :aria-label="t('work.room.comments.title')"
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
            v-roving-tabs
            class="doc-comment-panel__tabs"
            role="tablist"
            :aria-label="t('work.room.comments.filter')"
          >
            <button
              v-for="kind in ['open', 'resolved'] as const"
              :key="kind"
              type="button"
              role="tab"
              class="doc-comment-panel__tab"
              :aria-selected="filter === kind"
              :aria-controls="listId"
              @click="filter = kind"
            >
              {{ t(`work.room.comments.${kind}`) }}
              <span class="doc-comment-panel__count">{{ counts[kind] }}</span>
            </button>
          </div>
          <div class="doc-comment-panel__window-actions">
            <button
              v-if="writable"
              type="button"
              class="doc-comment-panel__close"
              :aria-label="t('work.room.comments.write')"
              :title="t('work.room.comments.write')"
              @click="writeOnDocument"
            >
              <v-icon size="18">mdi-plus</v-icon>
            </button>
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
              :disabled="busy"
              :aria-label="t('work.room.docTools.backToDocument')"
              :title="busy ? t('work.room.comments.waitForSend') : t('work.room.docTools.backToDocument')"
              @click="close"
            >
              <v-icon size="18">mdi-close</v-icon>
            </button>
          </div>
        </header>
        <div class="doc-tool-content">
          <DocComments
            :id="listId"
            ref="commentsRef"
            role="tabpanel"
            :aria-label="t(`work.room.comments.${filter}`)"
            :topic-id="topicId"
            :author="author"
            :send-comment="sendComment"
            :thread-state="threadState"
            :thread-actions="threadActions"
            :open-id="openId"
            :place-of="placeOf"
            :agent-name="agentName"
            :mention-names="mentionNames"
            :name-of="nameOf"
            :avatar-of="avatarOf"
            :writable="writable"
            :filter="filter"
            @update:open-id="emit('update:openId', $event)"
            @busy="busy = $event"
            @locate="emit('locate', $event)"
          />
        </div>
      </aside>
    </Transition>
  </div>
</template>

<style scoped>
.doc-pane {
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
  /* 卡片是白的一张张，底下这一列沉一档，和原型一样。 */
  background: var(--canvas);
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
  z-index: var(--z-panel-2);
  box-shadow: var(--shadow-2);
}
/* 评论栏出现、收起：侧栏的那一档时长（设计系统 §9.3），从它所在的那一边来、回那一边去；
   收起快一档。停靠时正文的宽度一下就让出来，栏本身淡进来。 */
.doc-comment-panel-enter-active {
  transition:
    opacity var(--dur-slow) var(--ease-out),
    transform var(--dur-slow) var(--ease-out);
}
.doc-comment-panel-leave-active {
  transition:
    opacity var(--dur-base) var(--ease-in),
    transform var(--dur-base) var(--ease-in);
}
.doc-comment-panel-enter-from,
.doc-comment-panel-leave-to {
  opacity: 0;
  transform: translateX(16px);
}
.doc-comment-panel--compact.doc-comment-panel-enter-from,
.doc-comment-panel--compact.doc-comment-panel-leave-to {
  transform: translateY(-8px);
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
.doc-comment-panel__close:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
.doc-tool-content {
  flex: 1 1 auto;
  min-height: 0;
  min-width: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
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
  z-index: var(--z-raised-2);
  cursor: col-resize;
  touch-action: none;
}
.doc-comment-panel__resize:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}
.doc-comment-panel__tabs {
  display: flex;
  gap: 2px;
}
.doc-comment-panel__tab {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 28px;
  padding: 0 10px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--muted);
  font: inherit;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background var(--dur-quick) var(--ease-standard);
}
.doc-comment-panel__tab:hover {
  background: var(--fill);
}
.doc-comment-panel__tab[aria-selected='true'] {
  background: var(--fill);
  color: var(--ink);
  font-weight: 600;
}
.doc-comment-panel__count {
  color: var(--faint);
  font-weight: 400;
}
.doc-comment-panel__tab:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
</style>
