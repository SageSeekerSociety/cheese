<script setup lang="ts">
/**
 * The wide layout of a layout area, on dockview. Loaded on demand by `LayoutArea`: this
 * file and dockview's own code and stylesheet stay out of every page that never shows a
 * wide layout.
 *
 * dockview owns the grid while this is mounted. After each change the adapter reads
 * dockview's layout back as an arrangement (`fromDockview`), restores the invariants
 * (`reconcile`) and hands it up; a layout that broke them (a panel twice, an unknown one)
 * is loaded back in its reconciled form. It never renders panel content: each panel's
 * cell holds an empty host element that the area teleports the instance into.
 *
 * Collapse, pinning, the lock and per-cell minimum widths are policies applied on top of
 * dockview's groups; the tabs and cell actions are ours, styled with the product tokens.
 */
import 'dockview-vue/dist/styles/dockview.css'

import type {
  DockviewApi,
  DockviewGroupPanel,
  DockviewReadyEvent,
  DockviewTheme,
  GroupviewPanelState,
  IDockviewHeaderActionsProps,
  IDockviewPanel,
  IDockviewPanelHeaderProps,
  IDockviewPanelProps,
} from 'dockview-vue'
import type { LayoutArrangement } from './arrangement'
import type { LayoutPanelInfo } from './types'

