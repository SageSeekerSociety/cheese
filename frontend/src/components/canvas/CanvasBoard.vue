<script setup lang="ts">
// 一块画布：格子摆在板上，格子之间连线（Vue Flow）。
//
// 一切都在板上就地完成：拖格子挪位置，拖格子边上的圆点连线，双击格子改字，双击空白处
// 加一个格子，双击连线改线上的字，选中后按删除键或点 × 删掉。改了什么就把一组改动交给
// `apply`（存到后端），存好后新的画布从 `board` 回来；没存成，板子按 `board` 回到原样。
//
// 没摆过位置的格子按连线自动排（lib/canvasLayout.ts），只是画出来，不存。
// 内嵌在文档里时，滚轮照常滚动文档，捏合缩放画布；全屏时滚轮缩放。
import '@vue-flow/core/dist/style.css'
import '@vue-flow/node-resizer/dist/style.css'

import type {
  Connection,
  Edge,
  EdgeChange,
  EdgeMouseEvent,
  Node,
  NodeChange,
  NodeDragEvent,
  VueFlowError,
} from '@vue-flow/core'
import type { CanvasBoard, CanvasCard, CanvasChange } from '@/types/canvas'

import { computed, getCurrentInstance, nextTick, ref, shallowRef, watch } from 'vue'
import { ConnectionMode, ErrorCode, Handle, MarkerType, Position, useVueFlow, VueFlow } from '@vue-flow/core'
import { NodeResizer } from '@vue-flow/node-resizer'

import CanvasCardNode from './CanvasCardNode.vue'

import { t } from '@/i18n'
import { layoutCards } from '@/lib/canvasLayout'

const props = withDefaults(
  defineProps<{
    board: CanvasBoard
    editable: boolean
    /** 占满整个屏幕：滚轮缩放，而不是滚动外面的文档。 */
    full?: boolean
    /** 存一组改动；回来的画布从 `board` 递进来。 */
    apply: (changes: CanvasChange[]) => Promise<unknown>
  }>(),
  { full: false }
)

const emit = defineEmits<{
  (e: 'open-task', taskId: string): void
}>()

const flowId = `canvas-${getCurrentInstance()?.uid ?? 0}`
const { fitView, zoomIn, zoomOut, screenToFlowCoordinate } = useVueFlow(flowId)

const nodes = shallowRef<Node[]>([])
const edges = shallowRef<Edge[]>([])
const editing = ref<string | null>(null)
const host = ref<HTMLElement | null>(null)

const byId = computed(() => new Map(props.board.cards.map((card) => [card.id, card])))

function draw() {
  const boxes = layoutCards(props.board.cards, props.board.edges)
  nodes.value = props.board.cards.map((card) => {
    const box = boxes.get(card.id)!
    return {
      id: card.id,
      type: 'card',
      position: { x: box.x, y: box.y },
      // Its own size when somebody set one; otherwise as tall as it says.
      width: box.width,
      ...(card.height ? { height: card.height } : {}),
      data: { card },
      draggable: props.editable && editing.value !== card.id,
    }
  })
  edges.value = props.board.edges.map((edge) => ({
    id: edge.id,
    source: edge.from,
    target: edge.to,
    label: edge.label || undefined,
    markerEnd: MarkerType.ArrowClosed,
    data: { label: edge.label },
  }))
}

watch(() => [props.board, props.editable, editing.value], draw, { immediate: true })

const number = (id: string) => byId.value.get(id)?.number ?? -1

async function send(changes: CanvasChange[]) {
  if (!changes.length) return
  try {
    await props.apply(changes)
  } catch {
    // Not saved: the board goes back to what was last stored.
    draw()
  }
}

// ---- Moving and resizing ---------------------------------------------------

function onDragStop({ nodes: moved }: NodeDragEvent) {
  void send(
    moved.map((node) => ({
      op: 'move',
      card: number(node.id),
      x: Math.round(node.position.x),
      y: Math.round(node.position.y),
    }))
  )
}

function onResized(card: CanvasCard, box: { x: number; y: number; width: number; height: number }) {
  void send([
    {
      op: 'move',
      card: card.number,
      x: Math.round(box.x),
      y: Math.round(box.y),
      width: Math.round(box.width),
      height: Math.round(box.height),
    },
  ])
}

// ---- Writing in a card -----------------------------------------------------

async function saveText(card: CanvasCard, content: string) {
  editing.value = null
  await send([{ op: 'update', card: card.number, content }])
}

