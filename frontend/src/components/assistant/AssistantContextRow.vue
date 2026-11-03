<script setup lang="ts">
// 输入框里那一排「这一句带上的」：芝士读的这一页、人选中交给芝士的那段字、待交的文件。
// 和频道输入框里的回复、引用、附件同一种标签（`ComposerChipRow`）：记号、名字、×。
// 这一页点 × 是这一问不带它：名字划掉，× 换成 +，点了带回来；换一页自己带回来。
import type { ChatAttachment } from '@/cx_types'

import ComposerChip from '@/components/room/ComposerChip.vue'
import ComposerChipRow from '@/components/room/ComposerChipRow.vue'
import { t } from '@/i18n'

defineProps<{
  /** 这一页叫什么；芝士读不到这一页时是 null，不画这一枚。 */
  page: string | null
  pageOff: boolean
  selection: string | null
  atts: ChatAttachment[]
}>()

const emit = defineEmits<{
  togglePage: []
  clearSelection: []
  removeAtt: [index: number]
}>()
</script>

<template>
  <ComposerChipRow
    :lead="!!page || !!selection"
    :atts="atts"
    :topic-id="null"
    @remove-att="(i) => emit('removeAtt', i)"
  >
    <template #lead>
      <ComposerChip
        v-if="page"
        key="page"
        quiet
        :label="page"
        :remove-label="t('tasks.assistant.context.leavePage')"
        :restore-label="pageOff ? t('tasks.assistant.context.includePage') : undefined"
        @remove="emit('togglePage')"
        @restore="emit('togglePage')"
      >
        <template #face
          ><v-icon size="13">{{ pageOff ? 'mdi-eye-off-outline' : 'mdi-eye-outline' }}</v-icon></template
        >
      </ComposerChip>
      <ComposerChip
        v-if="selection"
        key="selection"
        class="ctx-selection"
        quiet
        :label="selection"
        :remove-label="t('tasks.assistant.context.removeSelection')"
        @remove="emit('clearSelection')"
      >
        <template #face><v-icon size="13">mdi-format-quote-close</v-icon></template>
      </ComposerChip>
    </template>
  </ComposerChipRow>
</template>

<style scoped>
/* 面板窄：选中的字截得短一些，和这一页那枚并排时 × 还在眼前；全文在悬停气泡里。 */
.ctx-selection {
  max-width: 200px;
}
</style>