import { computed, markRaw, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { DockviewVue } from 'dockview-vue'

import {
  acceptsDrop,
  canCollapse,
  cellOfPanel,
  cellsOf,
  hasPlacementFault,
  parentDirection,
  placePanel,
  reconcile,
  setCollapsed,
  withCellIds,
} from './arrangement'
import { fromDockview, STRIP_SIZE, toDockview } from './dockviewJson'
import LayoutAddMenu from './LayoutAddMenu.vue'

import BaseButton from '@/components/base/BaseButton.vue'

/** No cell is squeezed below this, whatever its panels ask for. */
const MIN_CELL = 120
const UNBOUNDED = Number.MAX_SAFE_INTEGER

const props = defineProps<{
  arrangement: LayoutArrangement
  /** Changes when the area replaces the arrangement (reset, open): load it again. */
  loadKey: number
  panels: LayoutPanelInfo[]
  unplaced: LayoutPanelInfo[]
  locked: boolean
  resizable: boolean
}>()

const emit = defineEmits<{
  change: [arrangement: LayoutArrangement]
  host: [id: string, element: HTMLElement, attached: boolean]
  /** A panel's content was attached again after dockview hid its tab. */
  shown: [id: string]
}>()

const { t } = useI18n()

const theme: DockviewTheme = { name: 'cheese', className: 'layout-dock-theme', gap: 0 }

const root = ref<HTMLElement>()
const api = shallowRef<DockviewApi>()
const info = computed(() => new Map(props.panels.map((panel) => [panel.id, panel])))
const declared = computed(() => props.panels.map((panel) => panel.id))
const pinned = (id: string) => info.value.get(id)?.pinned === true

/** The arrangement as dockview shows it now; what the cell chrome reads. */
const model = shallowRef<LayoutArrangement>({ root: null })
/** Groups folded to a strip. Mirrors `model`, kept apart so a drag can be judged mid-sync. */
let collapsed = new Set<string>()
/** For each collapsed group, the size it opens back to. */
let expanded = new Map<string, number>()
const maximized = ref<string | null>(null)
let loading = false

function panelState(id: string): GroupviewPanelState {
  const panel = info.value.get(id)
  return {
    id,
    contentComponent: 'host',
    title: panel?.title ?? id,
    renderer: panel?.alwaysRender ? 'always' : 'onlyWhenVisible',
  }
}

function box() {
  return { width: root.value?.clientWidth || 1200, height: root.value?.clientHeight || 800 }
}

function load(arrangement: LayoutArrangement) {
  const dock = api.value
  if (!dock) return
  loading = true
  try {
    if (dock.hasMaximizedGroup()) dock.exitMaximizedGroup()
    const next = withCellIds(reconcile(arrangement, declared.value))
    stripped.clear()
    constrained.clear()
    if (!next.root) {
      dock.clear()
      collapsed = new Set()
      expanded = new Map()
    } else {
      const { layout, expandedSizes } = toDockview({ root: next.root }, box(), panelState)
      dock.fromJSON(layout)
      collapsed = new Set(
        cellsOf(next)
          .filter((cell) => cell.collapsed)
          .map((cell) => cell.id as string)
      )
      expanded = expandedSizes
    }
  } finally {
    loading = false
  }
  dock.panels.forEach(watchVisibility)
  sync()
}

let pending = false
function schedule() {
  if (pending) return
  pending = true
  queueMicrotask(() => {
    pending = false
    sync()
  })
}

function sync() {
  const dock = api.value
  if (!dock || loading) return
  // Maximizing hides the other cells only for the moment; the arrangement is unchanged.
  if (dock.hasMaximizedGroup()) return
  const derived = fromDockview(dock.toJSON(), collapsed, expanded)
  if (hasPlacementFault(derived, declared.value)) {
    load(derived)
    return
  }
  const next = reconcile(derived, declared.value)
  collapsed = new Set(
    cellsOf(next)
      .filter((cell) => cell.collapsed)
      .map((cell) => cell.id as string)
  )
  model.value = next
  applyPolicies()
  emit('change', next)
}

/** Groups currently held to a strip, and the constraints last given to each group. */
const stripped = new Set<string>()
const constrained = new Map<string, string>()

function applyPolicies() {
  const dock = api.value
  if (!dock) return
  for (const group of dock.groups) {
    const id = group.id
    const folded = collapsed.has(id)
    if (group.header.hidden !== folded) group.header.hidden = folded
    const lock = folded ? 'no-drop-target' : false
    if (group.locked !== lock) group.locked = lock
    const column = parentDirection(model.value, id) === 'column'
    const min = Math.max(MIN_CELL, ...group.panels.map((panel) => info.value.get(panel.id)?.minWidth ?? 0))
    const constraints = folded
      ? column
        ? { minimumWidth: min, maximumWidth: UNBOUNDED, minimumHeight: STRIP_SIZE, maximumHeight: STRIP_SIZE }
        : { minimumWidth: STRIP_SIZE, maximumWidth: STRIP_SIZE, minimumHeight: 0, maximumHeight: UNBOUNDED }
      : { minimumWidth: min, maximumWidth: UNBOUNDED, minimumHeight: 0, maximumHeight: UNBOUNDED }
    const key = JSON.stringify(constraints)
    if (constrained.get(id) !== key) {
      constrained.set(id, key)
      group.api.setConstraints(constraints)
    }
    if (folded && !stripped.has(id)) {
      stripped.add(id)
      group.api.setSize(column ? { height: STRIP_SIZE } : { width: STRIP_SIZE })
    } else if (!folded && stripped.has(id)) {
      stripped.delete(id)
      const size = expanded.get(id)
      expanded.delete(id)
      if (size) group.api.setSize(column ? { height: size } : { width: size })
    }
  }
}

// ---------------------------------------------------------------- cell actions

function collapse(group: DockviewGroupPanel) {
  const dock = api.value
  if (!dock || !canCollapse(model.value, group.id)) return
  if (dock.hasMaximizedGroup()) dock.exitMaximizedGroup()
  const column = parentDirection(model.value, group.id) === 'column'
  expanded.set(group.id, column ? group.api.height : group.api.width)
  collapsed.add(group.id)
  model.value = setCollapsed(model.value, group.id, true)
  applyPolicies()
  schedule()
}

function expand(cellId: string, panelId?: string) {
  const dock = api.value
  if (!dock) return
  collapsed.delete(cellId)
  model.value = setCollapsed(model.value, cellId, false)
  applyPolicies()
  if (panelId) dock.getPanel(panelId)?.api.setActive()
  schedule()
}

function toggleMaximize(group: DockviewGroupPanel) {
  const dock = api.value
  if (!dock) return
  if (dock.hasMaximizedGroup()) dock.exitMaximizedGroup()
  else if (group.activePanel) dock.maximizeGroup(group.activePanel)
}

function addTo(group: DockviewGroupPanel | null, id: string) {
  const dock = api.value
  if (!dock) return
  if (!group) {
    load(placePanel(model.value, id))
    return
  }
  const state = panelState(id)
  dock.addPanel({
    id,
    title: state.title,
    renderer: state.renderer,
    component: 'host',
    position: { referenceGroup: group },
  })
}

const cellCount = computed(() => cellsOf(model.value).length)

// ---------------------------------------------------------------- hosts

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

/** What a dockview event subscription returns. */
type Disposable = { dispose(): void }

const visibility = new Map<string, Disposable>()
function watchVisibility(panel: IDockviewPanel) {
  if (visibility.has(panel.id)) return
  visibility.set(
    panel.id,
    panel.api.onDidVisibilityChange((event) => {
      if (event.isVisible) emit('shown', panel.id)
    })
  )
}

const panelOf = (scope: IDockviewPanelProps) => scope.api.id
const collapsedCellOf = (id: string) => {
  const cell = cellOfPanel(model.value, id)
  return cell?.collapsed ? cell : undefined
}

// ---------------------------------------------------------------- wiring

const disposables: Disposable[] = []

function onReady(event: DockviewReadyEvent) {
  const dock = markRaw(event.api)
  api.value = dock
  disposables.push(
    dock.onWillDragPanel((e) => {
      if (pinned(e.panel.id)) e.nativeEvent.preventDefault()
    }),
    dock.onWillDragGroup((e) => {
      if (e.group.panels.some((panel) => pinned(panel.id))) e.nativeEvent.preventDefault()
    }),
    dock.onWillShowOverlay((e) => {
      if (e.group && !acceptsDrop(model.value, e.group.id)) e.preventDefault()
    }),
    dock.onDidAddPanel(watchVisibility),
    dock.onDidRemovePanel((panel) => {
      visibility.get(panel.id)?.dispose()
      visibility.delete(panel.id)
    }),
    dock.onDidMaximizedGroupChange((e) => {
      maximized.value = e.isMaximized ? e.group.id : null
      if (!e.isMaximized) schedule()
    }),
    dock.onDidLayoutChange(schedule)
  )
  load(props.arrangement)
}

watch(
  () => props.loadKey,
  () => load(props.arrangement)
)

onBeforeUnmount(() => {
  disposables.forEach((d) => d.dispose())
  visibility.forEach((d) => d.dispose())
})

const noContextMenu = () => []
</script>

<template>
  <div ref="root" class="layout-dock" :class="{ 'layout-dock--fixed': !resizable }">
    <DockviewVue
      class="layout-dock__view"
      :theme="theme"
      :disable-floating-groups="true"
      :disable-dnd="locked"
      :get-tab-context-menu-items="noContextMenu"
      @ready="onReady"
    >
      <template #panel-host="scope: IDockviewPanelProps">
        <div
          v-if="collapsedCellOf(panelOf(scope))"
          class="layout-dock__strip"
          :class="{
            'layout-dock__strip--column': parentDirection(model, collapsedCellOf(panelOf(scope))!.id!) === 'column',
          }"
        >
          <BaseButton
            v-for="id in collapsedCellOf(panelOf(scope))!.panels"
            :key="id"
            kind="ghost"
            size="sm"
            :icon="info.get(id)?.icon ?? 'mdi-dock-window'"
            :aria-label="t('shell.layout.expand', { title: info.get(id)?.title ?? id })"
            :title="t('shell.layout.expand', { title: info.get(id)?.title ?? id })"
            :data-layout-expand="id"
            @click="expand(collapsedCellOf(panelOf(scope))!.id!, id)"
          />
        </div>
        <div v-else :ref="hostRef(panelOf(scope))" class="layout-dock__host" :data-layout-host="panelOf(scope)" />
      </template>

      <template #defaultTab="scope: IDockviewPanelHeaderProps">
        <div class="layout-tab" :data-layout-tab="scope.api.id">
          <v-icon
            v-if="info.get(scope.api.id)?.icon"
            class="layout-tab__icon"
            size="16"
            :icon="info.get(scope.api.id)!.icon"
          />
          <span class="layout-tab__title">{{ info.get(scope.api.id)?.title ?? scope.api.id }}</span>
          <button
            v-if="info.get(scope.api.id)?.closable && !pinned(scope.api.id) && !locked"
            type="button"
            class="layout-tab__close"
            :aria-label="t('shell.layout.close', { title: info.get(scope.api.id)?.title ?? scope.api.id })"
            :title="t('shell.layout.close', { title: info.get(scope.api.id)?.title ?? scope.api.id })"
            :data-layout-close="scope.api.id"
            @pointerdown.stop
            @mousedown.stop
            @click.stop="scope.api.close()"
          >
            <v-icon size="14" icon="mdi-close" />
          </button>
        </div>
      </template>

      <template #rightHeaderActions="scope: IDockviewHeaderActionsProps">
        <div class="layout-actions">
          <LayoutAddMenu v-if="unplaced.length && !locked" :panels="unplaced" @pick="addTo(scope.group, $event)" />
          <BaseButton
            v-if="cellCount > 1"
            kind="ghost"
            size="sm"
            :icon="maximized === scope.group.id ? 'mdi-arrow-collapse' : 'mdi-arrow-expand'"
            :aria-label="maximized === scope.group.id ? t('shell.layout.restore') : t('shell.layout.maximize')"
            :title="maximized === scope.group.id ? t('shell.layout.restore') : t('shell.layout.maximize')"
            data-layout-maximize
            @click="toggleMaximize(scope.group)"
          />
          <BaseButton
            v-if="!maximized && canCollapse(model, scope.group.id)"
            kind="ghost"
            size="sm"
            :icon="
              parentDirection(model, scope.group.id) === 'column'
                ? 'mdi-arrow-collapse-vertical'
                : 'mdi-arrow-collapse-horizontal'
            "
            :aria-label="t('shell.layout.collapse')"
            :title="t('shell.layout.collapse')"
            data-layout-collapse
            @click="collapse(scope.group)"
          />
        </div>
      </template>

      <template #watermark>
        <div class="layout-dock__empty">
          <LayoutAddMenu v-if="unplaced.length" :panels="unplaced" @pick="addTo(null, $event)" />
        </div>
      </template>
    </DockviewVue>
  </div>
