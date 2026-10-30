<script setup lang="ts">
// 「题目内容」那一块的第一节「说明」：名称、描述，以及调用方塞进来的附件。
//
// 除了画，它还管一件事：把编辑器里**现在**的正文念出来（`readText`）。提交那一刻才
// 读一次正文（简介由它截出来），是这套行为的一部分，所以是一次调用，不是一串事件：
// `defineExpose` 出去，容器接到 `useTaskForm.ts` 的 `readDescriptionText` 上。
import type { DescriptionDoc } from '@/composables/useTaskForm'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import TaskFormSection from './TaskFormSection.vue'

import BaseField from '@/components/base/BaseField.vue'
import TipTapEditor from '@/components/common/Editor/TipTapEditor.vue'
import { serializeDoc } from '@/lib/docSchema/markdown'

/** `defineField` 那一对的另一半：`error-messages` / `error`。 */
type FieldControl = Record<string, any>

const NAME_MAX = 100

withDefaults(
  defineProps<{
    nameControl: FieldControl
    /** 这一节的标题；不给就是「说明」。一次发好几道时写明是第几道。 */
    title?: string
  }>(),
  { title: undefined }
)

defineSlots<{
  /** 标题右边：「从文件导入」「使用模板」。 */
  actions?: () => unknown
  /** 描述下面：附件。 */
  attachments?: () => unknown
}>()

const name = defineModel<string | undefined>('name', { required: true })
const description = defineModel<string | DescriptionDoc>('description', { required: true })

const { t } = useI18n()

const editor = ref<InstanceType<typeof TipTapEditor> | null>(null)

defineExpose({
  /** 编辑器里现在的正文。 */
  readText: () => editor.value?.editor?.getText(),
  /** 正文写成 Markdown：芝士读写详情用的就是它。 */
  readMarkdown: (): string | undefined => {
    const ed = editor.value?.editor as { getMarkdown?: () => string } | null | undefined
    return ed?.getMarkdown ? serializeDoc(ed as { getMarkdown: () => string }) : undefined
  },
})
</script>

<template>
  <TaskFormSection :title="title ?? t('tasks.form.brief')">
    <template v-if="$slots.actions" #actions><slot name="actions" /></template>

    <BaseField
      data-agent-field="name"
      :label="t('tasks.form.name')"
      required
      :error="nameControl['error-messages']?.[0]"
      :counter="{ current: name?.length ?? 0, max: NAME_MAX }"
    >
      <template #default="{ id, describedby, invalid, required }">
        <v-text-field
          :id="id"
          v-model="name"
          :aria-describedby="describedby"
          :aria-invalid="invalid"
          :aria-required="required"
          :maxlength="NAME_MAX"
          :placeholder="t('tasks.form.namePlaceholder')"
          :error="invalid"
          autocomplete="off"
          variant="outlined"
          density="comfortable"
          hide-details
        />
      </template>
    </BaseField>

    <BaseField data-agent-field="description" :label="t('tasks.form.description')">
      <TipTapEditor
        ref="editor"
        v-model="description"
        output="json"
        :min-height="200"
        :max-height="1000"
        :placeholder="t('tasks.form.descriptionPlaceholder')"
        :aria-label="t('tasks.form.description')"
      />
    </BaseField>

    <div v-if="$slots.attachments" data-agent-field="attachments">
      <slot name="attachments" />
    </div>
  </TaskFormSection>
</template>
