<script setup lang="ts">
// 富文本的只读那一侧：扩展和排版都与编辑器同一份（./richText.ts、RichTextContent），
// 编辑时什么样，看的时候就什么样。收 JSON 文档（对象或字符串）和 HTML。
import type { JSONContent } from '@tiptap/core'

import { onBeforeUnmount, watch } from 'vue'
import { useEditor } from '@tiptap/vue-3'

import { richTextExtensions, viewerContent } from './richText'
import RichTextContent from './RichTextContent.vue'

const props = defineProps<{ value: string | JSONContent }>()

const editor = useEditor({
  content: viewerContent(props.value),
  extensions: richTextExtensions(),
  editable: false,
})

watch(
  () => props.value,
  (value) => editor.value?.commands.setContent(viewerContent(value), { emitUpdate: false })
)

onBeforeUnmount(() => editor.value?.destroy())
</script>

<template>
  <RichTextContent :editor="editor" />
</template>
