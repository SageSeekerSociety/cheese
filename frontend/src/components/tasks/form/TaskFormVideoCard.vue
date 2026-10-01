<script setup lang="ts">
import { useI18n } from 'vue-i18n'

// 视频链接那张卡：一个选填的地址，外加一句「支持 Bilibili」的提示。
//
// 它只画：值进、值出，红字由 `defineField` 的另一半给。至于这个地址最后能不能解析
// （不支持的站，提交前要不要拦一道），那是提交那条路上的第二道闸门
// （`useTaskForm.ts`），这一件不知道 —— 所以它也能单独摆在预览站里。
//
// `parametersOnly`（只发参数）那条路上整张卡不存在：没有视频链接这回事。
import TaskFormSection from './TaskFormSection.vue'

const { t } = useI18n()

/** `defineField` 那一对的另一半：`error-messages` / `error`，`v-bind` 到控件上。 */
type FieldControl = Record<string, any>

defineProps<{
  parametersOnly?: boolean
  videoUrlControl: FieldControl
}>()

const videoUrl = defineModel<string | undefined>('videoUrl', { required: true })
</script>

<template>
  <TaskFormSection v-if="!parametersOnly" icon="mdi-video-outline" :title="t('tasks.form.videoTitle')">
    <v-text-field
      v-model="videoUrl"
      autocomplete="off"
      v-bind="videoUrlControl"
      :label="t('tasks.form.video.label')"
      placeholder="https://..."
      :hint="t('tasks.form.video.hint')"
      persistent-hint
    >
      <template #prepend-inner>
        <v-icon size="small" color="primary">mdi-link-variant</v-icon>
      </template>
    </v-text-field>
  </TaskFormSection>
</template>

<style scoped src="./task-form.css"></style>
