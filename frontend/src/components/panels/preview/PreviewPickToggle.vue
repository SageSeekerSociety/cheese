<script setup lang="ts">
// 预览条上的「圈选」开关。只在网页预览上给这一颗：图片、PDF 这些走别的查看器，没有可圈的东西。
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{ on: boolean }>()
const emit = defineEmits<{ (e: 'toggle'): void }>()
</script>

<template>
  <BaseButton
    kind="ghost"
    :icon="on ? 'mdi-cursor-default-click' : 'mdi-vector-square'"
    size="sm"
    class="pick-toggle"
    :class="{ 'pick-toggle--on': on }"
    data-testid="preview-pick"
    :aria-pressed="on"
    :title="t('work.room.preview.pick')"
    @click="emit('toggle')"
  >
    {{ t('work.room.preview.pick') }}
  </BaseButton>
</template>

<style scoped>
/* 贴着预览条右边（应用那一档右边还有别的按钮，所以靠 margin 推而不是 spacer）。 */
.pick-toggle {
  margin-left: auto;
}
.pick-toggle--on {
  color: var(--accent);
}
</style>
