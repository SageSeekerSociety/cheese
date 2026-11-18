<script setup lang="ts">
/**
 * A window-managed area: panels as tabs in cells the reader can split, resize, collapse,
 * maximize and rearrange, remembered per page in this browser. On narrow screens the
 * same panels stack, one at a time.
 *
 * The layer knows nothing about what a panel is. The page hands it panels (`LayoutPanel`)
 * and a default arrangement (`LayoutArrangement`); a panel's content is a plain component
 * (or the `panel` slot) that never learns it is inside a layout. The panels, the default
 * and the storage key may change while the area stays mounted: a new key brings that
 * page's arrangement, and the panels both pages declare keep their instances.
 *
 * The layer owns every panel's component instance and teleports it to wherever the panel
 * currently lives — a dockview cell, the narrow stack, or a hidden parking spot while its
 * cell is collapsed — so moving, collapsing, maximizing or crossing the breakpoint never
 * remounts it. dockview itself (~100 KB) is loaded only when a wide layout is first shown;
 * until it arrives `LayoutStatic` draws the same cells, so the page does not shift.
 */
import type { Component } from 'vue'
import type { LayoutArrangement, LayoutCell, SplitDirection } from './arrangement'
import type { CellSizing } from './dockviewJson'
import type { ScrollKeeper } from './keepScroll'
import type { LayoutPanel, LayoutPanelInfo } from './types'

import { computed, markRaw, onBeforeUnmount, reactive, ref, shallowRef, watch } from 'vue'
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
import LayoutStatic from './LayoutStatic.vue'
import { clampWidth, readWidth, rememberWidth } from './widths'

import { storageGet, storageSet } from '@/lib/browserStorage'

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
    /** The panel whose cell fills the wide area (`v-model:maximized`); not remembered. */
    maximized?: string | null
  }>(),
  { locked: false, resizable: true, narrow: undefined, maximized: null }
)

const emit = defineEmits<{
  'update:maximized': [id: string | null]
}>()

defineSlots<{
  /** Content of a panel declared without `component`. */
  panel?: (scope: { id: string }) => unknown
}>()

const display = useDisplay()
const isNarrow = computed(() => props.narrow ?? !display.mdAndUp.value)

