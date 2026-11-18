<script setup lang="ts">
/**
 * The wide layout drawn without dockview, for the moment before dockview's code has
 * arrived: the same cells at the same sizes, with the same dividers, but nothing can be
 * dragged yet. It is small and part of the main bundle, so a page laid out by the layer
 * paints in its final shape on the first frame; dockview then takes over that geometry.
 *
 * Like the dock it renders no panel content, only one empty host per visible panel.
 */
import type { LayoutArrangement, LayoutCell, SplitDirection } from './arrangement'
import type { CellSizing } from './dockviewJson'
import type { LayoutPanelInfo } from './types'

import { computed, onBeforeUnmount } from 'vue'

import LayoutStaticNode from './LayoutStaticNode.vue'

const props = defineProps<{
  arrangement: LayoutArrangement
  panels: LayoutPanelInfo[]
  sizing: (cell: LayoutCell, along: SplitDirection) => CellSizing
}>()

const emit = defineEmits<{
  host: [id: string, element: HTMLElement, attached: boolean]
}>()

const info = computed(() => new Map(props.panels.map((panel) => [panel.id, panel])))

const hostElements = new Map<string, HTMLElement>()
const hostRefs = new Map<string, (element: unknown) => void>()
function hostRef(id: string) {
  let fn = hostRefs.get(id)
  if (!fn) {
    fn = (element: unknown) => {
      const previous = hostElements.get(id)
      if (element instanceof HTMLElement) {
        if (previous === element) return
        hostElements.set(id, element)
        emit('host', id, element, true)
      } else if (previous) {
        hostElements.delete(id)
        emit('host', id, previous, false)
      }
    }
    hostRefs.set(id, fn)
  }
  return fn
}
onBeforeUnmount(() => hostElements.forEach((element, id) => emit('host', id, element, false)))
</script>

<template>
  <div class="layout-static">
    <LayoutStaticNode
      v-if="arrangement.root"
      :node="arrangement.root"
      :info="info"
      :sizing="sizing"
      :host-ref="hostRef"
    />
  </div>
</template>

<style>
/* Not scoped: the nodes are a recursive component of their own. */
.layout-static {
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  height: 100%;
}

.layout-static__split {
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
}

.layout-static__split--column {
  flex-direction: column;
}

.layout-static__slot {
  display: flex;
  position: relative;
  min-width: 0;
  min-height: 0;
}

/* The divider dockview draws between two cells (.dv-view::before), at the same place and
   taking its own pixel, as LayoutDock's theme has it. */
.layout-static__split--row > .layout-static__slot--divided {
  padding-left: 1px;
}

.layout-static__split--column > .layout-static__slot--divided {
  padding-top: 1px;
}

.layout-static__slot--divided::before {
  position: absolute;
  top: 0;
  left: 0;
  z-index: 5;
  pointer-events: none;
  content: '';
  background-color: var(--line);
}

.layout-static__split--row > .layout-static__slot--divided::before {
  width: 1px;
  height: 100%;
}

.layout-static__split--column > .layout-static__slot--divided::before {
  width: 100%;
  height: 1px;
}

.layout-static__cell {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  background: var(--surface);
}

.layout-static__tabs {
  display: flex;
  flex: 0 0 40px;
  align-items: center;
  padding: 0 6px;
  font-size: 13px;
  color: var(--muted);
  border-bottom: 1px solid var(--line);
}

.layout-static__tab {
  padding: 0 12px;
  white-space: nowrap;
}

.layout-static__tab--on {
  font-weight: 600;
  color: var(--ink);
}

.layout-static__host {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
}

.layout-static__cell--fit > .layout-static__host {
  flex: 0 0 auto;
}
</style>
