<script setup lang="ts">
// Markdown 的阅读视图：原文渲染出来，加上「读者指着一句话」这一件事，以及把这一份
// 文件上已有的评论画在那几处。
//
// 为什么不交给 iframe：内容域按 artifact 自己的 mime 原样发字节，而 text/markdown
// 对浏览器来说不是网页——挂上去读者看到的是星号和竖线（这就是它一直以来的样子）。
// 按文档的读法读（common/MarkdownView）：块、图表、公式和文档里长得一样，CJK 的
// `**这句。**下一句` 在哪儿都不断行，链接也统一新开一页。
//
// 按文档读，不按聊天读：文件里的单个换行是软换行，中文写作者在 .md 里不会为了
// 断行敲回车。聊天那边反过来，那里的换行就是作者敲的那个换行。
//
// 指的位置由 `markdownQuote` 算：渲染成什么样和选中的是哪一段文字，只有这块 DOM
// 知道，外面拿到的是一句话（原文 + 标题路径 + 前后各 32 字）。反过来的那一步——
// 把存下的那一句话在今天的正文里找回来——在 `markdownAnchors` 里。
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

import MarkdownView from '../../common/MarkdownView.vue'

import { type AnchorQuote, clearMarks, locateQuote, wrapRange } from './markdownAnchors'
import { type MarkdownQuote, quoteFromSelection } from './markdownQuote'

const props = defineProps<{
  /** 文件正文。不是 markdown 的那几种文件不会走到这里。 */
  source: string | null
  /** 这一份文件上已有的评论指着的那几处（`text`/`heading`/前后文，形状见
   *  `markdownQuote`）。这一版正文里已经找不到的那一处就不画。 */
  anchors?: readonly { id: string; quote: AnchorQuote }[]
  /** 现在点开的是哪一条：那一条的标记画得显眼些。 */
  activeId?: string | null
}>()

const emit = defineEmits<{
  /** 读者选中了一段原文。选不成的（太短、没选、跨出正文）不会发这个事件。 */
  (e: 'quote', payload: MarkdownQuote): void
  /** 读者点了正文上的某一颗标记：把那一串评论打开。 */
  (e: 'anchor', id: string): void
}>()

const view = ref<InstanceType<typeof MarkdownView> | null>(null)

function onSelect() {
  const quote = quoteFromSelection(view.value?.el ?? null, window.getSelection())
  if (quote) emit('quote', quote)
}

// ---- 把已有的评论画到正文上 ----
//
// 正文是 `MarkdownView` 自己渲染的 DOM（读法还要等它加载），标记只能从外面包一层。
// 每次正文或被指的几处变了就整个重画：先拆掉上一轮包的壳，再按文字找位置包新的。
let observer: MutationObserver | null = null

function paint() {
  const root = view.value?.el ?? null
  if (!root) return
  // 自己包出来的 mark 也算 DOM 变化：画的时候先不听了，画完再接上，否则拆-画-拆没完。
  observer?.disconnect()
  try {
    clearMarks(root)
    for (const anchor of props.anchors ?? []) {
      const range = locateQuote(root, anchor.quote)
      if (!range) continue
      const mark = wrapRange(range, anchor.id, anchor.id === props.activeId)
      // 点它开那一串评论：和文档里点标记是同一条路，都由外面接。
      //
      // 用 `click` 不用 `mousedown`：标记盖住的那几个字仍然要能选中、能拖（读者可能正是
      // 想指出那一段）。`click` 只在下和上落在同一颗标记上时才来，从标记里开始拖选不会
      // 误开评论栏；`mousedown` 还得 `preventDefault` 挡掉选字，那等于把这几个字锁住了。
      mark?.addEventListener('click', () => emit('anchor', anchor.id))
    }
  } finally {
    observer?.observe(root, { childList: true, subtree: true })
  }
}

const repaint = () => void nextTick(paint)
watch([() => props.source, () => props.anchors, () => props.activeId], repaint, {
  immediate: true,
  deep: true,
})
// 读法是第一次画的时候才加载的：加载完它会把自己整块重画，上一轮包的壳跟着没了。
// 盯住正文本身的 DOM 变化，重画一次（画的时候不听自己的，见 `paint`）。
watch(
  () => view.value?.el ?? null,
  (root) => {
    observer?.disconnect()
    observer = null
    if (!root) return
    observer = new MutationObserver(() => {
      if (props.anchors?.length) repaint()
    })
    observer.observe(root, { childList: true, subtree: true })
    repaint()
  },
  { immediate: true }
)
onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
  clearMarks(view.value?.el ?? null)
})
</script>

<template>
  <MarkdownView
    ref="view"
    class="doc__md md-content"
    data-testid="markdown"
    :source="source ?? ''"
    @mouseup="onSelect"
  />
</template>
