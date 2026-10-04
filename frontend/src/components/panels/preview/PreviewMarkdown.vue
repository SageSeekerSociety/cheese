<script setup lang="ts">
// Markdown 的阅读视图：原文渲染出来，加上「读者指着一句话」这一件事。
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
// 知道，外面拿到的是一句话（原文 + 标题路径 + 前后各 32 字）。
import { ref } from 'vue'

import MarkdownView from '../../common/MarkdownView.vue'

import { type MarkdownQuote, quoteFromSelection } from './markdownQuote'

const props = defineProps<{
  /** 文件正文。不是 markdown 的那几种文件不会走到这里。 */
  source: string | null
}>()

const emit = defineEmits<{
  /** 读者选中了一段原文。选不成的（太短、没选、跨出正文）不会发这个事件。 */
  (e: 'quote', payload: MarkdownQuote): void
}>()

const view = ref<InstanceType<typeof MarkdownView> | null>(null)

function onSelect() {
  const quote = quoteFromSelection(view.value?.el ?? null, window.getSelection())
  if (quote) emit('quote', quote)
}
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
