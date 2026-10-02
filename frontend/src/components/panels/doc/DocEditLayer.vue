<script setup lang="ts">
// 贴在正文里某一段下面的那张卡：「让{agent}改」的输入框和改好之后的条子。
//
// 卡不进编辑器（ProseMirror 会撤掉别人加进可编辑区的东西），而是浮在正文上面、量着那
// 一段的位置摆；那一段下面用装饰留出一块同样高的空白，卡就不压住后面的字。卡和空白
// 都跟着正文的每一次变化重新量。
import type { Editor } from '@tiptap/core'
import type { DocRewriteController } from '../../../composables/useDocRewrite'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { editMarks, setEditMarks } from '../../../lib/docEditMarks'

import DocRewriteBar from './DocRewriteBar.vue'
import DocRewriteBox from './DocRewriteBox.vue'

const props = defineProps<{
  editor: Editor | null
  agentName: string
  rewrite: DocRewriteController
}>()

const root = ref<HTMLElement | null>(null)
const card = ref<HTMLElement | null>(null)
const place = ref<{ top: number; left: number; width: number } | null>(null)
const tick = ref(0)

/** 卡贴着哪一段：正文里的一段范围。 */
const anchor = computed(() => {
  void tick.value
  const editor = props.editor
  if (!editor || editor.isDestroyed) return null
  const phase = props.rewrite.phase.value
  if (phase === 'idle' || phase === 'pending') return null
  const target = editMarks(editor.state).target
  return target ? { from: target.from, to: target.to, width: phase === 'asking' ? 460 : 0 } : null
})

let frame = 0
function schedule() {
  if (frame) return
  frame = requestAnimationFrame(() => {
    frame = 0
    tick.value++
    void nextTick(measure)
  })
}

function setGap(gap: { index: number; px: number } | null) {
  const editor = props.editor
  if (!editor || editor.isDestroyed) return
  const now = editMarks(editor.state).gap
  if (now === gap || (now && gap && now.index === gap.index && Math.abs(now.px - gap.px) < 2)) return
  editor.view.dispatch(setEditMarks(editor.state.tr, { gap }))
}

function measure() {
  const editor = props.editor
  const at = anchor.value
  const layer = root.value
  if (!editor || !at || !layer) {
    place.value = null
    setGap(null)
    return
  }
  const doc = editor.state.doc
  const inside = Math.max(0, Math.min(doc.content.size - 1, Math.max(at.from, at.to - 1)))
  const index = doc.resolve(inside).index(0)
  let blockPos = 0
  for (let i = 0; i < index; i++) blockPos += doc.child(i).nodeSize
  const block = editor.view.nodeDOM(blockPos) as HTMLElement | null
  if (!block?.getBoundingClientRect) {
    place.value = null
    return
  }
  const lr = layer.getBoundingClientRect()
  const br = block.getBoundingClientRect()
  let left = br.left - lr.left
  try {
    left = editor.view.coordsAtPos(at.from).left - lr.left - 12
  } catch {
    // 位置已经不在正文里了：贴着这一段的左边。
  }
  const width = at.width ? Math.min(at.width, lr.width) : 0
  const cardWidth = width || card.value?.offsetWidth || 0
  left = Math.max(0, Math.min(left, lr.width - cardWidth))
  place.value = { top: br.bottom - lr.top + 6, left, width }
  void nextTick(() => {
    const height = card.value?.offsetHeight ?? 0
    if (height) setGap({ index, px: height + 18 })
  })
}

// 量的东西：正文变了、卡换了、窗口变了、卡自己长高了。
let bound: Editor | null = null
function bind(editor: Editor | null) {
  bound?.off('transaction', schedule)
  bound = editor
  bound?.on('transaction', schedule)
}
watch(() => props.editor, bind, { immediate: true })
watch(() => props.rewrite.phase.value, schedule)
const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(schedule) : null
watch(card, (el, old) => {
  if (old) observer?.unobserve(old)
  if (el) observer?.observe(el)
})
window.addEventListener('resize', schedule)

// 在卡外面按下鼠标：输入框和条子都收起来（改到一半的那一次不受影响）。
function onPointerDown(e: MouseEvent) {
  const phase = props.rewrite.phase.value
  if (phase !== 'asking' && phase !== 'done' && phase !== 'undone') return
  if (e.target instanceof Node && card.value?.contains(e.target)) return
  props.rewrite.close()
}
document.addEventListener('mousedown', onPointerDown, true)

onBeforeUnmount(() => {
  bind(null)
  if (frame) cancelAnimationFrame(frame)
  observer?.disconnect()
  window.removeEventListener('resize', schedule)
  document.removeEventListener('mousedown', onPointerDown, true)
})
</script>

<template>
  <div ref="root" class="doc-edit-layer">
    <div
      v-if="place && anchor"
      ref="card"
      class="doc-edit-layer__card"
      :style="{ top: `${place.top}px`, left: `${place.left}px`, width: place.width ? `${place.width}px` : undefined }"
    >
      <DocRewriteBox
        v-if="rewrite.phase.value === 'asking'"
        :agent-name="agentName"
        @send="rewrite.send"
        @cancel="rewrite.close"
      />
      <DocRewriteBar
        v-else
        :agent-name="agentName"
        :undone="rewrite.phase.value === 'undone'"
        :busy="rewrite.busy.value"
        @undo="rewrite.undo"
        @redo="rewrite.redo"
        @again="rewrite.again"
      />
    </div>
  </div>
</template>

<style scoped>
.doc-edit-layer {
  position: absolute;
  inset: 0;
  z-index: 5;
  pointer-events: none;
}
.doc-edit-layer__card {
  position: absolute;
  max-width: 100%;
  pointer-events: auto;
}
</style>
