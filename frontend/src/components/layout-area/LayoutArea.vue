<script setup lang="ts">
/**
 * A window-managed area: panels as tabs in cells the reader can split, resize, collapse,
 * maximize and rearrange, remembered per page in this browser. On narrow screens the
 * same panels stack, one at a time.
 *
 * The layer knows nothing about what a panel is. The page hands it panels (`LayoutPanel`)
 * and a default arrangement (`LayoutArrangement`); a panel's content is a plain component
 * (or the `panel` slot) that never learns it is inside a layout.
 *
 * The layer owns every panel's component instance and teleports it to wherever the panel
 * currently lives — a dockview cell, the narrow stack, or a hidden parking spot while its
 * cell is collapsed — so moving, collapsing, maximizing or crossing the breakpoint never
 * remounts it. dockview itself (~100 KB) is loaded only when a wide layout is first shown.
 */
import type { LayoutArrangement } from './arrangement'
import type { ScrollKeeper } from './keepScroll'
import type { LayoutPanel, LayoutPanelInfo } from './types'

import { computed, defineAsyncComponent, onBeforeUnmount, reactive, ref, shallowRef, watch } from 'vue'
import { useDisplay } from 'vuetify'

import {
  activatePanel,
  cellsOf,
  placedPanels,
  placePanel,
  reconcile,
  restoreArrangement,
  serializeArrangement,
  setCollapsed,
  unplacedPanels,
  withCellIds,
} from './arrangement'
import { keepScroll } from './keepScroll'
import LayoutStack from './LayoutStack.vue'

import { storageGet, storageSet } from '@/lib/browserStorage'

const LayoutDock = defineAsyncComponent(() => import('./LayoutDock.vue'))

const props = withDefaults(
  defineProps<{
    panels: LayoutPanel[]
    defaultArrangement: LayoutArrangement
    /** Where this page's arrangement is remembered in the browser. */
    storageKey: string
    /** Bump it when the page's panels or default change shape; older saves are then ignored. */
    version: number
    /** No dragging, splitting or closing. Collapse and maximize still work. */
    locked?: boolean
    /** Whether the dividers between cells can be dragged. */
    resizable?: boolean
    /** Stack the panels. Left out, the product's mobile breakpoint decides. */
    narrow?: boolean
  }>(),
  { locked: false, resizable: true, narrow: undefined }
)

defineSlots<{
  /** Content of a panel declared without `component`. */
  panel?: (scope: { id: string }) => unknown
}>()

const display = useDisplay()
const isNarrow = computed(() => props.narrow ?? !display.mdAndUp.value)

const declared = computed(() => props.panels.map((panel) => panel.id))
const byId = computed(() => new Map(props.panels.map((panel) => [panel.id, panel])))
const infos = computed<LayoutPanelInfo[]>(() =>
  props.panels.map(({ id, title, icon, closable, pinned, minWidth, alwaysRender }) => ({
    id,
    title,
    icon,
    closable,
    pinned,
    minWidth,
    alwaysRender,
  }))
)

function fromDefault(): LayoutArrangement {
  return withCellIds(reconcile(props.defaultArrangement, declared.value))
}

function savedArrangement(): LayoutArrangement | null {
  const saved = restoreArrangement(storageGet(props.storageKey), {
    version: props.version,
    declared: declared.value,
    pinned: props.panels.filter((panel) => panel.pinned).map((panel) => panel.id),
  })
  return saved && withCellIds(saved)
}

const arrangement = shallowRef<LayoutArrangement>(savedArrangement() ?? fromDefault())
/** Bumped when the arrangement is replaced from outside dockview, so the adapter reloads. */
const loadKey = ref(0)

function commit(next: LayoutArrangement, reload = false) {
  arrangement.value = withCellIds(reconcile(next, declared.value))
  storageSet(props.storageKey, serializeArrangement(arrangement.value, props.version))
  if (reload) loadKey.value++
}

