<script setup lang="ts">
// 一段 Markdown 的阅读视图：消息、文件、周报、文档的旧版本。
//
// 它和文档用同一套解析和同一套块（panels/doc/blocks/reader.ts），所以同一段字在
// 消息里和在文档里长得一样。点名、话题、文件的 chip 只画不接：点击由外层按 data
// 属性委派，和原来一样。
import type { ReadAs } from '@/lib/docRead'
import type { RefNames } from '@/lib/refChip'

import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { mountMarkdown } from '@/components/panels/doc/blocks/reader'

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
let stop: (() => void) | null = null

function draw() {
  stop?.()
  stop = host.value
    ? mountMarkdown(host.value, props.source, { as: props.as, names: props.names, copyCode: props.copyCode })
    : null
}
onMounted(draw)
watch([() => props.source, () => props.as, () => props.names, () => props.copyCode], draw, { deep: true })
onBeforeUnmount(() => stop?.())

/** 画出来的那块 DOM：要从读者的选区算出原文的地方（文件预览的引用）用它。 */
defineExpose({ el: host })
</script>

<template>
  <div ref="host" />
</template>
