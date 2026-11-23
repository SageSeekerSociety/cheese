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
import { useI18n } from 'vue-i18n'
import { useDisplay } from 'vuetify'

import {
  activatePanel,
  cellOfPanel,
  cellsOf,
  placedPanels,
  placePanel,
  placeTransient,
  reconcile,
  restoreArrangement,
  sameArrangement,
  serializeArrangement,
  setCollapsed,
  unplacedPanels,
  withCellIds,
} from './arrangement'
import { cellSizing } from './dockviewJson'
import { keepScroll } from './keepScroll'
import LayoutStack from './LayoutStack.vue'
import LayoutStatic from './LayoutStatic.vue'
import { rememberedFold, rememberedWidth, rememberFold, rememberWidth } from './widths'

import BaseButton from '@/components/base/BaseButton.vue'
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
    /** Height of the cells' tab strips (a CSS length), to line them up with a header beside them. */
    tabStripHeight?: string
  }>(),
  { locked: false, resizable: true, narrow: undefined, maximized: null, tabStripHeight: '40px' }
)

const emit = defineEmits<{
  'update:maximized': [id: string | null]
  /** The reader picked this panel's tab (or dragged it), in whatever cell. */
  activated: [id: string]
  /** The panels on screen now: the visible tab of every cell in sight. */
  visible: [ids: string[]]
  /** The reader closed the tab of a transient panel: the page drops it. */
  close: [id: string]
  /** The reader double-clicked the tab of a temporary panel: the page keeps it. */
  keep: [id: string]
  /** The reader picked a panel that spawns others under "+": the page opens a new one. */
  spawn: [id: string]
}>()

defineSlots<{
  /** Content of a panel declared without `component`. */
  panel?: (scope: { id: string }) => unknown
  /** What a titled panel's title bar says, before its buttons. Without it, the panel's title. */
  head?: (scope: { id: string }) => unknown
}>()

const { t } = useI18n()
const display = useDisplay()
const isNarrow = computed(() => props.narrow ?? !display.mdAndUp.value)

// A panel that spawns others is never placed: it only stands under "+" (`spawns`).
const placeable = computed(() => props.panels.filter((panel) => !panel.spawns))
const spawners = computed<LayoutPanelInfo[]>(() =>
  props.panels
    .filter((panel) => panel.spawns)
    .map((panel) => ({
      id: panel.id,
      title: panel.title,
      icon: panel.icon,
      group: panel.group,
      beside: panel.beside,
      below: panel.below,
      spawns: true,
    }))
)
const declared = computed(() => placeable.value.map((panel) => panel.id))
const byId = computed(() => new Map(placeable.value.map((panel) => [panel.id, panel])))
const infos = computed<LayoutPanelInfo[]>(() =>
  placeable.value.map((panel) => {
    const info: LayoutPanelInfo & Partial<Pick<LayoutPanel, 'component' | 'props'>> = { ...panel }
    delete info.component
    delete info.props
    return info
  })
)

const groupOf = (id: string) => byId.value.get(id)?.group
const transient = computed(() => new Set(placeable.value.filter((panel) => panel.transient).map((panel) => panel.id)))

/** Transient panels that are not placed yet go to their group's cell, or beside or below the panel they name. */
function placeTransientPanel(arrangement: LayoutArrangement, id: string): LayoutArrangement {
  const panel = byId.value.get(id)
  return panel ? placeTransient(arrangement, panel, groupOf) : arrangement
}
function withTransients(arrangement: LayoutArrangement, ids: readonly string[]): LayoutArrangement {
  return ids.reduce((next, id) => (transient.value.has(id) ? placeTransientPanel(next, id) : next), arrangement)
}

/** A panel whose fold is remembered on its own gets that fold, whatever the arrangement says. */
function withFolds(arrangement: LayoutArrangement): LayoutArrangement {
  let next = arrangement
  for (const panel of placeable.value) {
    if (!panel.foldKey) continue
    const cell = cellOfPanel(next, panel.id)
    const folded = rememberedFold(panel.foldKey).value
    if (cell?.id && !!cell.collapsed !== folded) next = setCollapsed(next, cell.id, folded)
  }
  return next
}

function fromDefault(): LayoutArrangement {
  return withCellIds(reconcile(props.defaultArrangement, declared.value))
}

function savedArrangement(): LayoutArrangement | null {
  const saved = restoreArrangement(storageGet(props.storageKey), {
    version: props.version,
    declared: declared.value.filter((id) => !transient.value.has(id)),
    required: placeable.value.filter((panel) => panel.pinned || !panel.closable).map((panel) => panel.id),
  })
  return saved && withCellIds(reconcile(saved, declared.value))
}

function opening(): LayoutArrangement {
  const base = savedArrangement() ?? fromDefault()
  return withFolds(withCellIds(withTransients(base, declared.value)))
}

const arrangement = shallowRef<LayoutArrangement>(opening())
/** Bumped when the arrangement is replaced from outside dockview, so the adapter reloads. */
const loadKey = ref(0)

