<script setup lang="ts">
// 文档条上的「看 / 批注 / 改」三段。照原型：三颗并排的键，选中的那颗底色亮一点，
// 每颗带图标和一行 title（原型里也是 title，不是常显的说明字）。
//
// 「改」那一颗跟着这一份能不能改：不能改时是灰的，点它不生效，title 换一句话说清为什么
// （原型里那句「运行中应用请通过批注交由芝士修改源码」同一条道理）。灰的键留在原处——
// 一会儿摆一会儿藏，读者会以为这一段不可靠。
//
// 整份模式没有时不摆：那几处（资料库、产物页）没有话题页，按下去什么都不做的三颗键
// 比不摆更糟。
import type { PreviewMode } from '@/composables/panelPreview/previewMode'

import { computed, inject } from 'vue'

import { PREVIEW_MODE } from '@/composables/panelPreview/previewMode'

import { t } from '@/i18n'

const props = defineProps<{
  /** 这一格窄下来了：只留图标，字收进 title。和文档条上别的键同一条规矩。 */
  compact?: boolean
}>()

const modes = inject(PREVIEW_MODE, null)

/** 现在选中的那一段。没有这一份模式时整个控件不摆（见模板第一行）。 */
const active = computed<PreviewMode>(() => modes!.mode.value)

const items = computed(() => [
  {
    key: 'view' as const,
    label: t('work.room.preview.modeView'),
    icon: 'mdi-eye-outline',
    title: t('work.room.preview.modeViewTitle'),
  },
  {
    key: 'note' as const,
    label: t('work.room.preview.modeNote'),
    icon: 'mdi-message-draw',
    title: t('work.room.preview.modeNoteTitle'),
  },
  {
    key: 'edit' as const,
    label: t('work.room.preview.modeEdit'),
    icon: 'mdi-pencil-outline',
    title: t('work.room.preview.modeEditTitle'),
  },
])

function usable(key: PreviewMode): boolean {
  return modes!.enabled(key)
}
</script>

<template>
  <!-- 没有这一份模式时整个不摆：三颗键按下去什么都不做的控件，比不摆更糟（资料库、
       产物页那些没有话题页的地方挂的就是这一档）。 -->
  <div v-if="modes" class="mode-switch" role="group" :aria-label="t('work.room.preview.modeGroup')">
    <button
      v-for="item in items"
      :key="item.key"
      type="button"
      class="mode-switch__item"
      :class="{ 'is-on': active === item.key }"
      :disabled="!usable(item.key)"
      :title="usable(item.key) ? item.title : t('work.room.preview.modeEditBlocked')"
      :aria-pressed="active === item.key"
      :data-testid="`preview-mode-${item.key}`"
      @click="modes!.set(item.key)"
    >
      <v-icon size="15">{{ item.icon }}</v-icon>
      <span v-if="!props.compact" class="mode-switch__label">{{ item.label }}</span>
    </button>
  </div>
</template>

<style scoped>
/* 尺寸照原型（171×28、13px、圆角 8px、槽 --fill-2），也就是这一条上别的按钮那一号
   （历史、看原文、下载都是 28px 高、13px 字）。矮一号站在它们旁边会像是缩了一档。 */
.mode-switch {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border-radius: var(--radius-md);
  background: var(--fill-2);
}
.mode-switch__item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 24px;
  padding: 0 8px;
  font: inherit;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.mode-switch__item:hover:not(:disabled) {
  color: var(--text);
}
.mode-switch__item.is-on {
  color: var(--text);
  background: var(--raised);
  box-shadow: var(--shadow-1);
}
.mode-switch__item:disabled {
  color: var(--faint);
  cursor: default;
}
.mode-switch__label {
  white-space: nowrap;
}
</style>
