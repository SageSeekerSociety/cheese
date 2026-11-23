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
 * Collapse, pinning, the lock, bare and hidden cells and per-cell sizes are policies
 * applied on top of dockview's groups; the tabs and cell actions are ours, styled with
 * the product tokens.
 *
 * Space is not shared in proportion when the area resizes (`proportional-layout` off): a
 * cell with a width of its own (or, in a column, a height) is a low-priority view in
 * dockview's terms and keeps it, and the other cells absorb the change, as a code editor's
 * side bar and terminal do.
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
import type { LayoutArrangement, LayoutCell, SplitDirection } from './arrangement'
import type { CellSizing } from './dockviewJson'
import type { LayoutPanelInfo } from './types'

import { computed, markRaw, nextTick, onBeforeUnmount, reactive, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { DockviewVue } from 'dockview-vue'

import {
  acceptsDrop,
  canCollapse,
  cellOfPanel,
  cellsOf,
  defaultWeights,
  hasPlacementFault,
  hidesTabs,
  homeCell,
  parentDirection,
  placedPanels,
  placePanel,
  reconcile,
  resetSizes,
  setCollapsed,
  spawnLanding,
  withCellIds,
} from './arrangement'
import { fromDockview, STRIP_SIZE, toDockview } from './dockviewJson'
import LayoutAddMenu from './LayoutAddMenu.vue'
import LayoutSignal from './LayoutSignal.vue'
import { clampWidth } from './widths'

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
  /** Fixed sizes and hidden cells, as the area decides them. */
  sizing: (cell: LayoutCell, along: SplitDirection) => CellSizing
  /** The measured content height of each `fitHeight` panel. */
  fitHeights: Record<string, number>
  /** The panel whose cell fills the area, or null. */
  maximized: string | null
  /** The width each panel with a width of its own should have now. */
  fixedWidths: Record<string, number>
  /** The height each panel with a height of its own should have now. */
  fixedHeights: Record<string, number>
  /** The page's default: double-clicking a divider gives its split these sizes back. */
  defaultArrangement: LayoutArrangement
  /** Show this panel (and open its cell), or fold or open its cell; acted on once per `n`. */
  request: { id: string; fold?: boolean; n: number } | null
}>()

const emit = defineEmits<{
  change: [arrangement: LayoutArrangement]
  host: [id: string, element: HTMLElement, attached: boolean]
  /** A panel's content was attached again after dockview hid its tab. */
  shown: [id: string]
  /** The first arrangement is laid out; its hosts follow. */
  ready: []
  /** The reader dragged the divider of a cell with a width (or height) of its own. */
  resized: [id: string, size: number, axis: 'width' | 'height']
  /** dockview maximized a cell, or let it go (also when something else ended it). */
  maximized: [id: string | null]
  /** The reader picked this panel's tab. */
  activated: [id: string]
  /** The panels on screen now: the visible tab of every cell in sight. */
  visible: [ids: string[]]
  /** The reader picked, under "+", a panel that spawns others. */
  spawn: [id: string]
  /** The reader closed the tab of a transient panel. */
  close: [id: string]
  /** The reader double-clicked the tab of a temporary panel. */
  keep: [id: string]
}>()

const { t } = useI18n()

const theme: DockviewTheme = { name: 'cheese', className: 'layout-dock-theme', gap: 0 }

const root = ref<HTMLElement>()
const api = shallowRef<DockviewApi>()
const info = computed(() => new Map(props.panels.map((panel) => [panel.id, panel])))
const declared = computed(() => props.panels.map((panel) => panel.id))
const pinned = (id: string) => info.value.get(id)?.pinned === true
/** Bare cells (the page head, the sidebar) take no dropped tab; a lone pinned panel's cell does. */
const bareGroup = (group: DockviewGroupPanel) =>
  group.panels.length > 0 && group.panels.every((panel) => info.value.get(panel.id)?.bare)
