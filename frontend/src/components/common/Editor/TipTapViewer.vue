<script setup lang="ts">
// 富文本的只读那一侧：扩展和排版都与编辑器同一份（./richText.ts、RichTextContent），
// 编辑时什么样，看的时候就什么样。收 JSON 文档（对象或字符串）和 HTML；`format` 给
// `markdown` 时字符串当 Markdown 读（从 PDF 导入的题目描述）。
import type { JSONContent } from '@tiptap/core'

import { onBeforeUnmount, ref, watch } from 'vue'
import { useEditor } from '@tiptap/vue-3'

import { useInAppLinks } from '@/composables/useInAppLinks'

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

// 正文里指向站内页面的链接在应用里翻页，不整页重载（useInAppLinks）。挂的是编辑器
// 自己的 DOM（`view.dom`），不加一层包住正文的 div —— 那会动到版式。
const host = ref<HTMLElement | null>(null)
useInAppLinks(host)
watch(
  editor,
  (ed) => {
    host.value = ed?.view.dom ?? null
  },
  { immediate: true }
)

watch(
  () => props.value,
  (value) => editor.value?.commands.setContent(content(value), { emitUpdate: false })
)

onBeforeUnmount(() => editor.value?.destroy())
</script>

<template>
  <RichTextContent :editor="editor" />
</template>
