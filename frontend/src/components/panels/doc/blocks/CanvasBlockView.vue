<script setup lang="ts">
// 文档里的一块画布（tiptap 的节点视图）：标题、一句说明，下面是画布本身。
//
// 文档原文里只有一行 `:::canvas <编号> 标题` 和一句说明；画布另存，经 `host`（文档那一
// 层递进来的读写，见 types/canvas.ts）读写。别人改了画布，文档的实时连接会说一声
// （lib/docCanvasSignals），这里重读。
//
// 刚插进来、刚粘贴进来的块带着一个记号（`pending`）：插它的这一页按记号新建或复制一块
// 画布，再把编号写回节点。记号只有插它的那一页认得，所以同时开着文档的别人不会再做一遍。
//
// 宽度跟着正文那一栏；高度默认 420，拖底边改，存在画布上，大家看到的一样高。全屏是把
// 同一块搬到整个屏幕上（Teleport），板子不重建，正看着的地方还在。
import type { NodeViewProps } from '@tiptap/vue-3'
import type { CanvasBoard as Board, CanvasChange, CanvasHost } from '@/types/canvas'

import { computed, getCurrentInstance, onBeforeUnmount, onMounted, provide, ref, watch } from 'vue'
import { NodeViewWrapper } from '@tiptap/vue-3'

import CanvasBoard from '@/components/canvas/CanvasBoard.vue'
import { t } from '@/i18n'
import { listenToCanvas } from '@/lib/docCanvasSignals'
import { promisedWork, settlePromise } from '@/lib/docSchema/canvas'

const props = defineProps<NodeViewProps>()

// NodeViewWrapper injects these two from the node view around it, with no
// default; mounted on its own (the preview site, a test) nobody provides them,
// so an empty pair is provided here, the way AttachmentImageView does.
const provided = (getCurrentInstance() as unknown as { provides?: Record<string | symbol, unknown> } | null)?.provides
if (!provided || !Object.prototype.hasOwnProperty.call(provided, 'onDragStart')) provide('onDragStart', undefined)
if (!provided || !Object.prototype.hasOwnProperty.call(provided, 'decorationClasses'))
  provide('decorationClasses', undefined)

const DEFAULT_HEIGHT = 420
const MIN_HEIGHT = 240
const MAX_HEIGHT = 1200

const host = computed(() => (props.extension.options as { host?: CanvasHost | null }).host ?? null)
const canvasId = computed(() => String(props.node.attrs.canvas ?? ''))
const title = computed(() => String(props.node.attrs.title ?? ''))
const about = computed(() => String(props.node.attrs.about ?? ''))
const editable = computed(() => props.editor.isEditable)

const board = ref<Board | null>(null)
const failed = ref(false)
const saveFailed = ref(false)
const full = ref(false)
const height = ref(DEFAULT_HEIGHT)

let stopListening: (() => void) | null = null
let alive = true
/** The bottom edge is being dragged: a reload does not undo the height under the pointer. */
let dragging = false

async function load(id: string) {
  const reader = host.value
  if (!reader || !id) return
  try {
    const next = await reader.load(id)
    if (!alive || id !== canvasId.value) return
    board.value = next
    failed.value = false
    if (!dragging) height.value = next.height ?? DEFAULT_HEIGHT
  } catch {
    if (alive && id === canvasId.value) failed.value = true
  }
}

/** Make or copy the canvas this page promised the block (see `pending`). */
async function keepPromise() {
  const mark = String(props.node.attrs.pending ?? '')
  const work = mark ? promisedWork(mark) : null
  const maker = host.value
  if (!work || !maker) return false
  settlePromise(mark)
  try {
    const id = work === 'copy' && canvasId.value ? await maker.copy(canvasId.value) : await maker.create()
    if (alive) props.updateAttributes({ canvas: id, pending: '' })
  } catch {
    if (alive) failed.value = true
  }
  return true
}