/** A cell with no tab strip to drop a tab on. */
const tablessGroup = (group: DockviewGroupPanel) =>
  group.panels.length > 0 &&
  group.panels.every((panel) => info.value.get(panel.id)?.bare || info.value.get(panel.id)?.titled)

/** The arrangement as dockview shows it now; what the cell chrome reads. */
const model = shallowRef<LayoutArrangement>({ root: null })
/** Groups folded to a strip. Mirrors `model`, kept apart so a drag can be judged mid-sync. */
let collapsed = new Set<string>()
/** For each collapsed group, the size it opens back to. */
let expanded = new Map<string, number>()
/**
 * The maximized group, if any. Maximizing is done here, by hiding the other cells, and
 * not with dockview's own maximize: that one hides every other cell, the page head and
 * the sidebar among them, and those two stay where they are.
 */
const maximizedGroup = ref<string | null>(null)
/** The groups hidden for the maximized one, shown again when it ends. */
let hiddenForMaximize: string[] = []
let loading = false

const panelInfo = (id: string) => info.value.get(id)
/** The panels of a group, as the area declared them. */
const infosOf = (group: DockviewGroupPanel) =>
  group.panels.map((panel) => panelInfo(panel.id)).filter((p): p is LayoutPanelInfo => !!p)

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
    exitMaximize()
    const next = withCellIds(reconcile(arrangement, declared.value))
    stripped.clear()
    constrained.clear()
    fitted.clear()
    held.clear()
    if (!next.root) {
      dock.clear()
      collapsed = new Set()
      expanded = new Map()
    } else {
      const { layout, expandedSizes } = toDockview({ root: next.root }, box(), panelState, props.sizing)
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
  if (props.maximized) maximize(props.maximized)
  for (const group of dock.groups) if (group.activePanel) revealTab(group.activePanel.id)
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
  if (maximizedGroup.value) return
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
  refreshActives()
  emit('change', next)
}

// ---------------------------------------------------------------- what is on screen

/** Each group's visible tab, kept reactive for the header the page's actions go in. */
const activeOf = reactive<Record<string, string | undefined>>({})
let shownBefore = ''
function refreshActives() {
  const dock = api.value
  if (!dock) return
  const shown: string[] = []
  for (const group of dock.groups) {
    const id = group.activePanel?.id
    if (activeOf[group.id] !== id) activeOf[group.id] = id
    const inSight = group.api.isVisible && !collapsed.has(group.id)
    if (id && inSight) shown.push(id)
  }
  const key = shown.join('\n')
  if (key !== shownBefore) {
    shownBefore = key
    emit('visible', shown)
  }
}

/** Groups currently held to a strip, and the constraints last given to each group. */
const stripped = new Set<string>()
const constrained = new Map<string, string>()

/** Groups currently held to their content's height, at that height. */
const fitted = new Map<string, number>()
/** Groups with a width (or, in a column, a height) of their own, and the size last given to each. */
const held = new Map<string, number>()

/** The width a group keeps in a row, or the height it keeps in a column, if it keeps one. */
function heldSize(group: DockviewGroupPanel): { id: string; axis: 'width' | 'height'; size: number } | undefined {
  const column = parentDirection(model.value, group.id) === 'column'
  const axis = column ? 'height' : 'width'
  const fixed = infosOf(group).find((panel) => panel[axis])
  const size = fixed ? (column ? props.fixedHeights : props.fixedWidths)[fixed.id] : undefined
  return fixed && size !== undefined ? { id: fixed.id, axis, size } : undefined
}
function sizeOf(axis: 'width' | 'height', size: number): { width: number } | { height: number } {
  return axis === 'width' ? { width: size } : { height: size }
}
function sizeAlong(group: DockviewGroupPanel, axis: 'width' | 'height'): number {
  return axis === 'width' ? group.api.width : group.api.height
}

