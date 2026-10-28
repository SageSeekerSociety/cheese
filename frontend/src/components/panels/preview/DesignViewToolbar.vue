<script setup lang="ts">
// 面板里「看这张图」那一行：缩放档位、旋转、翻转、裁剪、调色、还原。
//
// 「在图上说」那一行是 `DesignSketchToolbar`，两行分开：上行的东西只改读者看到的样子，
// 原图不动；下行的东西会把一张新图交给房间里。摆成一行会让人分不清哪颗按钮会改动文件。
import type { ImageAdjust } from '@/lib/imageEdit'

import { computed } from 'vue'

import { t } from '@/i18n'

const props = defineProps<{
  scale: number
  /** 当前是哪一档：适应视口 / 适应宽度 / 实际像素 / 自定义。 */
  mode: 'fitv' | 'fitw' | 'actual' | 'custom'
  adjust: ImageAdjust
  edited: boolean
  cropping: boolean
}>()
const emit = defineEmits<{
  zoom: [factor: number]
  mode: ['fitv' | 'fitw' | 'actual']
  rotate: []
  flip: []
  crop: []
  tone: [open: boolean]
  restore: []
}>()

/** 亮度、饱和度摊开会占掉半行，收在一颗按钮后面，按下才开。 */
const toneOpen = computed(() => props.adjust.brightness !== 100 || props.adjust.saturation !== 100)

const ZOOM_MODES = ['fitv', 'fitw', 'actual'] as const
</script>

<template>
  <div class="design-view-tools" role="group" :aria-label="t('design.viewTools')">
    <div class="design-view-tools__scroll">
      <button
        type="button"
        class="dt-btn"
        :aria-label="t('design.zoomOut')"
        :disabled="scale <= 0.1"
        @click="emit('zoom', 1 / 1.25)"
      >
        −
      </button>
      <select
        class="dt-select"
        :value="mode"
        :aria-label="t('design.zoomMode')"
        @change="emit('mode', ($event.target as HTMLSelectElement).value as 'fitv' | 'fitw' | 'actual')"
      >
        <option v-for="value in ZOOM_MODES" :key="value" :value="value">
          {{ t(`design.zoomModeOption.${value}`) }}
        </option>
        <option v-if="mode === 'custom'" value="custom">{{ t('design.zoomModeOption.custom') }}</option>
      </select>
      <button
        type="button"
        class="dt-btn"
        :aria-label="t('design.zoomIn')"
        :disabled="scale >= 3"
        @click="emit('zoom', 1.25)"
      >
        ＋
      </button>
      <output class="dt-pct" :aria-label="t('design.scale')">{{ Math.round(scale * 100) }}%</output>
      <span class="dt-sep" />
      <button type="button" class="dt-btn dt-btn--text" :title="t('design.rotateHint')" @click="emit('rotate')">
        <i class="mdi mdi-rotate-right" aria-hidden="true" />{{ t('design.rotate') }}
      </button>
      <button
        type="button"
        class="dt-btn dt-btn--text"
        :class="{ 'is-on': adjust.flip }"
        :aria-pressed="adjust.flip"
        :title="t('design.flipHint')"
        @click="emit('flip')"
      >
        <i class="mdi mdi-flip-horizontal" aria-hidden="true" />{{ t('design.flip') }}
      </button>
      <button
        type="button"
        class="dt-btn dt-btn--text"
        :class="{ 'is-on': cropping }"
        :aria-pressed="cropping"
        :title="t('design.cropHint')"
        @click="emit('crop')"
      >
        <i class="mdi mdi-crop" aria-hidden="true" />{{ t('design.crop') }}
      </button>
      <button
        type="button"
        class="dt-btn dt-btn--text"
        :class="{ 'is-on': toneOpen }"
        :aria-expanded="toneOpen"
        :title="t('design.toneHint')"
        @click="emit('tone', !toneOpen)"
      >
        <i class="mdi mdi-tune-variant" aria-hidden="true" />{{ t('design.toneLabel') }}
      </button>
      <button
        type="button"
        class="dt-btn dt-btn--text"
        :disabled="!edited"
        :title="t('design.restoreHint')"
        @click="emit('restore')"
      >
        <i class="mdi mdi-backup-restore" aria-hidden="true" />{{ t('design.restore') }}
      </button>
    </div>
    <slot name="actions" />
  </div>
</template>

<style scoped>
.design-view-tools {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border-bottom: 1px solid var(--line);
}
/* 只让这一行横滚；右边那几样（在新窗口打开）是这一行的落点，滚出去就按不到了。 */
.design-view-tools__scroll {
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  align-items: center;
  gap: 4px;
  overflow-x: auto;
  scrollbar-width: none;
}
.design-view-tools__scroll::-webkit-scrollbar {
  display: none;
}
.dt-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 28px;
  padding: 0 8px;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: nowrap;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
  cursor: pointer;
  flex: none;
}
.dt-btn:hover:not(:disabled) {
  background: var(--fill-2);
}
.dt-btn:disabled {
  color: var(--faint);
  opacity: 0.45;
  cursor: default;
}
.dt-btn:focus-visible {
  outline: 2px solid var(--accent);
}
.dt-btn.is-on {
  color: var(--accent);
  background: var(--fill-2);
}
.dt-btn .mdi {
  font-size: 16px;
  line-height: 1;
}
.dt-select {
  height: 28px;
  padding: 0 6px;
  color: var(--text);
  font-size: 13px;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.dt-select:hover {
  background: var(--fill-2);
}
.dt-pct {
  min-width: 42px;
  color: var(--text);
  font-size: 13px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}
.dt-sep {
  flex: none;
  width: 1px;
  height: 16px;
  margin: 0 5px;
  background: var(--line-2);
}
</style>