watch(
  canvasId,
  (id) => {
    stopListening?.()
    stopListening = id ? listenToCanvas(id, () => void load(id)) : null
    board.value = null
    if (id) void load(id)
  },
  { immediate: true }
)

onMounted(() => void keepPromise())
onBeforeUnmount(() => {
  alive = false
  stopListening?.()
  window.removeEventListener('keydown', onEscape)
})

async function apply(changes: CanvasChange[]) {
  const writer = host.value
  if (!writer || !canvasId.value) return
  try {
    board.value = await writer.change(canvasId.value, changes)
    saveFailed.value = false
  } catch (error) {
    saveFailed.value = true
    throw error
  }
}

async function makeNow() {
  const maker = host.value
  if (!maker) return
  try {
    props.updateAttributes({ canvas: await maker.create(), pending: '' })
  } catch {
    failed.value = true
  }
}

// ---- Title and the sentence under it ----------------------------------------

function setText(key: 'title' | 'about', event: Event) {
  const value = (event.target as HTMLInputElement).value.replace(/\s+/g, ' ').trim()
  if (value !== String(props.node.attrs[key] ?? '')) props.updateAttributes({ [key]: value })
}

// ---- Height ------------------------------------------------------------------

function startResize(event: PointerEvent) {
  if (!editable.value || !board.value?.writable) return
  event.preventDefault()
  dragging = true
  const startY = event.clientY
  const startHeight = height.value
  const move = (e: PointerEvent) => {
    height.value = Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, startHeight + e.clientY - startY))
  }
  const up = () => {
    dragging = false
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
    const writer = host.value
    if (writer && canvasId.value && height.value !== (board.value?.height ?? DEFAULT_HEIGHT)) {
      writer.setHeight(canvasId.value, Math.round(height.value)).then(
        (next) => (board.value = next),
        () => (saveFailed.value = true)
      )
    }
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}

// ---- Full screen -------------------------------------------------------------

function onEscape(event: KeyboardEvent) {
  if (event.key === 'Escape') setFull(false)
}

function setFull(on: boolean) {
  full.value = on
  if (on) window.addEventListener('keydown', onEscape)
  else window.removeEventListener('keydown', onEscape)
}

const canChange = computed(() => editable.value && !!board.value?.writable)
</script>

<template>
  <NodeViewWrapper class="doc-canvas" data-block="canvas" contenteditable="false">
    <Teleport to="body" :disabled="!full">
      <div class="doc-canvas__frame" :class="{ 'doc-canvas__frame--full': full }">
        <header class="doc-canvas__head">
          <div class="doc-canvas__words">
            <input
              v-if="editable"
              autocomplete="off"
              class="doc-canvas__title"
              :value="title"
              :placeholder="t('work.room.doc.canvas.untitled')"
              :aria-label="t('work.room.doc.canvas.title')"
              maxlength="200"
              @change="setText('title', $event)"
              @keydown.enter.prevent="($event.target as HTMLInputElement).blur()"
            />
            <span v-else class="doc-canvas__title">{{ title || t('work.room.doc.canvas.untitled') }}</span>
            <input
              v-if="editable"
              autocomplete="off"
              class="doc-canvas__about"
              :value="about"
              :aria-label="t('work.room.doc.canvas.about')"
              maxlength="300"
              @change="setText('about', $event)"
              @keydown.enter.prevent="($event.target as HTMLInputElement).blur()"
            />
            <span v-else-if="about" class="doc-canvas__about">{{ about }}</span>
          </div>
          <span v-if="saveFailed" class="doc-canvas__note" role="status">{{
            t('work.room.doc.canvas.saveFailed')
          }}</span>
          <button
            v-if="board"
            type="button"
            class="doc-canvas__button"
            :aria-label="full ? t('work.room.doc.canvas.exitFull') : t('work.room.doc.canvas.full')"
            :title="full ? t('work.room.doc.canvas.exitFull') : t('work.room.doc.canvas.full')"
            @click="setFull(!full)"
          >
            <i :class="['mdi', full ? 'mdi-fullscreen-exit' : 'mdi-fullscreen']" aria-hidden="true" />
          </button>
        </header>

        <div class="doc-canvas__stage" :style="full ? undefined : { height: `${height}px` }">
          <CanvasBoard
            v-if="board"
            :board="board"
            :editable="canChange"
            :full="full"
            :apply="apply"
            @open-task="host?.openTask($event)"
          />
          <div v-else-if="failed" class="doc-canvas__state">{{ t('work.room.doc.canvas.loadFailed') }}</div>
          <div v-else-if="!canvasId && !node.attrs.pending" class="doc-canvas__state">
            <span>{{ t('work.room.doc.canvas.notMade') }}</span>
            <button v-if="editable && host" type="button" class="doc-canvas__button doc-canvas__make" @click="makeNow">
              {{ t('work.room.doc.canvas.make') }}
            </button>
          </div>
          <div v-else-if="!host" class="doc-canvas__state">{{ t('work.room.doc.canvas.elsewhere') }}</div>
          <div v-else class="doc-canvas__state doc-canvas__state--loading" aria-busy="true" />
        </div>
        <div
          v-if="!full && canChange"
          class="doc-canvas__grip"
          role="separator"
          aria-orientation="horizontal"
          :aria-label="t('work.room.doc.canvas.height')"
          :title="t('work.room.doc.canvas.height')"
          @pointerdown="startResize"
        />
      </div>
    </Teleport>
  </NodeViewWrapper>