function applyPolicies() {
  const dock = api.value
  if (!dock) return
  // The page hid the panels of the maximized cell: the maximize ends with them.
  const big = dock.groups.find((group) => group.id === maximizedGroup.value)
  if (maximizedGroup.value && (!big || (infosOf(big).length && infosOf(big).every((panel) => panel.hidden)))) {
    exitMaximize()
  }
  /** A cell appeared, disappeared or changed its limits: the room the others had changed. */
  let reshaped = false
  for (const group of dock.groups) {
    const id = group.id
    const panels = infosOf(group)
    const folded = collapsed.has(id)
    const headerHidden = folded || hidesTabs(panels)
    if (group.header.hidden !== headerHidden) group.header.hidden = headerHidden
    const lock = folded ? 'no-drop-target' : false
    if (group.locked !== lock) group.locked = lock
    const column = parentDirection(model.value, id) === 'column'
    const width = panels.find((panel) => panel.width)?.width
    const fitPanel = column ? panels.find((panel) => panel.fitHeight) : undefined
    const fitHeight = fitPanel ? props.fitHeights[fitPanel.id] ?? 0 : undefined
    group.element.classList.toggle('layout-dock__fit', fitPanel !== undefined)
    const min = Math.max(width?.min ?? MIN_CELL, ...panels.map((panel) => panel.minWidth ?? 0))
    const constraints = folded
      ? column
        ? { minimumWidth: min, maximumWidth: UNBOUNDED, minimumHeight: STRIP_SIZE, maximumHeight: STRIP_SIZE }
        : { minimumWidth: STRIP_SIZE, maximumWidth: STRIP_SIZE, minimumHeight: 0, maximumHeight: UNBOUNDED }
      : {
          minimumWidth: min,
          maximumWidth: width?.max ?? UNBOUNDED,
          // A height of its own has a floor; no ceiling here, so a cell left alone in its
          // column still fills it.
          minimumHeight: fitHeight ?? (column ? panels.find((panel) => panel.height)?.height?.min : undefined) ?? 0,
          maximumHeight: fitHeight ?? UNBOUNDED,
        }
    const key = JSON.stringify(constraints)
    if (constrained.get(id) !== key) {
      constrained.set(id, key)
      group.api.setConstraints(constraints)
      reshaped = true
    }
    if (fitHeight !== undefined && fitted.get(id) !== fitHeight) {
      fitted.set(id, fitHeight)
      group.api.setSize({ height: fitHeight })
    }
    const hidden = panels.length > 0 && panels.every((panel) => panel.hidden)
    // While a cell is maximized the others are hidden for it, and shown again when it ends.
    if (!maximizedGroup.value && group.api.isVisible === hidden) {
      group.api.setVisible(!hidden)
      // Shown again at the size it had when hidden: its own width may have changed since.
      held.delete(id)
      reshaped = true
    }
    // A width (or height) of its own changed elsewhere (another place showing the same
    // panel): follow it, once per new value, never in the middle of a drag.
    const wanted = heldSize(group)
    if (wanted && !dragging && group.api.isVisible && held.get(id) !== wanted.size) {
      held.set(id, wanted.size)
      if (Math.abs(sizeAlong(group, wanted.axis) - wanted.size) > 1) group.api.setSize(sizeOf(wanted.axis, wanted.size))
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
  // Neighbours whose minimum did not fit squeezed a cell below its own width (the sidebar
  // while the page still showed more cells than the window holds). With the room back, it
  // returns to the width the reader gave it.
  if (reshaped && !dragging) restoreHeldWidths(dock)
}

function restoreHeldWidths(dock: DockviewApi) {
  for (const group of dock.groups) {
    const wanted = heldSize(group)
    if (!wanted || !group.api.isVisible) continue
    if (Math.abs(sizeAlong(group, wanted.axis) - wanted.size) > 1) group.api.setSize(sizeOf(wanted.axis, wanted.size))
  }
}

// ---------------------------------------------------------------- cell actions

function collapse(group: DockviewGroupPanel) {
  const dock = api.value
  if (!dock || !canCollapse(model.value, group.id)) return
  exitMaximize()
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

/** Hides every cell but this one, the page head and the sidebar: it fills what they leave. */
function enterMaximize(group: DockviewGroupPanel) {
  const dock = api.value
  if (!dock || maximizedGroup.value === group.id) return
  exitMaximize()
  maximizedGroup.value = group.id
  hiddenForMaximize = []
  for (const other of dock.groups) {
    if (other === group || bareGroup(other) || !other.api.isVisible) continue
    other.api.setVisible(false)
    hiddenForMaximize.push(other.id)
  }
  emit('maximized', group.activePanel?.id ?? null)
  refreshActives()
}

function exitMaximize() {
  const dock = api.value
  if (!dock || !maximizedGroup.value) return
  maximizedGroup.value = null
  for (const id of hiddenForMaximize) dock.getGroup(id)?.api.setVisible(true)
  hiddenForMaximize = []
  emit('maximized', null)
  refreshActives()
  if (!loading) schedule()
}

function toggleMaximize(group: DockviewGroupPanel) {
  if (maximizedGroup.value) exitMaximize()
  else enterMaximize(group)
}

/** Fills the area with the cell of this panel; null gives the others their space back. */
function maximize(id: string | null) {
  const dock = api.value
  if (!dock) return
  const panel = id ? dock.getPanel(id) : undefined
  if (!panel) return void exitMaximize()
  if (maximizedGroup.value === panel.group.id || !panel.group.api.isVisible) return
  enterMaximize(panel.group)
}

watch(
  () => props.maximized,
  (id) => maximize(id)
)

function addTo(group: DockviewGroupPanel | null, id: string) {
  if (props.unplaced.some((panel) => panel.id === id && panel.spawns)) {
    emit('spawn', id)
    return
  }
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

const groupOf = (id: string) => info.value.get(id)?.group

/** What a cell's "+" offers: panels in no cell, and a spawning panel only where it would land. */
function offeredIn(cellId: string): LayoutPanelInfo[] {
  return props.unplaced.filter((panel) => !panel.spawns || spawnLanding(model.value, panel, groupOf) === cellId)
}
/**
 * The page's buttons for a cell: those its visible tab declares, in its group's home cell
 * only. Every cell offers collapse and maximize besides, in the same places.
 */
function actionsOf(cellId: string) {
  const active = info.value.get(activeOf[cellId] ?? '')
  if (!active?.actions?.length) return []
  return homeCell(model.value, active.group, groupOf)?.id === cellId ? active.actions : []
}

/** While a cell is maximized: does a panel out of sight carry a mark (a reply, a change)? */
const signalledOutOfSight = computed(() => {
  if (!maximizedGroup.value) return false
  const inSight = new Set(cellsOf(model.value).find((cell) => cell.id === maximizedGroup.value)?.panels ?? [])
  return props.panels.some((panel) => panel.signal && !inSight.has(panel.id))
})

/** A transient panel's tab closes by asking the page to drop it; the others go under "+". */
function closeTab(id: string, close: () => void) {
  if (info.value.get(id)?.transient) emit('close', id)
  else close()
}

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
      // Nothing lands around the whole area, in a folded cell or in a cell without tabs.
      if (e.kind === 'edge') return void e.preventDefault()
      if (e.group && (!acceptsDrop(model.value, e.group.id) || tablessGroup(e.group))) e.preventDefault()
    }),
    dock.onDidActivePanelChange(() => refreshActives()),
    dock.onDidAddPanel(watchVisibility),
    dock.onDidRemovePanel((panel) => {
      visibility.get(panel.id)?.dispose()
      visibility.delete(panel.id)
    }),
    dock.onDidRemoveGroup((group) => {
      if (group.id === maximizedGroup.value) exitMaximize()
    }),
    dock.onDidLayoutChange(schedule)
  )
  load(props.arrangement)
  void nextTick(() => emit('ready'))
}

