<script setup lang="ts">
// 在预览格里直接改一份 markdown：正文变成可编辑的，存回那一份房间文件本身。
//
// 和「概览文档」那种多人实时协同不是一回事（那一套走协作服务，房间文件是磁盘上的
// 字节）：这里做的是「能改、存得住、冲突不丢」。取数那一包在
// `composables/panelPreview/textEdit.ts`（外壳调一次、递下来），这一只只画那条横条、
// 一个编辑器和一个冲突/失败的提示。
import type { PanelPreviewTextEdit } from '../../../composables/panelPreview/textEdit'

import CodeEditor from '../../CodeEditor.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  /** 这一份文件的编辑会话（`composables/panelPreview/textEdit.ts` 那一包）。 */
  edit: PanelPreviewTextEdit
  /** 正在改哪一份（标题用）。 */
  path: string
}>()

const emit = defineEmits<{
  /** 存成了：编辑收起来、预览台读最新那一版。 */
  (e: 'saved'): void
  (e: 'cancel'): void
  /** 正文被编辑了：会话那一包里的草稿跟着换（这一只不直接改递进来的 prop）。 */
  (e: 'draft', value: string): void
}>()

async function save() {
  if (await props.edit.save()) emit('saved')
}
</script>

<template>
  <div class="md-edit">
    <div class="md-edit__bar">
      <v-icon size="16" class="md-edit__icon">mdi-pencil-outline</v-icon>
      <span class="md-edit__name">{{ path }}</span>
      <v-spacer />
      <BaseButton
        kind="secondary"
        size="sm"
        data-testid="text-edit-cancel"
        :disabled="edit.saving.value"
        @click="emit('cancel')"
      >
        {{ t('work.room.preview.cancel') }}
      </BaseButton>
      <BaseButton kind="primary" size="sm" data-testid="text-edit-save" :disabled="edit.saving.value" @click="save">
        {{ edit.saving.value ? t('work.room.preview.saving') : t('work.room.preview.save') }}
      </BaseButton>
    </div>
    <!-- 别人先改过：这一版不写，说清该怎么做，别让人对着一个存不下去的按钮点。 -->
    <v-alert v-if="edit.conflicted.value" type="warning" density="compact" class="mx-3 mb-2">
      {{ t('work.room.preview.saveConflict') }}
    </v-alert>
    <v-alert v-else-if="edit.error.value" type="error" density="compact" class="mx-3 mb-2">
      {{ edit.error.value }}
    </v-alert>
    <!-- 手机上不给编辑：软键盘配 Monaco 不是一件值得存下去的事（和「改动」那一格同一条）。 -->
    <CodeEditor
      :model-value="edit.draft.value ?? ''"
      :filename="path"
      class="md-edit__editor"
      @update:model-value="emit('draft', $event)"
      @save="save"
    />
  </div>
</template>

<style scoped>
.md-edit {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  flex-direction: column;
}
.md-edit__bar {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px 4px 12px;
  border-bottom: 1px solid var(--line);
}
.md-edit__icon {
  color: var(--muted);
}
.md-edit__name {
  font-size: 13px;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.md-edit__editor {
  flex: 1 1 auto;
  min-height: 0;
}
</style>
