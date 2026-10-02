<script setup lang="ts">
// 题目详情、知识库、空间公告与模板、团队简介共用的富文本编辑器。扩展和实况文档是同一套
// （./richText.ts），工具栏也是实况文档那一排，再接上插图和表格。
//
// v-model 的形状由 `output` 定：`json` 是 tiptap 的文档 JSON（题目、知识库、模板），
// `html` 是一段 HTML（公告、团队简介）。两种都只在有人改了内容时才往外发。
import type { JSONContent } from '@tiptap/core'
import type { ExtraFormatAction } from '@/components/panels/doc/DocFormatToolbar.vue'

import { computed, inject, onBeforeUnmount, ref, watch } from 'vue'
import { toast } from 'vuetify-sonner'
import { useEditor } from '@tiptap/vue-3'

import { ATTACHMENT_IMAGE_SOURCE } from './attachmentImageSource'
import { jsonContent, richTextExtensions } from './richText'
import RichTextContent from './RichTextContent.vue'

import DocFormatToolbar from '@/components/panels/doc/DocFormatToolbar.vue'
import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    output?: 'json' | 'html'
    minHeight?: number
    maxHeight?: number
    hideToolbar?: boolean
    placeholder?: string
    ariaLabel?: string
  }>(),
  { output: 'json', minHeight: undefined, maxHeight: undefined, placeholder: undefined, ariaLabel: undefined }
)

const model = defineModel<string | JSONContent>()

function incoming(value: unknown): JSONContent | string {
  if (props.output === 'html') return typeof value === 'string' ? value : ''
  return jsonContent(value)
}

/** 编辑器里现在的内容，按 v-model 的形状。 */
function current(): JSONContent | string {
  const ed = editor.value
  if (!ed) return ''
  return props.output === 'html' ? ed.getHTML() : ed.getJSON()
}

const empty = ref(true)

const editor = useEditor({
  content: incoming(model.value),
  extensions: richTextExtensions(),
  editorProps: {
    attributes: {
      role: 'textbox',
      'aria-multiline': 'true',
      ...(props.ariaLabel ? { 'aria-label': props.ariaLabel } : {}),
    },
  },
  onCreate: ({ editor: ed }) => {
    empty.value = ed.isEmpty
  },
  onUpdate: ({ editor: ed }) => {
    empty.value = ed.isEmpty
    model.value = props.output === 'html' ? ed.getHTML() : ed.getJSON()
  },
})

// 外面换了内容（换了一条在编辑的公告、加载完一道题）才装进去；是这边刚发出去的那份，
// 装回来只会把光标挪走。
watch(model, (value) => {
  const ed = editor.value
  if (!ed) return
  const next = incoming(value)
  if (JSON.stringify(next) === JSON.stringify(current())) return
  ed.commands.setContent(next, { emitUpdate: false })
  empty.value = ed.isEmpty
})

onBeforeUnmount(() => editor.value?.destroy())

// ---- 插图：先传成附件，再把附件 id 写进正文。
const images = inject(ATTACHMENT_IMAGE_SOURCE, null)
const fileInput = ref<HTMLInputElement | null>(null)
const uploading = ref(false)

async function insertImage(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || !images) return
  uploading.value = true
  try {
    const image = await images.upload(file)
    editor.value?.chain().focus().setImage(image).run()
  } catch {
    toast.error(t('editor.image.uploadFailed'))
  } finally {
    uploading.value = false
  }
}

const inTable = (ed: { isActive: (name: string) => boolean }) => ed.isActive('table')

const extraActions = computed<ExtraFormatAction[]>(() => [
  {
    key: 'codeBlock',
    icon: 'mdi-code-braces',
    label: t('editor.toolbar.codeBlock'),
    isActive: (ed) => ed.isActive('codeBlock'),
    command: (c) => c.toggleCodeBlock(),
  },
  {
    key: 'image',
    icon: 'mdi-image-plus-outline',
    label: t('editor.toolbar.image'),
    visible: () => images !== null,
    run: () => fileInput.value?.click(),
    busy: uploading.value,
  },
  {
    key: 'table',
    icon: 'mdi-table-plus',
    label: t('editor.toolbar.table'),
    visible: (ed) => !inTable(ed),
    command: (c) => c.insertTable({ rows: 3, cols: 3, withHeaderRow: true }),
  },
  {
    key: 'addRow',
    icon: 'mdi-table-row-plus-after',
    label: t('editor.toolbar.addRow'),
    visible: inTable,
    command: (c) => c.addRowAfter(),
  },
  {
    key: 'addColumn',
    icon: 'mdi-table-column-plus-after',
    label: t('editor.toolbar.addColumn'),
    visible: inTable,
    command: (c) => c.addColumnAfter(),
  },
  {
    key: 'deleteRow',
    icon: 'mdi-table-row-remove',
    label: t('editor.toolbar.deleteRow'),
    visible: inTable,
    command: (c) => c.deleteRow(),
  },
  {
    key: 'deleteColumn',
    icon: 'mdi-table-column-remove',
    label: t('editor.toolbar.deleteColumn'),
    visible: inTable,
    command: (c) => c.deleteColumn(),
  },
  {
    key: 'deleteTable',
    icon: 'mdi-table-remove',
    label: t('editor.toolbar.deleteTable'),
    visible: inTable,
    command: (c) => c.deleteTable(),
  },
])

const bodyStyle = computed(() => ({
  minHeight: props.minHeight ? `${props.minHeight}px` : undefined,
  maxHeight: props.maxHeight ? `${props.maxHeight}px` : undefined,
}))

/** 点到正文下面的空白也算点进编辑器：最短的时候正文只有一行，框却有 200px 高。 */
function focusBody(event: MouseEvent) {
  if ((event.target as HTMLElement).closest('.ProseMirror')) return
  editor.value?.commands.focus('end')
}

defineExpose({
  editor,
  isEmpty: empty,
})
</script>

<template>
  <div class="rt-editor">
    <DocFormatToolbar
      v-if="!hideToolbar"
      class="rt-editor__toolbar"
      :editor="editor ?? null"
      :disabled="false"
      disabled-reason=""
      :extra="extraActions"
    />
    <div class="rt-editor__body" :style="bodyStyle" @click="focusBody">
      <RichTextContent :editor="editor" :placeholder="placeholder ?? t('editor.placeholder')" />
    </div>
    <input ref="fileInput" type="file" accept="image/*" hidden @change="insertImage" />
  </div>
</template>

<style scoped>
.rt-editor {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
  transition: border-color var(--dur-quick) var(--ease-standard);
}
.rt-editor:focus-within {
  border-color: var(--muted);
}
.rt-editor__toolbar {
  flex: 0 0 auto;
  border-bottom: 1px solid var(--line);
}
.rt-editor__body {
  flex: 1 1 auto;
  padding: 12px 16px;
  overflow-y: auto;
  cursor: text;
}
.rt-editor__body :deep(.ProseMirror) {
  min-height: 24px;
}
</style>
