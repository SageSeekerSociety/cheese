<script setup lang="ts">
import type { RasterRegion } from './designRegion'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import DesignRasterRegion from './DesignRasterRegion.vue'

import { t } from '@/i18n'

const props = defineProps<{ src: string; alt: string; identity: string }>()
const emit = defineEmits<{ region: [region: RasterRegion] }>()
const pane = ref<HTMLElement | null>(null)
const image = ref<HTMLImageElement | null>(null)
const natural = ref({ width: 0, height: 0 })
const available = ref(0)
const zoom = ref(1)
const fitted = ref(true)
const selecting = ref(false)
const selectedRegion = ref<RasterRegion | null>(null)
const scale = computed(() =>
  fitted.value && natural.value.width
    ? Math.min(1, Math.max(0.1, (available.value - 32) / natural.value.width))
    : zoom.value
)
const dimensions = computed(() =>
  natural.value.width
    ? {
        width: `${natural.value.width * scale.value}px`,
        height: `${natural.value.height * scale.value}px`,
      }
    : {}
)
let observer: ResizeObserver | null = null
watch(
  pane,
  (element) => {
    observer?.disconnect()
    if (!element) return
    observer = new ResizeObserver(() => {
      available.value = element.clientWidth
    })
    observer.observe(element)
  },
  { flush: 'post' }
)
watch(
  [() => props.src, () => props.identity],
  () => {
    natural.value = { width: 0, height: 0 }
    selecting.value = false
    selectedRegion.value = null
    fitted.value = true
  },
  { flush: 'sync' }
)
function loaded() {
  if (image.value) natural.value = { width: image.value.naturalWidth, height: image.value.naturalHeight }
}
function setZoom(value: number) {
  zoom.value = Math.max(0.1, Math.min(3, value))
  fitted.value = false
}
function selected(region: RasterRegion) {
  selecting.value = false
  selectedRegion.value = region
  emit('region', region)
}
onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <section class="design-image">
    <div class="design-image__tools" role="group" :aria-label="t('design.viewportTools')">
      <button type="button" :aria-label="t('design.zoomOut')" :disabled="scale <= 0.1" @click="setZoom(scale / 1.25)">
        −
      </button>
      <output class="t-meta" :aria-label="t('design.scale')">{{ Math.round(scale * 100) }}%</output>
      <button type="button" :aria-label="t('design.zoomIn')" :disabled="scale >= 3" @click="setZoom(scale * 1.25)">
        +
      </button>
      <button type="button" :aria-pressed="fitted" @click="fitted = true">{{ t('design.fit') }}</button>
      <button type="button" :disabled="!natural.width" :aria-pressed="selecting" @click="selecting = !selecting">
        {{ t(selecting ? 'design.cancelRegion' : 'design.region') }}
      </button>
    </div>
    <output v-if="selectedRegion" class="t-meta" aria-live="polite">{{
      t('design.selectedRegion', selectedRegion)
    }}</output>
    <div ref="pane" class="design-image__pane" @scroll="selecting = false">
      <div class="design-image__sheet" :style="dimensions">
        <img ref="image" :src="src" :alt="alt" draggable="false" @load="loaded" />
        <DesignRasterRegion
          :image="image"
          :enabled="selecting"
          :identity="`${identity}:${scale}:${available}`"
          @select="selected"
          @cancel="selecting = false"
        />
      </div>
    </div>
  </section>
</template>

<style scoped>
.design-image {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
  min-height: 0;
}
.design-image__tools {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 8px;
  border-bottom: 1px solid var(--line);
}
.design-image__tools button {
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  line-height: var(--lh-13);
}
.design-image__tools button:hover:not(:disabled) {
  background: var(--fill-2);
}
.design-image__tools button:disabled {
  color: var(--faint);
}
.design-image__tools button:focus-visible {
  outline: 2px solid var(--accent);
}
.design-image__pane {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 16px;
}
.design-image__sheet {
  position: relative;
}
.design-image__sheet img {
  display: block;
  width: 100%;
  height: 100%;
}
</style>