const declared = computed(() => props.panels.map((panel) => panel.id))
const byId = computed(() => new Map(props.panels.map((panel) => [panel.id, panel])))
const infos = computed<LayoutPanelInfo[]>(() =>
  props.panels.map((panel) => {
    const info: LayoutPanelInfo & Partial<Pick<LayoutPanel, 'component' | 'props'>> = { ...panel }
    delete info.component
    delete info.props
    return info
  })
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

// Another page's arrangement: its key, or the panels themselves, changed.
watch(
  () => [props.storageKey, props.version, declared.value.join('\n')],
  () => {
    arrangement.value = savedArrangement() ?? fromDefault()
    if (maximizedNow.value) setMaximized(null)
    loadKey.value++
  }
)

const placed = computed(() => placedPanels(arrangement.value))
const unplaced = computed(() => unplacedPanels(arrangement.value, declared.value))
const unplacedInfos = computed(() => infos.value.filter((info) => unplaced.value.includes(info.id)))

// ---------------------------------------------------------------- sizes

/** The width each panel with a width of its own keeps, remembered under its key. */
const widths = reactive<Record<string, number>>({})
watch(
  () => props.panels,
  (panels) => {
    for (const panel of panels) {
      if (panel.width && widths[panel.id] === undefined) widths[panel.id] = readWidth(panel.width)
    }
  },
  { immediate: true }
)
function widthOf(panel: LayoutPanel): number {
  return clampWidth(panel.width!, widths[panel.id] ?? panel.width!.initial)
}
function onResized(id: string, width: number) {
  const spec = byId.value.get(id)?.width
  if (spec) widths[id] = rememberWidth(spec, width)
}

/** The content height of each `fitHeight` panel, as it is laid out now. */
const fitHeights = reactive<Record<string, number>>({})
const fitObserved = new Map<Element, string>()
const fitObserver =
  typeof ResizeObserver === 'undefined'
    ? null
    : new ResizeObserver((entries) => {
        for (const entry of entries) {
          const id = fitObserved.get(entry.target)
          if (!id) continue
          const height = Math.round(entry.borderBoxSize?.[0]?.blockSize ?? (entry.target as HTMLElement).offsetHeight)
          if (height > 0 && fitHeights[id] !== height) fitHeights[id] = height
        }
      })

function sizing(cell: LayoutCell, along: SplitDirection): CellSizing {
  const panels = cell.panels.map((id) => byId.value.get(id)).filter((p): p is LayoutPanel => !!p)
  const out: CellSizing = {}
  const sized = panels.find((panel) => panel.width)
  if (along === 'row' && sized) out.fixed = widthOf(sized)
  const fit = panels.find((panel) => panel.fitHeight)
  if (along === 'column' && fit) out.fixed = fitHeights[fit.id] ?? 0
  if (panels.length && panels.every((panel) => panel.hidden)) out.hidden = true
  return out
}

// ---------------------------------------------------------------- instances

const parking = ref<HTMLElement>()
const staticHosts = reactive<Record<string, HTMLElement | undefined>>({})
const dockHosts = reactive<Record<string, HTMLElement | undefined>>({})
const stackHosts = reactive<Record<string, HTMLElement | undefined>>({})

function hostSetter(hosts: Record<string, HTMLElement | undefined>) {
  return (id: string, element: HTMLElement, attached: boolean) => {
    if (attached) hosts[id] = element
    else if (hosts[id] === element) delete hosts[id]
  }
}
const onStaticHost = hostSetter(staticHosts)
const onDockHost = hostSetter(dockHosts)
const onStackHost = hostSetter(stackHosts)

const keepers = new Map<string, ScrollKeeper>()
const panelRoots = new Map<string, (element: unknown) => void>()
function panelRoot(id: string) {
  let fn = panelRoots.get(id)
  if (!fn) {
    let current: HTMLElement | null = null
    fn = (element: unknown) => {
      if (element instanceof HTMLElement) {
        if (current === element) return
        current = element
        if (!keepers.has(id)) keepers.set(id, keepScroll(element))
        fitObserved.set(element, id)
        fitObserver?.observe(element)
      } else {
        keepers.get(id)?.dispose()
        keepers.delete(id)
        if (current) {
          fitObserver?.unobserve(current)
          fitObserved.delete(current)
        }
        current = null
      }
    }
    panelRoots.set(id, fn)
  }
  return fn
}
function restoreScroll(id: string) {
  keepers.get(id)?.restore()
}
onBeforeUnmount(() => {
  keepers.forEach((keeper) => keeper.dispose())
  fitObserver?.disconnect()
})

// ---------------------------------------------------------------- wide

/** dockview's adapter, once its chunk has arrived. */
const Dock = shallowRef<Component>()
/** It has laid out its first arrangement and handed out a host for every visible cell. */
const dockLoaded = ref(false)
watch(
  isNarrow,
  (narrow) => {
    if (narrow || Dock.value) return
    void import('./LayoutDock.vue').then((module) => (Dock.value = markRaw(module.default)))
  },
  { immediate: true }
)
const visibleActives = computed(() =>
  cellsOf(arrangement.value)
    .filter((cell) => !sizing(cell, 'row').hidden)
    .map((cell) => cell.active ?? cell.panels[0])
)
/** Latched: once dockview has taken over, a later reload goes through dockview alone. */
const dockReady = ref(false)
watch(
  () => dockLoaded.value && visibleActives.value.every((id) => dockHosts[id]),
  (ready) => ready && (dockReady.value = true)
)

const maximizedNow = ref<string | null>(props.maximized)
watch(
  () => props.maximized,
  (id) => (maximizedNow.value = id)
)
function setMaximized(id: string | null) {
  if (maximizedNow.value === id) return
  maximizedNow.value = id
  emit('update:maximized', id)
}

// ---------------------------------------------------------------- narrow

const chosen = ref<string>()
const stackPlaced = computed(() => placed.value.filter((id) => !byId.value.get(id)?.hidden))
const stackActive = computed(() => {
  if (chosen.value && stackPlaced.value.includes(chosen.value)) return chosen.value
  const pinned = new Set(props.panels.filter((panel) => panel.pinned).map((panel) => panel.id))
  const actives = cellsOf(arrangement.value)
    .map((cell) => cell.active ?? cell.panels[0])
    .filter((id) => stackPlaced.value.includes(id))
  return actives.find((id) => !pinned.has(id)) ?? stackPlaced.value[0]
})
const stackPanels = computed(() => stackPlaced.value.map((id) => infos.value.find((info) => info.id === id)!))

function selectInStack(id: string) {
  chosen.value = id
  commit(activatePanel(arrangement.value, id))
}

function addInStack(id: string) {
  commit(placePanel(arrangement.value, id, stackActive.value))
  chosen.value = id
}

// ---------------------------------------------------------------- where each panel lives

const hosts = computed<Record<string, HTMLElement | undefined>>(() => {
  if (isNarrow.value) return stackHosts
  return dockReady.value ? dockHosts : staticHosts
})
watch(
  () => placed.value.map((id) => hosts.value[id]),
  () => placed.value.forEach(restoreScroll),
  { flush: 'post' }
)

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
    <template v-if="!isNarrow">
      <LayoutStatic
        v-if="!dockReady"
        :arrangement="arrangement"
        :panels="infos"
        :sizing="sizing"
        @host="onStaticHost"
      />
      <div v-if="Dock" class="layout-area__dock" :class="{ 'layout-area__dock--pending': !dockReady }">
        <component
          :is="Dock"
          :arrangement="arrangement"
          :load-key="loadKey"
          :panels="infos"
          :unplaced="unplacedInfos"
          :locked="locked"
          :resizable="resizable"
          :sizing="sizing"
          :fit-heights="fitHeights"
          :maximized="maximizedNow"
          @change="commit"
          @host="onDockHost"
          @shown="restoreScroll"
          @ready="dockLoaded = true"
          @resized="onResized"
          @maximized="setMaximized"
        />
      </div>
    </template>
    <LayoutStack
      v-else
      :panels="stackPanels"
      :active="stackActive"
      :unplaced="unplacedInfos"
      :locked="locked"
      @select="selectInStack"
      @add="addInStack"
      @host="onStackHost"
    />

    <div ref="parking" class="layout-area__parking" aria-hidden="true" />
    <template v-if="parking">
      <Teleport v-for="id in placed" :key="id" :to="hosts[id] ?? parking" defer>
        <div
          :ref="panelRoot(id)"
          class="layout-area__panel"
          :class="{ 'layout-area__panel--fit': byId.get(id)?.fitHeight }"
        >
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

.layout-area__dock {
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
}

/* Laid out under the static cells until it has a host for each of them. */
.layout-area__dock--pending {
  position: absolute;
  inset: 0;
  visibility: hidden;
}

.layout-area__parking {
  display: none;
}

.layout-area__panel {
  width: 100%;
  height: 100%;
  overflow: hidden;
}

.layout-area__panel--fit {
  height: auto;
}
</style>