watch(
  () => props.loadKey,
  () => load(props.arrangement)
)

// Panels the page added or dropped while the rest stays where it is: add each new one to the
// cell the area placed it in, remove what the page no longer declares. A new cell means a
// reload.
watch(
  () => props.arrangement,
  (arrangement) => {
    const dock = api.value
    if (!dock || loading) return
    const wanted = new Set(placedPanels(arrangement))
    const present = new Set(dock.panels.map((panel) => panel.id))
    for (const panel of [...dock.panels]) if (!wanted.has(panel.id)) dock.removePanel(panel)
    for (const id of wanted) {
      if (present.has(id)) continue
      const cell = cellOfPanel(arrangement, id)
      const group = cell?.id ? dock.groups.find((g) => g.id === cell.id) : undefined
      if (!group) return void load(arrangement)
      const state = panelState(id)
      dock.addPanel({
        id,
        title: state.title,
        renderer: state.renderer,
        component: 'host',
        position: { referenceGroup: group },
        inactive: true,
      })
    }
  }
)

// The area asks for a panel to be shown, or a cell folded or opened.
watch(
  () => props.request,
  (request) => {
    const dock = api.value
    if (!dock || !request) return
    const panel = dock.getPanel(request.id)
    if (!panel) return
    const group = panel.group
    if (request.fold === true) return void collapse(group)
    if (request.fold === false || collapsed.has(group.id)) expand(group.id)
    if (request.fold !== undefined) return
    if (maximizedGroup.value && maximizedGroup.value !== group.id) exitMaximize()
    panel.api.setActive()
    refreshActives()
    revealTab(request.id)
  }
)