const placed = computed(() => placedPanels(arrangement.value))
const unplaced = computed(() => unplacedPanels(arrangement.value, declared.value))
const unplacedInfos = computed(() => infos.value.filter((info) => unplaced.value.includes(info.id)))

// ---------------------------------------------------------------- instances

const parking = ref<HTMLElement>()
const hosts = reactive<Record<string, HTMLElement | undefined>>({})

function onHost(id: string, element: HTMLElement, attached: boolean) {
  if (attached) hosts[id] = element
  else if (hosts[id] === element) delete hosts[id]
}

const keepers = new Map<string, ScrollKeeper>()
function panelRoot(id: string) {
  return (element: unknown) => {
    if (element instanceof HTMLElement) {
      if (!keepers.has(id)) keepers.set(id, keepScroll(element))
    } else {
      keepers.get(id)?.dispose()
      keepers.delete(id)
    }
  }
}
function restoreScroll(id: string) {
  keepers.get(id)?.restore()
}
watch(
  () => placed.value.map((id) => hosts[id]),
  () => placed.value.forEach(restoreScroll),
  { flush: 'post' }
)
onBeforeUnmount(() => keepers.forEach((keeper) => keeper.dispose()))

// ---------------------------------------------------------------- narrow

const chosen = ref<string>()
const stackActive = computed(() => {
  if (chosen.value && placed.value.includes(chosen.value)) return chosen.value
  const pinned = new Set(props.panels.filter((panel) => panel.pinned).map((panel) => panel.id))
  const actives = cellsOf(arrangement.value).map((cell) => cell.active ?? cell.panels[0])
  return actives.find((id) => !pinned.has(id)) ?? placed.value[0]
})
const stackPanels = computed(() => placed.value.map((id) => infos.value.find((info) => info.id === id)!))

function selectInStack(id: string) {
  chosen.value = id
  commit(activatePanel(arrangement.value, id))
}

function addInStack(id: string) {
  commit(placePanel(arrangement.value, id, stackActive.value))
  chosen.value = id
}

// ---------------------------------------------------------------- page API

defineExpose({
  /** Back to the page's default arrangement, forgetting the saved one. */
  reset() {
    commit(fromDefault(), true)
  },
  /** Shows a panel: brings it back if it was closed, and makes it the visible tab. */
  open(id: string) {
    if (!byId.value.has(id)) return
    let next = activatePanel(placePanel(arrangement.value, id), id)
    const cell = cellsOf(next).find((c) => c.panels.includes(id))
    if (cell?.collapsed && cell.id) next = setCollapsed(next, cell.id, false)
    chosen.value = id
    commit(next, true)
  },
})
</script>

<template>
  <div class="layout-area">
    <LayoutDock
      v-if="!isNarrow"
      :arrangement="arrangement"
      :load-key="loadKey"
      :panels="infos"
      :unplaced="unplacedInfos"
      :locked="locked"
      :resizable="resizable"
      @change="commit"
      @host="onHost"
      @shown="restoreScroll"
    />
    <LayoutStack
      v-else
      :panels="stackPanels"
      :active="stackActive"
      :unplaced="unplacedInfos"
      :locked="locked"
      @select="selectInStack"
      @add="addInStack"
      @host="onHost"
    />

    <div ref="parking" class="layout-area__parking" aria-hidden="true" />
    <template v-if="parking">
      <Teleport v-for="id in placed" :key="id" :to="hosts[id] ?? parking" defer>
        <div :ref="panelRoot(id)" class="layout-area__panel">
          <component :is="byId.get(id)!.component" v-if="byId.get(id)?.component" v-bind="byId.get(id)!.props ?? {}" />
          <slot v-else :id="id" name="panel" />
        </div>
      </Teleport>
    </template>
  </div>
</template>

<style scoped>
.layout-area {
  display: flex;
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
  color: var(--text);
  background: var(--surface);
}

.layout-area__parking {
  display: none;
}

.layout-area__panel {
  width: 100%;
  height: 100%;
  overflow: hidden;
}
</style>
