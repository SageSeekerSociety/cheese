<template>
  <BaseButton kind="secondary" prepend-icon="mdi-download" :loading="loading" @click="emit('export')">
    {{ label }}
  </BaseButton>
</template>

<script setup lang="ts">
// 导出按钮只发一件事：有人按了「导出」。打开下载、带 token、拼地址这些取数的事
// 归页面（场景规则见 docs/manual/dev/scenes.md）；从前它自己 fetch，才被算成取数的
// 那一档。
import BaseButton from '@/components/base/BaseButton.vue'

defineProps<{
  loading: boolean
  label: string
}>()

const emit = defineEmits<{ export: [] }>()
</script>