/** A new card where the pointer is, or in the middle of what is in view; it opens for typing. */
async function addCard(at?: { x: number; y: number }) {
  if (!props.editable) return
  const rect = host.value?.getBoundingClientRect()
  const point = at ?? (rect ? { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 } : { x: 0, y: 0 })
  const spot = screenToFlowCoordinate(point)
  const known = new Set(props.board.cards.map((card) => card.id))
  try {
    await props.apply([{ op: 'add', content: '', x: Math.round(spot.x - 110), y: Math.round(spot.y - 28) }])
  } catch {
    return
  }
  await nextTick()
  const made = props.board.cards.find((card) => !known.has(card.id))
  if (made) editing.value = made.id
}

/** Enter on a focused card opens it for writing: the keyboard's double click. */
function onEnter(event: KeyboardEvent) {
  const node = (event.target as Element | null)?.closest?.('.vue-flow__node') as HTMLElement | null
  const id = node?.dataset.id
  if (!props.editable || !id || editing.value || event.target !== node) return
  event.preventDefault()
  editing.value = id
}

function onPaneDoubleClick(event: MouseEvent) {
  const target = event.target as Element | null
  if (!props.editable || !target?.closest('.vue-flow__pane') || target.closest('.vue-flow__node, .vue-flow__edge'))
    return
  void addCard({ x: event.clientX, y: event.clientY })
}

// ---- Lines -----------------------------------------------------------------

function onConnect(connection: Connection) {
  if (connection.source === connection.target) return
  void send([{ op: 'connect', from: number(connection.source), to: number(connection.target) }])
}

/** The line whose words are being written, and where its box sits on the board. */
const labelling = ref<{ edge: Edge; x: number; y: number; text: string } | null>(null)
const labelInput = ref<HTMLInputElement | null>(null)

async function onEdgeDoubleClick({ edge, event }: EdgeMouseEvent) {
  if (!props.editable || !host.value) return
  const rect = host.value.getBoundingClientRect()
  const pointer = event as MouseEvent
  labelling.value = {
    edge,
    x: pointer.clientX - rect.left,
    y: pointer.clientY - rect.top,
    text: (edge.data as { label?: string } | undefined)?.label ?? '',
  }
  await nextTick()
  labelInput.value?.focus()
  labelInput.value?.select()
}

function saveLabel() {
  const open = labelling.value
  if (!open) return
  labelling.value = null
  const was = (open.edge.data as { label?: string } | undefined)?.label ?? ''
  if (open.text.trim() === was) return
  void send([{ op: 'connect', from: number(open.edge.source), to: number(open.edge.target), label: open.text.trim() }])
}

function removeLabelled() {
  const open = labelling.value
  if (!open) return
  labelling.value = null
  void send([{ op: 'disconnect', from: number(open.edge.source), to: number(open.edge.target) }])
}

// ---- Deleting (the delete key, or a card's ×) ------------------------------

// The delete key takes the selected cards and lines, and with a card the lines
// joined to it; the board reports those as two lists. They are gathered and
// sent as one set of changes, without the lines that go with their card anyway.
const removing = { cards: new Set<string>(), lines: new Set<string>() }
let flush: ReturnType<typeof setTimeout> | null = null

function gather(kind: 'cards' | 'lines', ids: string[]) {
  if (!ids.length) return
  ids.forEach((id) => removing[kind].add(id))
  flush ??= setTimeout(() => {
    flush = null
    const cards = [...removing.cards]
    const lines = [...removing.lines]
      .map((id) => props.board.edges.find((edge) => edge.id === id))
      .filter((edge) => edge && !removing.cards.has(edge.from) && !removing.cards.has(edge.to))
    removing.cards.clear()
    removing.lines.clear()
    void send([
      ...cards.map((id): CanvasChange => ({ op: 'delete', card: number(id) })),
      ...lines.map((edge): CanvasChange => ({ op: 'disconnect', from: number(edge!.from), to: number(edge!.to) })),
    ])
  }, 0)
}

function onNodesChange(changes: NodeChange[]) {
  gather(
    'cards',
    changes.filter((change) => change.type === 'remove').map((change) => change.id)
  )
}

function onEdgesChange(changes: EdgeChange[]) {
  gather(
    'lines',
    changes.filter((change) => change.type === 'remove').map((change) => change.id)
  )
}

function removeCard(card: CanvasCard) {
  void send([{ op: 'delete', card: card.number }])
}

