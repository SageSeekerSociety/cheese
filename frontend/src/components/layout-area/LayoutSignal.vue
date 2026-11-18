<script setup lang="ts">
/**
 * The mark on a layout tab (`LayoutPanel.signal`), drawn as the work panel's own tab strip
 * drew it: a breathing green dot for something happening, an amber dot for something new
 * to look at, a number for how many, amber only while it is news.
 */
import type { LayoutPanelSignal } from './types'

defineProps<{ signal?: LayoutPanelSignal }>()
</script>

<template>
  <span v-if="signal?.kind === 'pulse'" class="layout-signal layout-signal--pulse" />
  <span v-else-if="signal?.kind === 'dot'" class="layout-signal layout-signal--dot" />
  <span
    v-else-if="signal?.kind === 'count' && signal.count"
    class="layout-signal__count"
    :class="{ 'layout-signal__count--new': signal.fresh }"
    >{{ signal.count }}</span
  >
</template>

<style scoped>
.layout-signal {
  flex: 0 0 auto;
  width: 6px;
  height: 6px;
  border-radius: var(--radius-pill);
}

.layout-signal--dot {
  background: var(--accent);
}

.layout-signal--pulse {
  background: var(--ok);
  animation: layout-signal-breathe 1.6s ease-in-out infinite;
}

@keyframes layout-signal-breathe {
  0%,
  100% {
    opacity: 1;
  }

  50% {
    opacity: 0.35;
  }
}

@media (prefers-reduced-motion: reduce) {
  .layout-signal--pulse {
    animation: none;
  }
}

.layout-signal__count {
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--faint);
}

.layout-signal__count--new {
  color: var(--accent);
}
</style>
