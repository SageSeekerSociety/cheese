<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'

// 删除 / 停用（启用）共用的确认框。两处的形态完全一样 —— 说清后果、一个取消、一个主
// 操作 —— 只有文案不同，所以是一件东西开两次，不是两件东西。
//
// 文案由页面给（它知道删的是谁、停的是谁：名字要进那句话里），「那个主操作叫什么」也
// 由页面给（删除 / 停用 / 启用是三个词）。
const props = defineProps<{
  modelValue: boolean
  /** 框题（「删除模型」/「停用模型」）。 */
  title: string
  /** 正文：说清后果，名字已经拼在里面。 */
  body: string
  /** 主操作上那两个字（「删除」/「停用」/「启用」）。 */
  confirmLabel: string
  /** 正在写：**框不许关**（`persistent`），两个按钮都灰 —— 关掉框不会让请求停下来。 */
  busy: boolean
}>()

const emit = defineEmits<{ 'update:modelValue': [open: boolean]; confirm: [] }>()

const { t } = useI18n()
</script>

<template>
  <v-dialog
    :model-value="props.modelValue"
    max-width="440"
    :persistent="props.busy"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <v-card rounded="lg">
      <v-card-title class="t-dialog-title px-4 pt-4 pb-2">{{ props.title }}</v-card-title>
      <v-card-text class="px-4">{{ props.body }}</v-card-text>
      <v-card-actions class="pa-4 pt-0">
        <v-spacer />
        <BaseButton kind="ghost" :disabled="props.busy" @click="emit('update:modelValue', false)">
          {{ t('models.dialog.cancel') }}
        </BaseButton>
        <BaseButton kind="danger" solid :loading="props.busy" @click="emit('confirm')">{{
          props.confirmLabel
        }}</BaseButton>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
