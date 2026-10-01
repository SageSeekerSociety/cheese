<script setup lang="ts">
import type { DesignDevice } from '@/composables/useDesignViewport'

import { t } from '@/i18n'

const props = defineProps<{ device: DesignDevice; scale: number; fitted: boolean }>()
const emit = defineEmits<{ device: [value: DesignDevice]; zoom: [value: number]; fit: [] }>()
const devices: DesignDevice[] = ['desktop', 'tablet', 'mobile']
function select(event: Event) {
  emit('device', (event.target as HTMLSelectElement).value as DesignDevice)
}
</script>

<template>
  <div class="design-toolbar" role="group" :aria-label="t('design.viewportTools')">
    <label class="t-meta">
      {{ t('design.viewport') }}
      <select :value="device" @change="select">
        <option v-for="value in devices" :key="value" :value="value">{{ t(`design.${value}`) }}</option>
      </select>
    </label>
    <button
      type="button"
      :aria-label="t('design.zoomOut')"
      :disabled="scale <= 0.1"
      @click="emit('zoom', props.scale / 1.25)"
    >
      −
    </button>
    <output class="t-meta" :aria-label="t('design.scale')">{{ Math.round(scale * 100) }}%</output>
    <button
      type="button"
      :aria-label="t('design.zoomIn')"
      :disabled="scale >= 3"
      @click="emit('zoom', props.scale * 1.25)"
    >
      +
    </button>
    <button type="button" :aria-pressed="fitted" @click="emit('fit')">{{ t('design.fit') }}</button>
  </div>
</template>

<style scoped>
.design-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 8px;
  background: var(--canvas);
  color: var(--text);
  border-bottom: 1px solid var(--line);
}
.design-toolbar select {
  padding: 4px 8px;
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  font-size: 13px;
}
.design-toolbar button {
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  line-height: var(--lh-13);
}
.design-toolbar button:hover:not(:disabled) {
  background: var(--fill-2);
}
.design-toolbar button:disabled {
  color: var(--faint);
}
.design-toolbar button:focus-visible,
.design-toolbar select:focus-visible {
  outline: 2px solid var(--accent);
}
</style>
