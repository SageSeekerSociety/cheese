<script setup lang="ts">
// 页头上右侧面板的开关「概览」。开着时按下的样子，再点收起；Esc 收起浮层后焦点回到
// 这颗（`data-panel-toggle`）。
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'toggle'): void }>()
</script>

<template>
  <BaseButton
    data-panel-toggle
    size="sm"
    prepend-icon="mdi-page-layout-sidebar-right"
    class="panel-toggle tap-target"
    :class="{ 'panel-toggle--open': open }"
    :aria-expanded="open ? 'true' : 'false'"
    aria-controls="topic-panel"
    :title="open ? t('work.room.panel.close') : t('work.room.panel.open')"
    @click="emit('toggle')"
  >
    {{ t('work.room.panel.label') }}
  </BaseButton>
</template>

<style scoped>
.panel-toggle--open {
  background: var(--fill-2);
  color: var(--ink);
}
</style>
