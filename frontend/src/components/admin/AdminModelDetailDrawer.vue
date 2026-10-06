<script setup lang="ts">
import { useAdminModelDetail } from '@/composables/useAdminModelDetail'

import AdminModelDetailDrawerView from './AdminModelDetailDrawerView.vue'

// 一个模型的详情抽屉（契约 §3.2）。**这一层自己去拉数据**（收一个 `name`），不接一个塞满
// 字段的 props 对象 —— 详情比列表项多出 `series` 和 `platform_usage` 两块，让调用方把这些
// 一起查好再传进来，它就得同时管两份加载态，而抽屉本来就需要自己的「正在加载」。
//
// 画在 `AdminModelDetailDrawerView.vue`（只吃 props、只往上发事件）。取数本身在
// `composables/useAdminModelDetail` —— 模型管理页把抽屉画在自己视图里时用的是同一份，
// 那边详情跟着页面的窗口走（见那一份的注释）。
const props = defineProps<{
  modelValue: boolean
  /** 要看的模型名。`null` 时抽屉不拉数据（关着的常态）。 */
  name: string | null
  /** 与页面统一的统计窗口，天。 */
  days: number
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
}>()

const { detail, loading, error, load } = useAdminModelDetail({
  open: () => props.modelValue,
  name: () => props.name,
  days: () => props.days,
})
</script>

<template>
  <AdminModelDetailDrawerView
    :open="modelValue"
    :name="name"
    :detail="detail"
    :loading="loading"
    :error="error"
    @close="emit('update:modelValue', false)"
    @retry="load"
  />
</template>
