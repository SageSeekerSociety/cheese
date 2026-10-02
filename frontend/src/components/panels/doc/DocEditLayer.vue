<script setup lang="ts">
// 贴在正文里某一段下面的那张卡：选中文字后点 AI 队友开出的输入框、改好之后的条子、问了
// 它之后的小卡，正看着的那一处改动，或者正看着的那一处修改建议。同一时刻只有一张，按这个
// 次序。
//
// 卡不进编辑器（ProseMirror 会撤掉别人加进可编辑区的东西），而是浮在正文上面、量着那
// 一段的位置摆；那一段下面用装饰留出一块同样高的空白，卡就不压住后面的字。卡和空白
// 都跟着正文的每一次变化重新量。
import type { Editor } from '@tiptap/core'
import type { DocAgentController } from '../../../composables/useDocAgent'
import type { DocReviewController } from '../../../composables/useDocReview'
import type { DocSuggestionsController } from '../../../composables/useDocSuggestions'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { editMarks, setEditMarks } from '../../../lib/docEditMarks'

import DocAgentAnswer from './DocAgentAnswer.vue'
import DocAgentBox from './DocAgentBox.vue'
import DocReviewCard from './DocReviewCard.vue'
import DocRewriteBar from './DocRewriteBar.vue'
import DocSuggestionCard from './DocSuggestionCard.vue'

const props = defineProps<{
  editor: Editor | null
  agentName: string
  editable: boolean
  rewrite: DocAgentController
  review: DocReviewController
  suggestions: DocSuggestionsController
  /** 修改建议的理由（建议 id → 理由）。 */
  suggestionReasons: Record<string, string>
  /** handle → 名字：回答里的点名读成名字。 */
  mentionNames: Record<string, string>
}>()
const emit = defineEmits<{ (e: 'open-thread', id: string): void }>()

const root = ref<HTMLElement | null>(null)
const card = ref<HTMLElement | null>(null)
const place = ref<{ top: number; left: number; width: number } | null>(null)
const tick = ref(0)

/** 卡贴着哪一段（正文里的一段范围），是哪一张。 */
const anchor = computed(() => {
  void tick.value
  const editor = props.editor
  if (!editor || editor.isDestroyed) return null
  const phase = props.rewrite.phase.value
  if (phase !== 'idle' && phase !== 'pending') {
    const target = editMarks(editor.state).target
    if (target)
      return {
        kind: 'rewrite' as const,
        from: target.from,
        to: target.to,
        width: phase === 'asking' || phase === 'waiting' || phase === 'answered' ? 460 : 0,
      }
  }
  const change = props.review.current.value
  if (change) return { kind: 'review' as const, from: change.from, to: change.to, width: 540 }
  const suggestion = props.suggestions.active.value
  if (suggestion) return { kind: 'suggestion' as const, from: suggestion.from, to: suggestion.to, width: 540 }
  return null
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
  // 卡不出正文那一栏：窄屏上贴着栏的两边，不贴着页面的边。
  const column = editor.view.dom.getBoundingClientRect()
  const min = Math.max(0, column.left - lr.left)
  const max = Math.min(lr.width, column.right - lr.left)
  const width = at.width ? Math.min(at.width, max - min) : 0
  const cardWidth = width || card.value?.offsetWidth || 0
  left = Math.max(min, Math.min(left, max - cardWidth))
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
watch(() => props.suggestions.current.value, schedule)
watch(() => props.review.current.value, schedule)
const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(schedule) : null
watch(card, (el, old) => {
  if (old) observer?.unobserve(old)
  if (el) observer?.observe(el)
})
window.addEventListener('resize', schedule)

// 在卡外面按下鼠标：卡收起来（改到一半的那一次不受影响）。
function onPointerDown(e: MouseEvent) {
  if (e.target instanceof Node && card.value?.contains(e.target)) return
  const phase = props.rewrite.phase.value
  if (phase === 'asking' || phase === 'done' || phase === 'undone' || phase === 'answered') props.rewrite.close()
  // 建议卡：点到别处就收起，点到另一处建议则换成那一处（正文的点击会接着说是哪一处）。
  else if (props.suggestions.current.value) props.suggestions.close()
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
      <DocReviewCard
        v-if="anchor.kind === 'review' && review.current.value"
        :index="review.live.value.indexOf(review.current.value)"
        :total="review.live.value.length"
        :busy="review.busy.value"
        :editable="editable"
        @restore="review.restore(review.current.value.index)"
      />
      <DocSuggestionCard
        v-else-if="anchor.kind === 'suggestion' && suggestions.active.value"
        :agent-name="agentName"
        :agent-handle="suggestions.active.value.author || null"
        :index="suggestions.index.value"
        :total="suggestions.list.value.length"
        :reason="suggestionReasons[suggestions.active.value.id] ?? null"
        :editable="editable"
        @accept="suggestions.decide(suggestions.active.value.id, true)"
        @reject="suggestions.decide(suggestions.active.value.id, false)"
      />
      <DocAgentBox
        v-else-if="rewrite.phase.value === 'asking'"
        :agent-name="agentName"
        :editable="rewrite.editable.value"
        @edit="rewrite.edit"
        @ask="rewrite.question"
        @cancel="rewrite.close"
      />
      <DocAgentAnswer
        v-else-if="rewrite.phase.value === 'waiting' || rewrite.phase.value === 'answered'"
        :agent-name="agentName"
        :waiting="rewrite.phase.value === 'waiting'"
        :answer="rewrite.answer.value"
        :posted="!!rewrite.threadId.value"
        :mention-names="mentionNames"
        @open-thread="rewrite.threadId.value && emit('open-thread', rewrite.threadId.value)"
        @close="rewrite.close"
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
