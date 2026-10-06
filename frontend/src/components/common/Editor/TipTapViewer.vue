<script setup lang="ts">
// 富文本的只读那一侧：扩展和排版都与编辑器同一份（./richText.ts、RichTextContent），
// 编辑时什么样，看的时候就什么样。收 JSON 文档（对象或字符串）和 HTML；`format` 给
// `markdown` 时字符串当 Markdown 读（从 PDF 导入的题目描述）。
import type { JSONContent } from '@tiptap/core'

import { onBeforeUnmount, watch } from 'vue'
import { useEditor } from '@tiptap/vue-3'

import { markdownContent, richTextExtensions, viewerContent } from './richText'
import RichTextContent from './RichTextContent.vue'

import { READING } from '@/components/panels/doc/blocks/shapes'

const props = defineProps<{ value: string | JSONContent; format?: 'markdown' }>()

const content = (value: string | JSONContent) =>
  props.format === 'markdown' && typeof value === 'string' ? markdownContent(value) : viewerContent(value)

const editor = useEditor({
  content: content(props.value),
  extensions: richTextExtensions(),
  editable: false,
  // 读的人看到的样子和文档一样：脚注在引用处弹出，文末的脚注列表收起来。
  editorProps: { attributes: { class: READING } },
})

watch(
  () => props.value,
  (value) => editor.value?.commands.setContent(content(value), { emitUpdate: false })
)

onBeforeUnmount(() => editor.value?.destroy())
</script>

<template>
  <RichTextContent :editor="editor" />
</template>
