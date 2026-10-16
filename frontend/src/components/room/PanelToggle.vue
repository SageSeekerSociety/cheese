<script setup lang="ts">
// 页头上右侧面板的开关「概览」。开着时按下的样子，再点收起；Esc 收起浮层后焦点回到
// 这颗（`data-panel-toggle`）。
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  open: boolean
  /** 面板里只有「文件」一格（私聊）：开关就叫「文件」，不叫「概览」。 */
  files?: boolean
}>()
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
    :title="
      props.files
        ? t(open ? 'work.room.panel.closeFiles' : 'work.room.panel.openFiles')
        : t(open ? 'work.room.panel.close' : 'work.room.panel.open')
    "
    @click="emit('toggle')"
  >
    {{ props.files ? t('work.room.tabs.files') : t('work.room.panel.label') }}
  </BaseButton>
</template>

<style scoped>
.panel-toggle--open {
  background: var(--fill-2);
  color: var(--ink);
}
</style>