function commit(next: LayoutArrangement, reload = false) {
  arrangement.value = withCellIds(reconcile(next, declared.value))
  // Transient panels are the page's business: what is remembered is the rest.
  const lasting = declared.value.filter((id) => !transient.value.has(id))
  storageSet(props.storageKey, serializeArrangement(reconcile(arrangement.value, lasting), props.version))
  for (const panel of placeable.value) {
    const cell = panel.foldKey ? cellOfPanel(arrangement.value, panel.id) : undefined
    if (cell) rememberFold(panel.foldKey!, !!cell.collapsed)
  }
  if (reload) loadKey.value++
}

// Another page's arrangement (its key changed) is loaded afresh; panels that come and go
// on the same page are placed or dropped where they are, without reloading the rest.
watch(
  () => [props.storageKey, props.version, declared.value.join('\n')] as const,
  ([key, version], [keyBefore, versionBefore, declaredBefore]) => {
    if (key !== keyBefore || version !== versionBefore) {
      arrangement.value = opening()
      if (maximizedNow.value) setMaximized(null)
      loadKey.value++
      return
    }
    const before = new Set(declaredBefore.split('\n'))
    commit(
      withTransients(
        arrangement.value,
        declared.value.filter((id) => !before.has(id))
      )
    )
  }
)

const placed = computed(() => placedPanels(arrangement.value))
const unplaced = computed(() =>
  unplacedPanels(arrangement.value, declared.value).filter((id) => !transient.value.has(id))
)
const unplacedInfos = computed(() => [
  ...infos.value.filter((info) => unplaced.value.includes(info.id)),
  ...spawners.value,
])

// ---------------------------------------------------------------- sizes