/** A tab strip too narrow for its tabs scrolls: bring the tab just shown into view. */
function revealTab(id: string) {
  requestAnimationFrame(() =>
    root.value
      ?.querySelector(`[data-layout-tab="${CSS.escape(id)}"]`)
      ?.closest('.dv-tab')
      ?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  )
}

// What the area decides about each panel (hidden, bare, sizes) can change at any time.
watch(
  () => [props.panels, props.fitHeights, props.fixedWidths],
  () => applyPolicies(),
  { deep: true }
)

// ---------------------------------------------------------------- dragging a fixed width

/** A divider is being dragged: the widths cells keep on their own may change now. */
let dragging = false
function onPointerDown(event: PointerEvent) {
  if (!(event.target instanceof Element) || !event.target.closest('.dv-sash')) return
  dragging = true
  window.addEventListener('pointerup', endDrag)
  window.addEventListener('pointercancel', endDrag)
}
function endDrag() {
  window.removeEventListener('pointerup', endDrag)
  window.removeEventListener('pointercancel', endDrag)
  if (!dragging) return
  dragging = false
  const dock = api.value
  if (!dock) return
  for (const group of dock.groups) {
    const fixed = heldSize(group)
    if (fixed && group.api.isVisible) emit('resized', fixed.id, Math.round(sizeAlong(group, fixed.axis)), fixed.axis)
  }
}

// ---------------------------------------------------------------- double-click: default sizes

/**
 * Double-clicking a divider gives its split back the sizes the page's default gives it:
 * a cell with a width of its own goes back to its initial width, the others share the
 * rest by their default weights. A split the default has no counterpart for stays as is.
 */
