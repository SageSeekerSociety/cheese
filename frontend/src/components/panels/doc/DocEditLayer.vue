<script setup lang="ts">
// 贴在正文里某一段下面的那张卡：选中文字后点 AI 队友开出的输入框、改好之后的条子、问了
// 它之后的小卡，正看着的那一处改动，或者正看着的那一处修改建议。同一时刻只有一张，按这个
// 次序。
//
// 卡不进编辑器（ProseMirror 会撤掉别人加进可编辑区的东西），而是浮在正文上面：贴着那
// 段文字的最后一行往下摆，下面放不下就摆到第一行上面。它盖住后面的字，不把正文推开；
// 跟着正文的每一次变化重新量。
import type { Editor } from '@tiptap/core'
import type { DocAgentController } from '../../../composables/useDocAgent'
import type { DocReviewController } from '../../../composables/useDocReview'
import type { DocSuggestionsController } from '../../../composables/useDocSuggestions'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { editMarks } from '../../../lib/docEditMarks'

import DocAgentBox from './DocAgentBox.vue'
import DocAgentResult from './DocAgentResult.vue'
import DocReviewCard from './DocReviewCard.vue'
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

async function toComment() {
  const thread = await props.rewrite.toComment()
  if (thread) emit('open-thread', thread)
}

const root = ref<HTMLElement | null>(null)
const card = ref<HTMLElement | null>(null)
const inner = ref<HTMLElement | null>(null)
/** 卡里的东西有多高：外面那层跟着它慢慢变高变矮，换内容时不跳。 */
const height = ref<number | null>(null)
const place = ref<{ top: number; left: number; width: number } | null>(null)
const tick = ref(0)
/** 卡和那段文字之间留的空。 */
const GAP = 8

/** 卡贴着哪一段（正文里的一段范围），是哪一张。 */
const anchor = computed(() => {
  void tick.value
  const editor = props.editor
  if (!editor || editor.isDestroyed) return null
  const phase = props.rewrite.phase.value
  if (phase !== 'idle') {
    const target = editMarks(editor.state).target
    if (target) return { kind: 'rewrite' as const, from: target.from, to: target.to, width: 420 }
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

/** 往上找第一个会滚动的容器：卡要待在它看得见的那一块里。 */
function scroller(el: HTMLElement | null): HTMLElement | null {
  for (let at = el?.parentElement ?? null; at; at = at.parentElement) {
    if (/(auto|scroll)/.test(getComputedStyle(at).overflowY)) return at
  }
  return null
}

/** 正文里一个位置在屏幕上的哪儿；量不出字的位置时（位置已经不在正文里了）退到它所在的那一块。 */
function coordsAt(editor: Editor, pos: number, side: -1 | 1): { top: number; left: number; bottom: number } | null {
  try {
    return editor.view.coordsAtPos(pos, side)
  } catch {
    const { node } = editor.view.domAtPos(pos)
    const el = node instanceof Element ? node : node.parentElement
    return el?.getBoundingClientRect() ?? null
  }
}

function measure() {
  const editor = props.editor
  const at = anchor.value
  const layer = root.value
  if (!editor || !at || !layer) {
    place.value = null
    return
  }
  const size = editor.state.doc.content.size
  const from = Math.max(0, Math.min(size, at.from))
  const to = Math.max(from, Math.min(size, at.to))
  const start = coordsAt(editor, from, 1)
  const end = coordsAt(editor, to, -1)
  if (!start || !end) {
    place.value = null
    return
  }
  const lr = layer.getBoundingClientRect()
  // 卡不出正文那一栏：窄屏上贴着栏的两边，不贴着页面的边。
  const column = editor.view.dom.getBoundingClientRect()
  const min = Math.max(0, column.left - lr.left)
  const max = Math.min(lr.width, column.right - lr.left)
  const width = at.width ? Math.min(at.width, max - min) : 0
  const cardWidth = width || card.value?.offsetWidth || 0
  const left = Math.max(min, Math.min(start.left - lr.left - 12, max - cardWidth))
  // 按卡最后有多高来算放上面还是下面，不按长到一半的高度，免得来回翻。
  const tall = inner.value?.offsetHeight ?? 0
  const view = scroller(layer)?.getBoundingClientRect()
  const below = end.bottom + GAP
  const above = start.top - GAP - tall
  const flip = !!view && tall > 0 && below + tall > view.bottom && above >= view.top
  place.value = { top: (flip ? above : below) - lr.top, left, width }
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
const observer =
  typeof ResizeObserver !== 'undefined'
    ? new ResizeObserver(() => {
        height.value = inner.value?.offsetHeight ?? null
        schedule()
      })
    : null
watch(inner, (el, old) => {
  if (old) observer?.unobserve(old)
  // 新开的一张卡从它自己的高度起，不从上一张收起前的高度长过来。
  height.value = null
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
    <Transition name="doc-edit-layer">
      <div
        v-if="place && anchor"
        ref="card"
        class="doc-edit-layer__card"
        :style="{ top: `${place.top}px`, left: `${place.left}px`, width: place.width ? `${place.width}px` : undefined }"
      >
        <div class="doc-edit-layer__clip" :style="{ height: height ? `${height}px` : undefined }">
          <div ref="inner" class="doc-edit-layer__inner">
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
              scope="selection"
              :context="rewrite.context.value"
              @run="rewrite.run"
              @say="rewrite.say"
              @cancel="rewrite.close"
            />
            <DocAgentResult
              v-else
              :agent-name="agentName"
              :phase="rewrite.phase.value"
              :kind="rewrite.kind.value"
              :answer="rewrite.answer.value"
              :changed="rewrite.edits.value.length"
              :busy="rewrite.busy.value"
              commentable
              :mention-names="mentionNames"
              @stop="rewrite.stop"
              @undo="rewrite.undo"
              @redo="rewrite.redo"
              @say="rewrite.say"
              @comment="toComment"
              @close="rewrite.close"
            />
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.doc-edit-layer {
  position: absolute;
  inset: 0;
  z-index: var(--z-raised-5);
  pointer-events: none;
}
.doc-edit-layer__card {
  position: absolute;
  max-width: 100%;
}
/* 剪掉长到一半的部分，但给投影留出地方：剪的那层四边各多出 24px，再用负的外边距收回来。 */
.doc-edit-layer__clip {
  box-sizing: content-box;
  margin: -24px;
  padding: 24px;
  overflow: hidden;
  transition: height 200ms cubic-bezier(0.2, 0, 0, 1);
}
.doc-edit-layer__inner {
  pointer-events: auto;
}
.doc-edit-layer-enter-active {
  transition:
    opacity 140ms ease-out,
    transform 160ms cubic-bezier(0.2, 0, 0, 1);
}
.doc-edit-layer-leave-active {
  transition: opacity 100ms ease-in;
}
.doc-edit-layer-enter-from {
  opacity: 0;
  transform: translateY(-4px) scale(0.98);
}
.doc-edit-layer-leave-to {
  opacity: 0;
}
@media (prefers-reduced-motion: reduce) {
  .doc-edit-layer__clip,
  .doc-edit-layer-enter-active,
  .doc-edit-layer-leave-active {
    transition: none;
  }
}
</style>