// ---- The view --------------------------------------------------------------

let fitted = false
function fitOnce() {
  if (fitted || !props.board.cards.length) return
  fitted = true
  void fitView({ padding: 0.2, maxZoom: 1 })
}
watch(
  () => props.board.cards.length,
  () => nextTick(fitOnce)
)

// Going full screen or back changes how much of the board fits: show all of it
// again, once the board has measured its new size (a frame later).
watch(
  () => props.full,
  () => setTimeout(() => void fitView({ padding: 0.2, maxZoom: 1 }), 60)
)

/** What the board cannot measure is not a fault where nothing has a size (a test, a closed panel). */
function onFlowError(error: VueFlowError) {
  if (error.code === ErrorCode.MISSING_VIEWPORT_DIMENSIONS || error.code === ErrorCode.MISSING_STYLES) return
  console.warn(error.message)
}

defineExpose({ fit: () => fitView({ padding: 0.2, maxZoom: 1 }), addCard })
</script>

<template>
  <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- a double click on the empty board is the pointer's shortcut; 添加格子 below is the keyboard's way, and Enter on a focused card opens it for writing -->
  <div
    ref="host"
    class="canvas-board"
    :class="{ 'canvas-board--full': full }"
    @dblclick="onPaneDoubleClick"
    @keydown.enter="onEnter"
  >
    <VueFlow
      :id="flowId"
      :nodes="nodes"
      :edges="edges"
      :min-zoom="0.2"
      :max-zoom="2"
      :nodes-draggable="editable"
      :nodes-connectable="editable"
      :elements-selectable="editable"
      :delete-key-code="editable ? ['Backspace', 'Delete'] : null"
      :zoom-on-double-click="false"
      :zoom-on-scroll="full"
      :prevent-scrolling="full"
      :connection-mode="ConnectionMode.Loose"
      :default-edge-options="{ type: 'default' }"
      @init="fitOnce"
      @error="onFlowError"
      @node-drag-stop="onDragStop"
      @connect="onConnect"
      @edge-double-click="onEdgeDoubleClick"
      @nodes-change="onNodesChange"
      @edges-change="onEdgesChange"
    >
      <template #node-card="node">
        <NodeResizer
          v-if="editable"
          :is-visible="node.selected && editing !== node.id"
          :min-width="120"
          :min-height="48"
          line-class-name="canvas-board__resize-line"
          handle-class-name="canvas-board__resize-handle"
          @resize-end="onResized(node.data.card, $event.params)"
        />
        <Handle type="target" :position="Position.Left" class="canvas-board__handle" :connectable="editable" />
        <Handle type="source" :position="Position.Right" class="canvas-board__handle" :connectable="editable" />
        <CanvasCardNode
          :card="node.data.card"
          :editable="editable"
          :selected="node.selected"
          :editing="editing === node.id"
          @edit="editing = node.id"
          @save="saveText(node.data.card, $event)"
          @cancel="editing = null"
          @remove="removeCard(node.data.card)"
          @open-task="emit('open-task', $event)"
        />
      </template>
    </VueFlow>

    <p v-if="!board.cards.length" class="canvas-board__empty">{{ t('work.room.doc.canvas.empty') }}</p>

    <div
      v-if="labelling"
      class="canvas-board__label nodrag"
      :style="{ left: `${labelling.x}px`, top: `${labelling.y}px` }"
    >
      <input
        ref="labelInput"
        v-model="labelling.text"
        autocomplete="off"
        class="canvas-board__label-input"
        :aria-label="t('work.room.doc.canvas.lineLabel')"
        maxlength="200"
        @keydown.enter.prevent="saveLabel"
        @keydown.esc.prevent="labelling = null"
        @blur="saveLabel"
      />
      <button type="button" class="canvas-board__tool" @mousedown.prevent @click="removeLabelled">
        {{ t('work.room.doc.canvas.removeLine') }}
      </button>
    </div>

    <div class="canvas-board__tools">
      <button
        v-if="editable"
        type="button"
        class="canvas-board__tool"
        :title="t('work.room.doc.canvas.addCard')"
        @click="addCard()"
      >
        <i class="mdi mdi-plus" aria-hidden="true" />
        {{ t('work.room.doc.canvas.addCard') }}
      </button>
      <button
        type="button"
        class="canvas-board__tool canvas-board__tool--icon"
        :aria-label="t('work.room.doc.canvas.zoomOut')"
        :title="t('work.room.doc.canvas.zoomOut')"
        @click="zoomOut()"
      >
        <i class="mdi mdi-minus" aria-hidden="true" />
      </button>
      <button
        type="button"
        class="canvas-board__tool canvas-board__tool--icon"
        :aria-label="t('work.room.doc.canvas.zoomIn')"
        :title="t('work.room.doc.canvas.zoomIn')"
        @click="zoomIn()"
      >
        <i class="mdi mdi-plus" aria-hidden="true" />
      </button>
      <button
        type="button"
        class="canvas-board__tool canvas-board__tool--icon"
        :aria-label="t('work.room.doc.canvas.fit')"
        :title="t('work.room.doc.canvas.fit')"
        @click="fitView({ padding: 0.2, maxZoom: 1 })"
      >
        <i class="mdi mdi-fit-to-screen-outline" aria-hidden="true" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.canvas-board {
  position: relative;
  width: 100%;
  height: 100%;
  background-color: var(--canvas);
  background-image: radial-gradient(var(--line-2) 1px, transparent 1px);
  background-size: 20px 20px;
}

