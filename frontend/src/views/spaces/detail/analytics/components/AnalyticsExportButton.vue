<template>
  <BaseButton kind="secondary" prepend-icon="mdi-download" :loading="loading" @click="emit('export')">
    {{ label }}
  </BaseButton>
</template>

<script setup lang="ts">
import BaseButton from '@/components/base/BaseButton.vue'

// 导出这一格数据的那颗按钮：只画按钮、只报「点了导出」。拉 CSV 要带登录凭据，那是
// 页面的事（`composables/useAnalyticsExport.ts`）；放在这里，整块画面就自己够得到接口，
// 不能只给 props 挂起来了。
defineOptions({ name: 'AnalyticsExportButton' })

defineProps<{
  /** 按钮上的字，各格自己传（「导出参与者」一类）。 */
  label?: string
  /** 这一格正在导出。 */
  loading?: boolean
}>()

const emit = defineEmits<{
  export: []
}>()
</script>

<style scoped></style>
