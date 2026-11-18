<script setup lang="ts">
/** One node of the static wide layout (`LayoutStatic`): a cell with its host, or a split. */
import type { LayoutCell, LayoutNode, SplitDirection } from './arrangement'
import type { CellSizing } from './dockviewJson'
import type { LayoutPanelInfo } from './types'

import { computed } from 'vue'

import { isSplit } from './arrangement'
import { STRIP_SIZE } from './dockviewJson'

const props = defineProps<{
  node: LayoutNode
  info: Map<string, LayoutPanelInfo>
  sizing: (cell: LayoutCell, along: SplitDirection) => CellSizing
  hostRef: (id: string) => (element: unknown) => void
}>()

const cell = computed(() => (isSplit(props.node) ? null : props.node))
const split = computed(() => (isSplit(props.node) ? props.node : null))
const fits = (node: LayoutNode) => !isSplit(node) && node.panels.some((id) => props.info.get(id)?.fitHeight === true)
const bare = computed(() => !!cell.value && cell.value.panels.every((id) => props.info.get(id)?.bare === true))
const active = computed(() => cell.value && (cell.value.active ?? cell.value.panels[0]))

const hiddenAt = (node: LayoutNode, along: SplitDirection) =>
  !isSplit(node) && props.sizing(node, along).hidden === true

function weight(node: LayoutNode): number {
  return typeof node.size === 'number' && node.size > 0 ? node.size : 1
}

/** Each child's flex, whether it is out of sight, and whether a divider precedes it. */
const children = computed(() => {
  const s = split.value
  if (!s) return []
  return s.children.map((child, i) => {
    const own = isSplit(child) ? {} : props.sizing(child, s.direction)
    let flex: string
    if (!isSplit(child) && child.collapsed) flex = `0 0 ${STRIP_SIZE}px`
    else if (fits(child) && s.direction === 'column') flex = '0 0 auto'
    else if (own.fixed !== undefined) flex = `0 0 ${own.fixed}px`
    else flex = `${weight(child)} 1 0`
    return {
      key: isSplit(child) ? `split-${i}` : child.id ?? `cell-${i}`,
      node: child,
      flex,
      hidden: own.hidden === true,
      divided: i > 0 && !fits(child) && !fits(s.children[i - 1]) && !hiddenAt(s.children[i - 1], s.direction),
    }
  })
})
</script>

<template>
  <div v-if="split" class="layout-static__split" :class="`layout-static__split--${split.direction}`">
    <div
      v-for="child in children"
      v-show="!child.hidden"
      :key="child.key"
      class="layout-static__slot"
      :class="{ 'layout-static__slot--divided': child.divided }"
      :style="{ flex: child.flex }"
    >
      <LayoutStaticNode :node="child.node" :info="info" :sizing="sizing" :host-ref="hostRef" />
    </div>
  </div>
  <div v-else-if="cell" class="layout-static__cell" :class="{ 'layout-static__cell--fit': fits(cell) }">
    <template v-if="!cell.collapsed">
      <div v-if="!bare" class="layout-static__tabs">
        <div
          v-for="id in cell.panels"
          :key="id"
          class="layout-static__tab"
          :class="{ 'layout-static__tab--on': id === active }"
        >
          {{ info.get(id)?.title ?? id }}
        </div>
      </div>
      <div :key="active!" :ref="hostRef(active!)" class="layout-static__host" :data-layout-host="active" />
    </template>
  </div>
</template>
