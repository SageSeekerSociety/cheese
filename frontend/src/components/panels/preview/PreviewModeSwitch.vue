<script setup lang="ts">
// 文档条上的「看 / 批注 / 改」三段。照原型：三颗并排的键，选中的那颗底色亮一点，
// 每颗带图标和一行 title（原型里也是 title，不是常显的说明字）。
//
// 「改」那一颗跟着这一份能不能改：不能改时是灰的，点它不生效，title 换一句话说清为什么
// （原型里那句「运行中应用请通过批注交由芝士修改源码」同一条道理）。灰的键留在原处——
// 一会儿摆一会儿藏，读者会以为这一段不可靠。
import type { PreviewMode } from '@/composables/panelPreview/previewMode'

import { computed, inject } from 'vue'

import { PREVIEW_MODE } from '@/composables/panelPreview/previewMode'

import { t } from '@/i18n'

const props = defineProps<{
  /** 这一格窄下来了：只留图标，字收进 title。和文档条上别的键同一条规矩。 */
  compact?: boolean
}>()

const modes = inject(PREVIEW_MODE, null)

/** 现在选中的那一段。没有这个口（资料库、产物页那几处没有话题页）时按「看」。 */
const active = computed<PreviewMode>(() => modes?.mode.value ?? 'view')

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
  return modes?.enabled(key) ?? key !== 'edit'
}
</script>

<template>
  <div class="mode-switch" role="group" :aria-label="t('work.room.preview.modeGroup')">
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
      @click="modes?.set(item.key)"
    >
      <v-icon size="15">{{ item.icon }}</v-icon>
      <span v-if="!props.compact" class="mode-switch__label">{{ item.label }}</span>
    </button>
  </div>
</template>

<style scoped>
.mode-switch {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border-radius: var(--radius-sm);
  background: var(--fill);
}
.mode-switch__item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  font: inherit;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  background: none;
  border: 0;
  border-radius: calc(var(--radius-sm) - 1px);
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
