<script setup lang="ts">
// 在预览格里和几个人一起改同一份 markdown：正文绑在协同文档上，谁打的字都实时合进
// 同一份，协作服务在停手几秒后把它存回这份文件本身。
//
// 和「文档」那一格（PanelDocView + DocSurface）用的是同一套 schema、同一个编辑器内核，
// 复用它的正文排版（DocProse）和块视图（editorBlocks）；不同的是这一只小得多：预览格
// 里不摆评论面板、大纲、mention 名册、画布那一整套，只要正文能一起改。真的那套是
// rich editor，这里要的是「同一份文本，几个人同编」。
//
// 编辑器只在会话到了之后建：`Collaboration` 扩展只在建编辑器时认一次文档，所以文档
// 换了就整个重建（`watch` 里 destroy 再 build）。
import type { PanelPreviewLiveEdit } from '../../../composables/panelPreview/liveEdit'
import type { DocSession } from '../../../composables/useDocCollab'

import { onBeforeUnmount, shallowRef, watch } from 'vue'
import Collaboration from '@tiptap/extension-collaboration'
import CollaborationCaret from '@tiptap/extension-collaboration-caret'

import { renderCaret } from '../../../lib/docCaret'
import { docExtensions } from '../../../lib/docSchema'
import { editorBlocks } from '../doc/blocks'
import { DocEditor } from '../doc/docEditor'
import DocProse from '../doc/DocProse.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  /** 这一份的协同编辑会话（`composables/panelPreview/liveEdit.ts` 那一包）。 */
  live: PanelPreviewLiveEdit
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

const editor = shallowRef<DocEditor | undefined>()

function buildEditor(session: DocSession): DocEditor {
  return new DocEditor({
    extensions: [
      ...editorBlocks(docExtensions()),
      Collaboration.configure({ document: session.doc }),
      CollaborationCaret.configure({ provider: session.provider, user: session.user, render: renderCaret }),
    ],
    editable: !props.live.readOnly.value,
    editorProps: { attributes: { class: 'doc-prose' } },
  })
}

watch(
  () => props.live.session.value,
  (session) => {
    editor.value?.destroy()
    editor.value = session ? buildEditor(session) : undefined
  },
  { immediate: true }
)
onBeforeUnmount(() => editor.value?.destroy())

// 能不能改：只读的人键盘收起来。emitUpdate=false —— tiptap v3 的 setEditable 默认会
// 发一次假 update，正文一个字没动。
watch(
  () => props.live.readOnly.value,
  (ro) => editor.value?.setEditable(!ro, false)
)
</script>

<template>
  <div class="md-live" data-testid="markdown-live">
    <div class="md-live__bar">
      <v-icon size="16" class="md-live__icon">mdi-pencil-outline</v-icon>
      <span class="md-live__name">{{ live.editingPath.value }}</span>
      <span v-if="live.connection.value !== 'connected'" class="md-live__state t-meta">
        {{ t('work.room.preview.liveConnecting') }}
      </span>
      <v-spacer />
      <BaseButton kind="secondary" size="sm" data-testid="live-edit-close" @click="emit('close')">
        {{ t('work.room.preview.done') }}
      </BaseButton>
    </div>
    <v-alert v-if="live.outdated.value" type="warning" density="compact" class="mx-3 mb-2">
      {{ t('work.room.doc.outdated') }}
    </v-alert>
    <v-alert v-else-if="live.error.value" type="error" density="compact" class="mx-3 mb-2">
      {{ live.error.value }}
    </v-alert>
    <DocProse :editor="editor" :loading="!live.synced.value" class="md-live__body" />
  </div>
</template>

<style scoped>
.md-live {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  flex-direction: column;
}
.md-live__bar {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px 4px 12px;
  border-bottom: 1px solid var(--line);
}
.md-live__icon {
  color: var(--muted);
}
.md-live__name {
  font-size: 13px;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.md-live__state {
  color: var(--faint);
  flex: none;
}
.md-live__body {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
}
</style>