/** The width (or height) each panel with one of its own keeps, live and shared under its key. */
function extentOf(id: string, axis: 'width' | 'height'): number {
  const spec = byId.value.get(id)?.[axis]
  return spec ? rememberedWidth(spec).value : 0
}
const fixedWidths = computed<Record<string, number>>(() =>
  Object.fromEntries(
    placeable.value.filter((panel) => panel.width).map((panel) => [panel.id, extentOf(panel.id, 'width')])
  )
)
const fixedHeights = computed<Record<string, number>>(() =>
  Object.fromEntries(
    placeable.value.filter((panel) => panel.height).map((panel) => [panel.id, extentOf(panel.id, 'height')])
  )
)
function onResized(id: string, size: number, axis: 'width' | 'height') {
  const spec = byId.value.get(id)?.[axis]
  if (spec) rememberWidth(spec, size)
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
  return cellSizing(panels, along, extentOf, fitHeights)
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
    // A folded cell shows its strip, not a host: nothing to wait for there.
    .filter((cell) => !sizing(cell, 'row').hidden && !cell.collapsed)
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

// ---------------------------------------------------------------- title bars

/** The cell of this panel is the one that fills the area. */
function fills(id: string): boolean {
  const big = maximizedNow.value
  return !!big && !!cellOfPanel(arrangement.value, id)?.panels.includes(big)
}
/** More than one cell in sight: there is something to give this panel's cell the room of. */
const severalCells = computed(
  () => cellsOf(arrangement.value).filter((cell) => !sizing(cell, 'row').hidden && !hidesTabsOf(cell)).length > 1
)
function hidesTabsOf(cell: LayoutCell): boolean {
  return cell.panels.every((id) => byId.value.get(id)?.bare)
}
/** While this panel's cell fills the area: a panel out of sight carries a mark. */
function signalledElsewhere(id: string): boolean {
  const inSight = new Set(cellOfPanel(arrangement.value, id)?.panels ?? [])
  return placeable.value.some((panel) => panel.signal && !inSight.has(panel.id))
}

// ---------------------------------------------------------------- narrow

const chosen = ref<string>()
const stackPlaced = computed(() => placed.value.filter((id) => !byId.value.get(id)?.hidden))
const stackActive = computed(() => {
  if (chosen.value && stackPlaced.value.includes(chosen.value)) return chosen.value
  const pinned = new Set(placeable.value.filter((panel) => panel.pinned).map((panel) => panel.id))
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
  if (spawners.value.some((panel) => panel.id === id)) return emit('spawn', id)
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

/** Asks dockview to act on a panel without reloading the layout; counted so a repeat still acts. */
const request = shallowRef<{ id: string; fold?: boolean; n: number } | null>(null)
let requests = 0

/**
 * The page's default arrangement as it would open now: open tasks and files in their
 * group's cell, and a panel whose fold is remembered on its own (the sidebar) as it is,
 * since that fold belongs to every page.
 */
function defaultNow(): LayoutArrangement {
  return withFolds(withCellIds(withTransients(fromDefault(), declared.value)))
}

/** The reader has moved something away from the page's default arrangement. */
const rearranged = computed(() => {
  const lasting = declared.value.filter((id) => !transient.value.has(id))
  return !sameArrangement(reconcile(arrangement.value, lasting), reconcile(defaultNow(), lasting))
})

/** Back to the page's default arrangement, forgetting the saved one. */
function reset() {
  commit(defaultNow(), true)
}

/** A panel asked for before the page declared it: shown as soon as it is. */
let pendingOpen: string | null = null
watch(declared, (ids) => {
  if (pendingOpen && ids.includes(pendingOpen)) open(pendingOpen)
})

/** Shows a panel: brings it back if it was closed, and makes it the visible tab of its cell. */
function open(id: string) {
  if (!byId.value.has(id)) {
    pendingOpen = id
    return
  }
  pendingOpen = null
  chosen.value = id
  if (!placed.value.includes(id)) {
    commit(transient.value.has(id) ? placeTransientPanel(arrangement.value, id) : placePanel(arrangement.value, id))
  }
  if (isNarrow.value || !dockReady.value) {
    let next = activatePanel(arrangement.value, id)
    const cell = cellOfPanel(next, id)
    if (cell?.collapsed && cell.id) next = setCollapsed(next, cell.id, false)
    commit(next, !isNarrow.value)
    return
  }
  request.value = { id, n: ++requests }
}

/** Folds the cell of this panel to a strip, or opens it again. */
function fold(id: string, folded: boolean) {
  const cell = cellOfPanel(arrangement.value, id)
  if (!cell?.id) return
  if (isNarrow.value || !dockReady.value) {
    commit(setCollapsed(arrangement.value, cell.id, folded), !isNarrow.value)
    return
  }
  request.value = { id, fold: folded, n: ++requests }
}

defineExpose({ reset, open, fold, rearranged })
</script>

<template>
  <div class="layout-area" :style="{ '--layout-tab-strip-height': tabStripHeight }">
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
          :fixed-widths="fixedWidths"
          :fixed-heights="fixedHeights"
          :default-arrangement="defaultArrangement"
          :maximized="maximizedNow"
          :request="request"
          @change="commit"
          @host="onDockHost"
          @shown="restoreScroll"
          @ready="dockLoaded = true"
          @spawn="emit('spawn', $event)"
          @resized="onResized"
          @maximized="setMaximized"
          @activated="emit('activated', $event)"
          @visible="emit('visible', $event)"
          @close="emit('close', $event)"
          @keep="emit('keep', $event)"
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
          :class="{
            'layout-area__panel--fit': byId.get(id)?.fitHeight,
            'layout-area__panel--titled': byId.get(id)?.titled,
          }"
        >
          <div v-if="byId.get(id)?.titled" class="layout-head" :data-layout-head="id">
            <div class="layout-head__main">
              <slot :id="id" name="head">
                <span class="layout-head__title">{{ byId.get(id)?.title ?? id }}</span>
              </slot>
            </div>
            <BaseButton
              v-for="action in byId.get(id)?.actions ?? []"
              :key="action.label"
              kind="ghost"
              size="sm"
              :icon="action.icon"
              :aria-label="action.label"
              :title="action.label"
              @click="action.run()"
            />
            <span v-if="!isNarrow && (severalCells || fills(id))" class="layout-head__signalled">
              <BaseButton
                kind="ghost"
                size="sm"
                :icon="fills(id) ? 'mdi-arrow-collapse' : 'mdi-arrow-expand'"
                :aria-label="fills(id) ? t('shell.layout.restore') : t('shell.layout.maximize')"
                :title="fills(id) ? t('shell.layout.restore') : t('shell.layout.maximize')"
                data-layout-maximize
                @click="setMaximized(fills(id) ? null : id)"
              />
              <span v-if="fills(id) && signalledElsewhere(id)" class="layout-head__dot" aria-hidden="true" />
            </span>
            <BaseButton
              kind="ghost"
              size="sm"
              icon="mdi-close"
              :aria-label="t('shell.layout.close', { title: byId.get(id)?.title ?? id })"
              :title="t('shell.layout.close', { title: byId.get(id)?.title ?? id })"
              :data-layout-close="id"
              @click="emit('close', id)"
            />
          </div>
          <div class="layout-area__body">
            <component
              :is="byId.get(id)!.component"
              v-if="byId.get(id)?.component"
              v-bind="byId.get(id)!.props ?? {}"
            />
            <slot v-else :id="id" name="panel" />
          </div>
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

.layout-area__body {
  height: 100%;
  min-height: 0;
}

.layout-area__panel--fit,
.layout-area__panel--fit > .layout-area__body {
  height: auto;
}

.layout-area__panel--titled {
  display: flex;
  flex-direction: column;
}

.layout-area__panel--titled > .layout-area__body {
  flex: 1 1 0;
  height: auto;
  overflow: hidden;
}

/* A titled panel's own bar: as tall as a tab strip, so it lines up with the cells beside it. */
.layout-head {
  display: flex;
  flex: none;
  gap: 2px;
  align-items: center;
  height: var(--layout-tab-strip-height, 40px);
  padding: 0 8px 0 4px;
  border-bottom: 1px solid var(--line);
}

.layout-head__main {
  display: flex;
  flex: 1 1 auto;
  gap: 4px;
  align-items: center;
  min-width: 0;
  height: 100%;
}

.layout-head__title {
  padding: 0 8px;
  overflow: hidden;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.layout-head__signalled {
  position: relative;
  display: inline-flex;
}

.layout-head__dot {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 6px;
  height: 6px;
  pointer-events: none;
  background: var(--accent);
  border-radius: var(--radius-pill);
}
</style>
