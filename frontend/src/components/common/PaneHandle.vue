<script setup lang="ts">
// 贴在右边那一栏左缘的拖把：拖动改宽度，左右键也能调，双击回到默认（见 `usePaneWidth`）。
// 样式照对话栏和工作面板之间那一条（`pane-resizer`）：平时是一条细线，悬停变深。
import type { usePaneWidth } from '@/composables/usePaneWidth'

import { t } from '@/i18n'

const props = defineProps<{ pane: ReturnType<typeof usePaneWidth> }>()
</script>

<template>
  <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- a focusable separator: pointer drag, arrow keys, double-click reset -->
  <div
    class="pane-handle"
    role="separator"
    aria-orientation="vertical"
    tabindex="0"
    :aria-valuenow="props.pane.percent.value"
    :aria-valuemin="props.pane.minPercent.value"
    :aria-valuemax="props.pane.maxPercent"
    :aria-label="t('work.topic.resize')"
    :title="t('work.topic.resize')"
    data-testid="pane-handle"
    @pointerdown="props.pane.startDrag"
    @keydown="props.pane.onKey"
    @dblclick="props.pane.reset()"
  />
</template>

<style scoped>
.pane-handle {
  position: absolute;
  top: 0;
  bottom: 0;
  left: -3px;
  z-index: var(--z-raised);
  width: 6px;
  cursor: col-resize;
  touch-action: none;
}

.pane-handle::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 2px;
  width: 2px;
  background: transparent;
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.pane-handle:hover::after,
.pane-handle:focus-visible::after {
  background: var(--faint);
}
</style>