</template>

<style scoped>
.doc-canvas {
  margin: 12px 0;
}

.doc-canvas__frame {
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
}

.doc-canvas__frame--full {
  position: fixed;
  inset: 0;
  z-index: var(--z-overlay);
  display: flex;
  flex-direction: column;
  border: 0;
  border-radius: 0;
  padding-top: env(safe-area-inset-top);
}

.doc-canvas__head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 8px 8px 12px;
  border-bottom: 1px solid var(--line);
}

/* 只有全屏（`--full`，整层铺满窗口）那一档才要：这时头部左边就是标题输入框，桌面版
   macOS 里要让开三颗窗口按钮（style.css 的 --window-buttons-inset，别处恒为 0）。 */
.doc-canvas__frame--full .doc-canvas__head {
  padding-left: max(12px, var(--window-buttons-inset));
}

.doc-canvas__words {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
}

.doc-canvas__title,
.doc-canvas__about {
  min-width: 0;
  padding: 0;
  border: 0;
  outline: none;
  font: inherit;
  background: transparent;
}

.doc-canvas__title {
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  color: var(--ink);
}

.doc-canvas__about {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.doc-canvas__title::placeholder {
  color: var(--faint);
}

.doc-canvas__note {
  flex: 0 0 auto;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--danger-ink);
}

.doc-canvas__button {
  display: inline-grid;
  flex: 0 0 auto;
  place-items: center;
  min-width: 28px;
  height: 28px;
  padding: 0 6px;
  border: 0;
  border-radius: var(--radius-sm);
  font: inherit;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  background: none;
  cursor: pointer;
}

.doc-canvas__button .mdi {
  font-size: 18px;
}

.doc-canvas__button:hover {
  color: var(--ink);
  background: var(--fill);
}

.doc-canvas__stage {
  position: relative;
  flex: 1 1 auto;
  min-height: 0;
}

.doc-canvas__state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 100%;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--faint);
  background: var(--canvas);
}

.doc-canvas__make {
  color: var(--text);
  border: 1px solid var(--line-2);
}

.doc-canvas__grip {
  height: 8px;
  border-top: 1px solid var(--line);
  background: var(--surface);
  cursor: ns-resize;
  touch-action: none;
}

.doc-canvas__grip:hover {
  background: var(--fill);
}
</style>