</template>

<style scoped>
.layout-dock {
  flex: 1 1 auto;
  min-width: 0;
  height: 100%;
}

.layout-dock__host {
  width: 100%;
  height: 100%;
}

.layout-dock__strip {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  height: 100%;
  padding-top: 6px;
  background: var(--surface);
}

.layout-dock__strip--column {
  flex-direction: row;
  padding-top: 0;
  padding-left: 6px;
}

.layout-dock__empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}

.layout-tab {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 100%;
  white-space: nowrap;
}

.layout-tab__icon {
  color: var(--faint);
}

.layout-tab__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 0;
  color: var(--faint);
  cursor: pointer;
  background: transparent;
  border: 0;
  border-radius: var(--radius-sm);
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}

.layout-tab__close:hover {
  color: var(--ink);
  background: var(--fill);
}

.layout-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  height: 100%;
  padding: 0 4px;
}
</style>

<style>
/*
 * dockview's own knobs mapped onto the product tokens. Dark mode follows the tokens;
 * there is no second dockview theme. Selection is the neutral ink underline the work
 * panel's tabs use (PanelTabs.vue), never a coloured bar.
 */
.layout-dock-theme {
  --dv-tabs-and-actions-container-font-size: 13px;
  --dv-tabs-and-actions-container-height: 40px;
  --dv-tab-font-size: 13px;
  --dv-drag-over-background-color: var(--fill-2);
  --dv-drag-over-border-color: var(--line-2);
  --dv-drag-over-border: 1px solid var(--line-2);
  --dv-edge-dock-indicator-color: var(--line-2);
  --dv-icon-hover-background-color: var(--fill);
  --dv-tabs-container-scrollbar-color: var(--scrollbar-thumb);
  --dv-scrollbar-background-color: var(--scrollbar-thumb);
  --dv-paneview-active-outline-color: var(--focus-ring);
  --dv-border-radius: 0;
  --dv-tab-border-radius: 0;
  --dv-sash-border-radius: 0;
  --dv-tab-margin: 0;
  --dv-spacing-padding: 0;
  --dv-sash-color: transparent;
  --dv-active-sash-color: var(--line-2);
  --dv-active-sash-transition-duration: var(--dur-quick);
  --dv-active-sash-transition-delay: 0s;
  --dv-group-view-background-color: var(--surface);
  --dv-tabs-and-actions-container-background-color: var(--surface);
  --dv-activegroup-visiblepanel-tab-background-color: transparent;
  --dv-activegroup-hiddenpanel-tab-background-color: transparent;
  --dv-inactivegroup-visiblepanel-tab-background-color: transparent;
  --dv-inactivegroup-hiddenpanel-tab-background-color: transparent;
  --dv-tab-divider-color: transparent;
  --dv-activegroup-visiblepanel-tab-color: var(--ink);
  --dv-activegroup-hiddenpanel-tab-color: var(--muted);
  --dv-inactivegroup-visiblepanel-tab-color: var(--ink);
  --dv-inactivegroup-hiddenpanel-tab-color: var(--muted);
  --dv-separator-border: var(--line);
  --dv-paneview-header-border-color: var(--line);

  font-family: inherit;
  color: var(--text);
}