function onDoubleClick(event: MouseEvent) {
  const dock = api.value
  const sash = event.target instanceof Element ? event.target.closest('.dv-sash') : null
  const container = sash?.closest('.dv-split-view-container')
  if (!dock || !container || maximizedGroup.value) return
  const horizontal = container.classList.contains('dv-horizontal')
  const children = [...container.querySelectorAll(':scope > .dv-view-container > .dv-view')]
    .filter((view) => view.classList.contains('visible'))
    .map((view) => {
      const groups = dock.groups.filter((group) => view.contains(group.element))
      return { view, groups, panels: groups.flatMap((group) => group.panels.map((panel) => panel.id)) }
    })
  if (children.length < 2 || children.some((child) => !child.groups.length)) return
  const weights = defaultWeights(
    props.defaultArrangement,
    children.map((child) => child.panels)
  )
  if (!weights) return
  const axis = horizontal ? 'width' : 'height'
  const fixedOf = (panels: string[]) => panels.map((id) => info.value.get(id)?.[axis]).find((extent) => extent)
  const fixed = children.map((child) => {
    const width = child.groups.length === 1 ? fixedOf(child.panels) : undefined
    return width ? clampWidth(width, width.initial) : undefined
  })
  const extent = (view: Element) =>
    horizontal ? view.getBoundingClientRect().width : view.getBoundingClientRect().height
  const total = children.reduce((sum, child) => sum + extent(child.view), 0)
  const sizes = resetSizes(total, weights, fixed)
  // dockview gives each change to the last cell of the split: set all but that one, in order.
  children.slice(0, -1).forEach((child, i) => {
    child.groups[0].api.setSize(horizontal ? { width: sizes[i] } : { height: sizes[i] })
  })
  children.forEach((child) => {
    const own = fixedOf(child.panels)
    if (own) emit('resized', child.panels.find((id) => info.value.get(id)?.[axis])!, clampWidth(own, own.initial), axis)
  })
}

onBeforeUnmount(() => {
  endDrag()
  disposables.forEach((d) => d.dispose())
  visibility.forEach((d) => d.dispose())
})

const noContextMenu = () => []
</script>

<template>
  <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- listens for drags and double-clicks on dockview's dividers inside, not a control of its own -->
  <div
    ref="root"
    class="layout-dock"
    :class="{ 'layout-dock--fixed': !resizable }"
    @pointerdown.capture="onPointerDown"
    @dblclick.capture="onDoubleClick"
  >
    <DockviewVue
      class="layout-dock__view"
      :theme="theme"
      :disable-floating-groups="true"
      :disable-dnd="locked"
      :proportional-layout="false"
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
        <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions, vuejs-accessibility/click-events-have-key-events -- dockview's own tab element takes the click and the keyboard; this only hears which tab the reader picked -->
        <div
          class="layout-tab"
          :class="{
            'layout-tab--faint': info.get(scope.api.id)?.faint,
            'layout-tab--temporary': info.get(scope.api.id)?.temporary,
          }"
          :data-layout-tab="scope.api.id"
          :title="info.get(scope.api.id)?.hint ?? info.get(scope.api.id)?.title"
          @click="emit('activated', scope.api.id)"
          @dblclick="info.get(scope.api.id)?.temporary && emit('keep', scope.api.id)"
        >
          <v-icon
            v-if="info.get(scope.api.id)?.icon"
            class="layout-tab__icon"
            size="16"
            :icon="info.get(scope.api.id)!.icon"
          />
          <span class="layout-tab__title">{{ info.get(scope.api.id)?.title ?? scope.api.id }}</span>
          <LayoutSignal :signal="info.get(scope.api.id)?.signal" />
          <button
            v-if="info.get(scope.api.id)?.closable && !pinned(scope.api.id) && !locked"
            type="button"
            class="layout-tab__close"
            :aria-label="t('shell.layout.close', { title: info.get(scope.api.id)?.title ?? scope.api.id })"
            :title="t('shell.layout.close', { title: info.get(scope.api.id)?.title ?? scope.api.id })"
            :data-layout-close="scope.api.id"
            @pointerdown.stop
            @mousedown.stop
            @click.stop="closeTab(scope.api.id, () => scope.api.close())"
          >
            <v-icon size="14" icon="mdi-close" />
          </button>
        </div>
      </template>

      <template #rightHeaderActions="scope: IDockviewHeaderActionsProps">
        <div class="layout-actions">
          <LayoutAddMenu
            v-if="offeredIn(scope.group.id).length && !locked"
            :panels="offeredIn(scope.group.id)"
            @pick="addTo(scope.group, $event)"
          />
          <BaseButton
            v-if="!maximizedGroup && canCollapse(model, scope.group.id)"
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
          <span v-if="cellCount > 1" class="layout-actions__signalled">
            <BaseButton
              kind="ghost"
              size="sm"
              :icon="maximizedGroup === scope.group.id ? 'mdi-arrow-collapse' : 'mdi-arrow-expand'"
              :aria-label="maximizedGroup === scope.group.id ? t('shell.layout.restore') : t('shell.layout.maximize')"
              :title="maximizedGroup === scope.group.id ? t('shell.layout.restore') : t('shell.layout.maximize')"
              data-layout-maximize
              @click="toggleMaximize(scope.group)"
            />
            <span
              v-if="maximizedGroup === scope.group.id && signalledOutOfSight"
              class="layout-actions__dot"
              aria-hidden="true"
            />
          </span>
          <BaseButton
            v-for="action in actionsOf(scope.group.id)"
            :key="action.label"
            kind="ghost"
            size="sm"
            :icon="action.icon"
            :aria-label="action.label"
            :title="action.label"
            @click="action.run()"
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

