<script setup lang="ts">
// 文档条上的「看 / 批注 / 改」三段。照原型：三颗并排的键，选中的那颗底色亮一点，
// 每颗带图标和一行 title（原型里也是 title，不是常显的说明字）。
//
// 「改」那一颗只在**这一份真能就地改**的时候才摆（markdown 有协同会话、Office 那几种
// 在桌面宽上）。不能改就不摆：一颗灰着的键按下去什么都不做，读者还得悬停才知道为什么，
// 而它旁边那两颗本来就是「读」和「说」——能做的事一件没少。所以 PDF、图片、网页这些
// 上看到的是两颗。
//
// 整份模式没有时不摆：那几处（资料库、产物页）没有话题页，按下去什么都不做的键比不摆
// 更糟。
import type { PreviewMode } from '@/composables/panelPreview/previewMode'

import { computed, inject } from 'vue'

import { PREVIEW_MODE } from '@/composables/panelPreview/previewMode'

import { t } from '@/i18n'

const props = defineProps<{
  /** 这一格窄下来了：只留图标，字收进 title。和文档条上别的键同一条规矩。 */
  compact?: boolean
}>()

const modes = inject(PREVIEW_MODE, null)

/** 现在选中的那一段。没有这一份模式时整个控件不摆（见模板第一行），所以这下面几处
 *  对 `modes` 都用「一定在」的写法。 */
const active = computed<PreviewMode>(() => modes!.mode.value)

const ALL_ITEMS = [
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
]

/** 摆出来的那几颗：这一份开不起「改」时它就不摆，而不是灰着。判据每次现问
 *  （`modes.enabled`），markdown 的协同会话连上那一刻它就自己出现——那一下「改」是
 *  真能用的，不是一会儿能一会儿不能。 */
const items = computed(() => ALL_ITEMS.filter((item) => modes!.enabled(item.key)))
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
      :title="item.title"
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
.mode-switch__item:hover {
  color: var(--text);
}
.mode-switch__item.is-on {
  color: var(--text);
  background: var(--raised);
  box-shadow: var(--shadow-1);
}
.mode-switch__label {
  white-space: nowrap;
}
</style>