.layout-dock-theme .dv-tabs-and-actions-container {
  padding: 0 6px;
  border-bottom: 1px solid var(--line);
}

.layout-dock-theme .dv-tab {
  position: relative;
  padding: 0 12px;
  transition: color var(--dur-quick) var(--ease-standard);
}

.layout-dock-theme .dv-tab:hover {
  color: var(--ink);
}

.layout-dock-theme .dv-tab.dv-active-tab {
  font-weight: 600;
}

.layout-dock-theme .dv-tab.dv-active-tab::after {
  position: absolute;
  right: 12px;
  bottom: 0;
  left: 12px;
  height: 2px;
  content: '';
  background: var(--ink);
}

.layout-dock-theme .dv-tab.dv-active-tab .layout-tab__icon {
  color: var(--ink);
}

.layout-dock-theme
  .dv-split-view-container.dv-separator-border
  > .dv-view-container
  > .dv-view:not(:first-child)::before {
  background-color: var(--line);
}

.layout-dock-theme .dv-groupview:focus,
.layout-dock-theme .dv-groupview:focus-visible {
  outline: none;
}

.layout-dock-theme .dv-drop-target-selection,
.layout-dock-theme .dv-drop-target-anchor {
  border-radius: var(--radius-md);
}

/* Global, not scoped: DockviewVue renders a fragment, so the scope attribute never reaches it. */
.layout-dock__view {
  width: 100%;
  height: 100%;
}

/* The lock on resizing: the dividers stay drawn but take no pointer. */
.layout-dock--fixed .dv-sash {
  pointer-events: none;
}
</style>
