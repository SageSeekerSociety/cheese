<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import ConfirmDialog from '@/components/base/ConfirmDialog.vue'

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
  <!-- Confirm deletion / block: title, the consequence, cancel and a solid red confirm. -->
  <ConfirmDialog
    :model-value="props.modelValue"
    :title="props.title"
    :confirm-label="props.confirmLabel"
    :cancel-label="t('models.dialog.cancel')"
    :loading="props.busy"
    danger
    @update:model-value="emit('update:modelValue', $event)"
    @confirm="emit('confirm')"
  >
    {{ props.body }}
  </ConfirmDialog>
</template>
