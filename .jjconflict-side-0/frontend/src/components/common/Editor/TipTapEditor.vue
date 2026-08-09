<template>
  <VuetifyTiptap
    ref="editor"
    v-model="content"
    rounded
    editor-class="tiptap-editor"
    :output="output"
    :min-height="minHeight"
    :max-height="maxHeight"
    :hide-toolbar="hideToolbar"
    :dense="dense"
    flat
  >
    <template #bottom>
      <slot name="bottom"></slot>
    </template>
  </VuetifyTiptap>
</template>

<script setup lang="ts">
import type { JSONContent } from 'vuetify-pro-tiptap'

import { computed, ref } from 'vue'
import { VuetifyTiptap } from 'vuetify-pro-tiptap'

const props = defineProps<{
  output?: 'json' | 'html' | 'text'
  minHeight?: number
  maxHeight?: number
  hideToolbar?: boolean
  dense?: boolean
}>()

const editor = ref<InstanceType<typeof VuetifyTiptap> | null>(null)

const editorInstance = computed(() => editor.value?.editor)

const isEmpty = computed(() => {
  return editorInstance.value?.isEmpty ?? true
})

const modelValue = defineModel<string | JSONContent>()
const contentModel = defineModel<string | JSONContent>('content')

const EMPTY_DOC: JSONContent = {
  type: 'doc',
  content: [{ type: 'paragraph' }],
}

const normalizeContent = (value: string | JSONContent | undefined): string | JSONContent => {
  if (props.output !== 'json') {
    return value ?? ''
  }
  if (typeof value === 'string') {
    if (!value.trim()) {
      return EMPTY_DOC
    }
    try {
      const parsed = JSON.parse(value) as JSONContent
      if (parsed?.type === 'doc') {
        return normalizeContent(parsed)
      }
    } catch {
      // Treat legacy plain-text descriptions as editable TipTap content.
    }
    return {
      type: 'doc',
      content: [
        {
          type: 'paragraph',
          content: [{ type: 'text', text: value }],
        },
      ],
    }
  }
  if (!value || value.type !== 'doc' || !Array.isArray(value.content) || value.content.length === 0) {
    return EMPTY_DOC
  }
  return value
}

const content = computed<string | JSONContent | undefined>({
  get() {
    return normalizeContent(contentModel.value ?? modelValue.value)
  },
  set(value) {
    const normalized = normalizeContent(value)
    modelValue.value = normalized
    contentModel.value = normalized
  },
})

defineExpose({
  editor: editorInstance,
  isEmpty,
})
</script>

<style lang="scss">
.vuetify-pro-tiptap-editor {
  overflow: visible;
}
</style>