.canvas-board__empty {
  position: absolute;
  inset: 50% 0 auto;
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--faint);
  text-align: center;
  transform: translateY(-50%);
  pointer-events: none;
}

.canvas-board__tools {
  position: absolute;
  right: 8px;
  bottom: 8px;
  z-index: var(--z-raised-5);
  display: flex;
  gap: 2px;
  padding: 2px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
}

.canvas-board__tool {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 28px;
  padding: 0 8px;
  border: 0;
  border-radius: var(--radius-sm);
  font: inherit;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  background: none;
  cursor: pointer;
}

.canvas-board__tool--icon {
  justify-content: center;
  width: 28px;
  padding: 0;
  font-size: 15px;
}

.canvas-board__tool:hover {
  color: var(--ink);
  background: var(--fill);
}

.canvas-board__label {
  position: absolute;
  z-index: var(--z-raised-6);
  display: flex;
  gap: 4px;
  padding: 4px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  background: var(--surface);
  box-shadow: var(--shadow-1);
  transform: translate(-50%, -50%);
}

.canvas-board__label-input {
  width: 180px;
  padding: 2px 6px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  outline: none;
  font: inherit;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--ink);
  background: var(--surface);
}

.canvas-board__label-input:focus {
  border-color: var(--muted);
}

/* Vue Flow's own parts, in the product's colours. */
.canvas-board :deep(.vue-flow__edge-path) {
  stroke: var(--faint);
  stroke-width: 1.25;
}

.canvas-board :deep(.vue-flow__edge.selected .vue-flow__edge-path),
.canvas-board :deep(.vue-flow__edge:hover .vue-flow__edge-path) {
  stroke: var(--muted);
}

.canvas-board :deep(.vue-flow__arrowhead polyline) {
  fill: var(--faint);
  stroke: var(--faint);
}

.canvas-board :deep(.vue-flow__edge-textbg) {
  fill: var(--canvas);
}

.canvas-board :deep(.vue-flow__edge-text) {
  font-size: 12px;
  fill: var(--muted);
}

.canvas-board :deep(.vue-flow__connection-path) {
  stroke: var(--muted);
}

.canvas-board :deep(.vue-flow__node) {
  border-radius: var(--radius-md);
}

/* The dots a line starts and ends at: there while the pointer is on the card. */
.canvas-board :deep(.canvas-board__handle) {
  width: 8px;
  height: 8px;
  border: 1.5px solid var(--faint);
  background: var(--surface);
  opacity: 0;
  transition: opacity var(--dur-quick) var(--ease-standard);
}

.canvas-board :deep(.vue-flow__node:hover .canvas-board__handle),
.canvas-board :deep(.vue-flow__node.selected .canvas-board__handle),
.canvas-board :deep(.vue-flow__node .canvas-board__handle.connecting) {
  opacity: 1;
}

.canvas-board :deep(.canvas-board__resize-line) {
  border-color: transparent;
}

.canvas-board :deep(.canvas-board__resize-handle) {
  width: 8px;
  height: 8px;
  border: 1.5px solid var(--muted);
  border-radius: var(--radius-sm);
  background: var(--surface);
}

.canvas-board :deep(.vue-flow__node.selected),
.canvas-board :deep(.vue-flow__node:focus-visible) {
  outline: none;
}
</style>
