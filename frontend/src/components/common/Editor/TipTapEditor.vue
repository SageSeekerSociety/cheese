<script setup lang="ts">
// 题目详情、知识库、空间公告与模板、团队简介共用的富文本编辑器。扩展和实况文档是同一套
// （./richText.ts），工具栏也是实况文档那一排，再接上插图和表格。
//
// v-model 的形状由 `output` 定：`json` 是 tiptap 的文档 JSON（题目、知识库、模板），
// `html` 是一段 HTML（公告、团队简介）。两种都只在有人改了内容时才往外发。
import type { JSONContent } from '@tiptap/core'

import { computed, inject, onBeforeUnmount, ref, watch } from 'vue'
import { toast } from 'vuetify-sonner'
import { useEditor } from '@tiptap/vue-3'

import { ATTACHMENT_IMAGE_SOURCE } from './attachmentImageSource'
import { jsonContent, richTextExtensions } from './richText'
import RichTextContent from './RichTextContent.vue'
import RichTextToolbar from './RichTextToolbar.vue'

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

// 全屏：编辑区铺满窗口，Esc 或再点一次退出。
const fullscreen = ref(false)
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && fullscreen.value) fullscreen.value = false
}

const bodyStyle = computed(() =>
  fullscreen.value
    ? {}
    : {
        minHeight: props.minHeight ? `${props.minHeight}px` : undefined,
        maxHeight: props.maxHeight ? `${props.maxHeight}px` : undefined,
      }
)

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
  <!-- 全屏时挪到 body 下：页面里的祖先可能自带层叠和裁切，fixed 铺不满窗口。 -->
  <Teleport to="body" :disabled="!fullscreen">
    <div class="rt-editor" :class="{ 'is-fullscreen': fullscreen }" @keydown="onKeydown">
      <RichTextToolbar
        v-if="!hideToolbar"
        class="rt-editor__toolbar"
        :editor="editor"
        :can-insert-image="images !== null"
        :uploading="uploading"
        :fullscreen="fullscreen"
        @insert-image="fileInput?.click()"
        @toggle-fullscreen="fullscreen = !fullscreen"
      />
      <div class="rt-editor__body" :style="bodyStyle" @click="focusBody">
        <RichTextContent :editor="editor" :placeholder="placeholder ?? t('editor.placeholder')" />
      </div>
      <input ref="fileInput" type="file" accept="image/*" hidden @change="insertImage" />
    </div>
  </Teleport>
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
.rt-editor.is-fullscreen {
  position: fixed;
  inset: 0;
  /* 盖住页面和弹窗（Vuetify 弹窗 2400），工具栏的下拉（2500）仍在它之上。 */
  z-index: var(--z-overlay-2);
  border: none;
  border-radius: 0;
  /* 整屏铺开之后，顶上那条工具栏钻进刘海、正文最后几行压在 Home 横杠上。整块往里
     让出安全区，两者各自让开。桌面和没有安全区的设备上 `env()` 是 0，形状不变。 */
  padding-top: env(safe-area-inset-top, 0px);
  padding-bottom: env(safe-area-inset-bottom, 0px);
}
.rt-editor.is-fullscreen .rt-editor__body {
  padding: 24px max(16px, calc((100% - 760px) / 2));
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
