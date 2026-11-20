<script setup lang="ts">
/**
 * The narrow layout of a layout area: no cells, one panel at a time under a strip of
 * tabs. It only hands out the one host element; the area teleports the active panel
 * into it, so switching keeps every panel's instance.
 */
import type { LayoutPanelInfo } from './types'

import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import LayoutAddMenu from './LayoutAddMenu.vue'

const props = defineProps<{
  panels: LayoutPanelInfo[]
  active: string | undefined
  unplaced: LayoutPanelInfo[]
  locked: boolean
}>()

const emit = defineEmits<{
  select: [id: string]
  add: [id: string]
  host: [id: string, element: HTMLElement, attached: boolean]
}>()

const { t } = useI18n()
const body = ref<HTMLElement>()

onMounted(() => {
  if (props.active && body.value) emit('host', props.active, body.value, true)
})
watch(
  () => props.active,
  (next, previous) => {
    if (!body.value) return
    if (previous) emit('host', previous, body.value, false)
    if (next) emit('host', next, body.value, true)
  }
)
onBeforeUnmount(() => {
  if (props.active && body.value) emit('host', props.active, body.value, false)
})
</script>

<template>
  <div class="layout-stack">
    <div class="layout-stack__bar" role="tablist" :aria-label="t('shell.layout.tabs')">
      <button
        v-for="panel in panels"
        :key="panel.id"
        type="button"
        role="tab"
        class="layout-stack__tab"
        :class="{ 'layout-stack__tab--on': panel.id === active }"
        :aria-selected="panel.id === active"
        :data-layout-tab="panel.id"
        @click="emit('select', panel.id)"
      >
        <v-icon v-if="panel.icon" size="16" :icon="panel.icon" />
        <span>{{ panel.title }}</span>
      </button>
      <LayoutAddMenu v-if="unplaced.length && !locked" :panels="unplaced" @pick="emit('add', $event)" />
    </div>
    <div ref="body" class="layout-stack__body" role="tabpanel" />
  </div>
</template>

<style scoped>
.layout-stack {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-height: 0;
}

.layout-stack__bar {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 2px;
  padding: 0 6px;
  overflow-x: auto;
  border-bottom: 1px solid var(--line);
  scrollbar-width: none;
}

.layout-stack__bar::-webkit-scrollbar {
  display: none;
}

.layout-stack__tab {
  display: inline-flex;
  position: relative;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
  min-height: 44px;
  padding: 0 12px;
  font-size: 13px;
  color: var(--muted);
  white-space: nowrap;
  cursor: pointer;
  background: transparent;
  border: 0;
  transition: color var(--dur-quick) var(--ease-standard);
}

.layout-stack__tab:hover {
  color: var(--ink);
}

.layout-stack__tab--on {
  font-weight: 600;
  color: var(--ink);
}

.layout-stack__tab--on::after {
  position: absolute;
  right: 12px;
  bottom: -1px;
  left: 12px;
  height: 2px;
  content: '';
  background: var(--ink);
}

.layout-stack__body {
  flex: 1 1 auto;
  min-height: 0;
}
</style>