.layout-tab--faint .layout-tab__title {
  color: var(--muted);
}

.layout-tab--temporary .layout-tab__title {
  font-style: italic;
}

.layout-actions__signalled {
  display: inline-flex;
  position: relative;
}

/* Something out of sight while this cell is maximized: an unread mark (amber is for unread, §1.6). */
.layout-actions__dot {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 6px;
  height: 6px;
  pointer-events: none;
  background: var(--accent);
  border-radius: var(--radius-pill);
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
  --dv-tabs-and-actions-container-height: var(--layout-tab-strip-height, 40px);
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

/* A hidden cell (hidden by the page, or while another is maximized) is out of layout, not
   squeezed to nothing: content laid out at zero width reflows and loses its scroll place. */
.layout-dock-theme .dv-view:not(.visible) > .dv-groupview {
  display: none;
}

/* A divider that cannot move (beside a hidden cell, or a cell held at one size) is inert:
   dockview marks it .dv-disabled but its hover colour checks a .disabled class it never
   sets. */
.layout-dock-theme .dv-sash.dv-disabled {
  pointer-events: none;
}

/* A divider takes a pixel of its own: the cell's content starts after it, never under it. */
.layout-dock-theme .dv-split-view-container.dv-horizontal > .dv-view-container > .dv-view:not(:first-child) {
  padding-left: 1px;
}
.layout-dock-theme .dv-split-view-container.dv-vertical > .dv-view-container > .dv-view:not(:first-child) {
  padding-top: 1px;
}

/* No divider where there is nothing to divide: after a hidden cell (also while another
   cell is maximized), and on either side of a cell as tall as its content, which draws
   its own edge and cannot be dragged. */
.layout-dock-theme .dv-view:not(.visible)::before,
.layout-dock-theme .dv-view:not(.visible) + .dv-view::before,
.layout-dock-theme .dv-view:has(> .layout-dock__fit)::before,
.layout-dock-theme .dv-view:has(> .layout-dock__fit) + .dv-view::before {
  display: none;
}
.layout-dock-theme .dv-split-view-container > .dv-view-container > .dv-view:not(.visible),
.layout-dock-theme .dv-split-view-container > .dv-view-container > .dv-view:not(.visible) + .dv-view,
.layout-dock-theme .dv-split-view-container > .dv-view-container > .dv-view:has(> .layout-dock__fit),
.layout-dock-theme .dv-split-view-container > .dv-view-container > .dv-view:has(> .layout-dock__fit) + .dv-view {
  padding: 0;
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
