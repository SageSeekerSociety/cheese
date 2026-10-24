<script setup lang="ts">
// 一段 Markdown 的阅读视图：消息、文件、周报、文档的旧版本。
//
// 它和文档用同一套解析和同一套块（panels/doc/blocks/reader.ts），所以同一段字在
// 消息里和在文档里长得一样。点名、话题、文件的 chip 只画不接：点击由外层按 data
// 属性委派，和原来一样。
//
// 读法连着 tiptap、ProseMirror 和整套块，首屏不等它：第一次画的时候才加载。
// 加载好之前，对话里的一条（as="chat"）先照原文画出来：空着的话，读法一到每条消息
// 一齐撑高，整列往下跳。别处（文档、文件）加载好之前是空的。
import type { ReadAs } from '@/lib/docRead'
import type { RefNames } from '@/lib/refChip'

import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { refsVersion } from '@/lib/refDirectory'
import { renderPlain } from '@/lib/renderMessage'

type Reader = typeof import('@/components/panels/doc/blocks/reader')
let reader: Reader | null = null
let readerLoading: Promise<Reader> | null = null
function loadReader(): Promise<Reader> {
  readerLoading ??= import('@/components/panels/doc/blocks/reader').then((m) => (reader = m))
  return readerLoading
}

const props = withDefaults(
  defineProps<{
    source: string
    /** chat：单个换行就是换行（作者敲的那个回车）。 */
    as?: ReadAs
    names?: RefNames
    /** 代码块右上角放一颗「复制」。 */
    copyCode?: boolean
  }>(),
  { as: 'doc', names: undefined, copyCode: false }
)

const host = ref<HTMLElement | null>(null)

/** 读法还没到时的那一份：原文，换行照留，引用画成 chip。 */
function plainFirst(source: string, names: RefNames | undefined): string {
  return `<div class="md-plain-first">${renderPlain(source, names ?? { mentionNames: {}, topicTitles: {} })}</div>`
}
let stop: (() => void) | null = null
let gone = false

function draw() {
  if (!reader) {
    if (host.value && props.as === 'chat') host.value.innerHTML = plainFirst(props.source, props.names)
    void loadReader().then(() => gone || draw())
    return
  }
  stop?.()
  stop = host.value
    ? reader.mountMarkdown(host.value, props.source, { as: props.as, names: props.names, copyCode: props.copyCode })
    : null
}

onMounted(draw)
watch([() => props.source, () => props.as, () => props.copyCode], draw)
watch(
  () => (/<[@#&]/.test(props.source) ? props.names : undefined),
  (names) => names && draw(),
  { deep: true }
)
// 一个 `<#id>` 指着什么是后到的（lib/refDirectory）：到了，有这种引用的再画一遍。
watch(refsVersion, () => props.source.includes('<#') && draw())
onBeforeUnmount(() => {
  gone = true
  stop?.()
})

/** 画出来的那块 DOM：要从读者的选区算出原文的地方（文件预览的引用）用它。 */
defineExpose({ el: host })
</script>

<template>
  <!-- .md-reader: this is reading-mode body copy; each block gets content-visibility
       off-screen (see styles/docBlocks.css). -->
  <div ref="host" class="md-reader" />
</template>

<style scoped>
.md-reader :deep(.md-plain-first) {
  white-space: pre-wrap;
}
</style>
