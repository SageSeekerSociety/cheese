<script setup lang="ts">
// 赛题详情那张卡：两种编辑器二选一 —— 原来就是 Markdown 的题继续用纯文本域，其余走
// 富文本（TipTap，存 JSON）。
//
// 除了画，它还管一件事：把编辑器里**现在**的正文念出来（`readText`）。这一手是给
// 容器用的 —— 提交那一刻（和弹确认框那一刻）才读一次正文，是这套行为的一部分，所以
// 它是一次调用，不是一串事件：`defineExpose` 出去，容器接到 `useTaskForm.ts` 的
// `readDescriptionText` 上。Markdown 那条路上没有富文本编辑器，念出来的是空。
import type { DescriptionDoc } from '@/composables/useTaskForm'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import TaskFormSection from './TaskFormSection.vue'

import TipTapEditor from '@/components/common/Editor/TipTapEditor.vue'

defineProps<{
  /** `markdown` 画纯文本域，`tiptap` 画富文本。 */
  descriptionFormat: 'markdown' | 'tiptap'
  /** 只发参数那条路上整张卡不存在：没有「题目详情」这回事。 */
  parametersOnly?: boolean
}>()

const markdownDescription = defineModel<string>('markdownDescription', { required: true })
const description = defineModel<string | DescriptionDoc>('description', { required: true })

const { t } = useI18n()

const descriptionEditor = ref<InstanceType<typeof TipTapEditor> | null>(null)

defineExpose({
  /** 富文本编辑器里现在的正文；不在（Markdown 那条路）就是 `undefined`。 */
  readText: () => descriptionEditor.value?.editor?.getText(),
})
</script>

<template>
  <TaskFormSection v-if="!parametersOnly" icon="mdi-text-box-outline" :title="t('tasks.form.taskDescription')">
    <!-- Markdown 格式使用纯文本编辑器 -->
    <v-textarea
      v-if="descriptionFormat === 'markdown'"
      v-model="markdownDescription"
      autocomplete="off"
      label="题目详情（Markdown 格式）"
      :rows="10"
      :max-rows="30"
      rounded
      class="markdown-textarea"
    ></v-textarea>
    <!-- TipTap JSON 格式使用富文本编辑器 -->
    <TipTapEditor
      v-else
      ref="descriptionEditor"
      v-model="description"
      output="json"
      rounded
      :min-height="200"
      :max-height="1000"
      editor-class="tiptap-editor"
    />
  </TaskFormSection>
</template>

<style scoped>
.tiptap-editor {
  margin-top: 0;
}

.markdown-textarea :deep(.v-textarea__textarea) {
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', 'Consolas', monospace;
  font-size: 0.9rem;
  line-height: 1.6;
  resize: vertical;
}

.markdown-textarea :deep(.v-textarea__field) {
  min-height: 200px;
}
</style>

<style scoped src="./task-form.css"></style>
