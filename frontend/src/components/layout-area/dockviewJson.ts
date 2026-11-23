/**
 * Our arrangement to dockview's serialised layout and back.
 *
 * Type-only imports from dockview: this module carries no dockview code, so the
 * conversion is tested without a browser and costs the main bundle nothing.
 */
import type { GroupviewPanelState, SerializedDockview } from 'dockview-vue'
import type { LayoutArrangement, LayoutCell, LayoutNode, LayoutSplit, SplitDirection } from './arrangement'

import { isSplit } from './arrangement'

/** Width (or height, in a column) of a collapsed cell. */
export const STRIP_SIZE = 40

type Orientation = 'HORIZONTAL' | 'VERTICAL'
type GridNode = SerializedDockview['grid']['root']

const orientationOf = (direction: SplitDirection): Orientation => (direction === 'row' ? 'HORIZONTAL' : 'VERTICAL')
const directionOf = (orientation: Orientation): SplitDirection => (orientation === 'HORIZONTAL' ? 'row' : 'column')
const flip = (orientation: Orientation): Orientation => (orientation === 'HORIZONTAL' ? 'VERTICAL' : 'HORIZONTAL')

/** How one cell is laid out beyond its weight. */
export interface CellSizing {
  /**
   * Its extent along its split, in px. It takes no share of the weights, and the cells
   * sharing the split absorb any change in the area's size (`keepShares` in the adapter).
   */
  fixed?: number
  /** Laid out out of sight, with the size it will come back at. */
  hidden?: boolean
}

/** What a cell's panels say about their sizes, as `cellSizing` reads them. */
export interface SizedPanel {
  id: string
  width?: unknown
  height?: unknown
  fitHeight?: boolean
  hidden?: boolean
}

/**
 * How a cell of these panels is laid out along its split: a width of its own in a row, a
 * height of its own or its content's height in a column, and hidden when every panel in
 * it is. `extentOf` gives the px a panel with a width or height of its own keeps now.
 */
export function cellSizing(
  panels: readonly SizedPanel[],
  along: SplitDirection,
  extentOf: (id: string, axis: 'width' | 'height') => number,
  fitHeights: Readonly<Record<string, number>>
): CellSizing {
  const out: CellSizing = {}
  const wide = panels.find((panel) => panel.width)
  if (along === 'row' && wide) out.fixed = extentOf(wide.id, 'width')
  const tall = panels.find((panel) => panel.height)
  if (along === 'column' && tall) out.fixed = extentOf(tall.id, 'height')
  const fit = panels.find((panel) => panel.fitHeight)
  if (along === 'column' && fit) out.fixed = fitHeights[fit.id] ?? 0
  if (panels.length && panels.every((panel) => panel.hidden)) out.hidden = true
  return out
}

/** Where a floating cell sits over the area, in px from the area's top left corner. */
export interface FloatBox {
  left: number
  top: number
  width: number
  height: number
}

/** No floating window is made smaller than this, in px. */
export const FLOAT_MIN = { width: 240, height: 120 }

/**
 * The box moved and shrunk into the area as it is now: wholly inside it, never smaller than
 * `FLOAT_MIN` unless the area itself is.
 */
export function fitFloat(box: FloatBox, area: { width: number; height: number }): FloatBox {
  const width = Math.round(Math.min(area.width, Math.max(FLOAT_MIN.width, box.width)))
  const height = Math.round(Math.min(area.height, Math.max(FLOAT_MIN.height, box.height)))
  return {
    width,
    height,
    left: Math.round(Math.min(Math.max(0, box.left), area.width - width)),
    top: Math.round(Math.min(Math.max(0, box.top), area.height - height)),
  }
}

export interface DockviewLoad {
  layout: SerializedDockview
  /** For each collapsed cell, the size it opens back to, in px along its split. */
  expandedSizes: Map<string, number>
}

/** A node's share of its split, as laid out: its size, or 1 when it has none. */
export function weight(node: LayoutNode): number {
  return typeof node.size === 'number' && node.size > 0 ? node.size : 1
}

/**
 * Lays an arrangement out in a box. Every cell must carry an id (`withCellIds`); it
 * becomes the dockview group id. A collapsed cell gets `STRIP_SIZE` and a fixed cell
 * its own size; the other open cells share the rest by weight, and the size a
 * collapsed cell had is returned for when it opens. A hidden cell takes no space now
 * and keeps the share it would have for when it is shown; so does a split whose every cell
 * is hidden. A floating cell floats at the box `floatBox` gives it, fitted into the area.
 */
export function toDockview(
  arrangement: LayoutArrangement & { root: LayoutNode },
  box: { width: number; height: number },
  panelState: (id: string) => GroupviewPanelState,
  sizing: (cell: LayoutCell, along: SplitDirection) => CellSizing = () => ({}),
  floatBox: (cell: LayoutCell) => FloatBox = () => ({ left: 48, top: 48, width: 480, height: 320 })
): DockviewLoad {
  const panels: Record<string, GroupviewPanelState> = {}
  const expandedSizes = new Map<string, number>()

  const leaf = (cell: LayoutCell, size: number, own: CellSizing): GridNode => {
    cell.panels.forEach((id) => (panels[id] = panelState(id)))
    return {
      type: 'leaf',
      size,
      ...(own.hidden ? { visible: false } : {}),
      data: {
        id: cell.id as string,
        views: cell.panels,
        activeView: cell.active ?? cell.panels[0],
        ...(own.fixed !== undefined ? { priority: 'low' as const } : {}),
        ...(cell.collapsed ? { hideHeader: true, locked: 'no-drop-target' as const } : {}),
      },
    }
  }

  // `length` is the extent along this split's own axis, `cross` the other one.
  const branch = (
    children: LayoutNode[],
    orientation: Orientation,
    length: number,
    cross: number,
    size: number
  ): GridNode => {
    const along = directionOf(orientation)
    const own = children.map((child) => (isSplit(child) ? { hidden: allHidden(child) } : sizing(child, along)))
    const strip = (i: number) => {
      const child = children[i]
      return !isSplit(child) && child.collapsed === true
    }
    const weighted = children.map((_, i) => !strip(i) && own[i].fixed === undefined)
    const taken = children.reduce((sum, _, i) => {
      if (own[i].hidden) return sum
      if (strip(i)) return sum + STRIP_SIZE
      return sum + (own[i].fixed ?? 0)
    }, 0)
    const free = Math.max(0, length - taken)
    const total = children.reduce((sum, c) => sum + weight(c), 0)
    const allWeights = children.reduce((sum, c, i) => sum + (weighted[i] ? weight(c) : 0), 0)
    const shownWeights = children.reduce((sum, c, i) => sum + (weighted[i] && !own[i].hidden ? weight(c) : 0), 0)
    return {
      type: 'branch',
      size,
      data: children.map((child, i) => {
        if (strip(i)) {
          expandedSizes.set((child as LayoutCell).id as string, Math.round((weight(child) / total) * length))
          return leaf(child as LayoutCell, STRIP_SIZE, own[i])
        }
        const fixed = own[i].fixed
        const extent =
          fixed !== undefined
            ? fixed
            : Math.round((weight(child) / (own[i].hidden ? allWeights : shownWeights || 1)) * free)
        if (!isSplit(child)) return leaf(child, extent, own[i])
        const node = branch(child.children, flip(orientation), cross, extent, extent)
        return own[i].hidden ? { ...node, visible: false } : node
      }),
    }
  }

  /** Every cell under this split is hidden: dockview lays the split out of sight too. */
  const allHidden = (split: LayoutSplit): boolean =>
    split.children.every((child) =>
      isSplit(child) ? allHidden(child) : sizing(child, split.direction).hidden === true
    )

  const root = arrangement.root
  const orientation: Orientation = isSplit(root) ? orientationOf(root.direction) : 'HORIZONTAL'
  const length = orientation === 'HORIZONTAL' ? box.width : box.height
  const cross = orientation === 'HORIZONTAL' ? box.height : box.width
  const rootNode = branch(isSplit(root) ? root.children : [root], orientation, length, cross, cross)
  const floatingGroups = (arrangement.floating ?? []).map((cell) => {
    cell.panels.forEach((id) => (panels[id] = panelState(id)))
    return {
      data: { id: cell.id as string, views: cell.panels, activeView: cell.active ?? cell.panels[0] },
      position: fitFloat(floatBox(cell), box),
    }
  })
  return {
    layout: {
      grid: { root: rootNode, width: box.width, height: box.height, orientation } as SerializedDockview['grid'],
      panels,
      ...(floatingGroups.length ? { floatingGroups } : {}),
    },
    expandedSizes,
  }
}

/**
 * Reads dockview's layout back as an arrangement. Sizes are dockview's px, which work as
 * weights; a collapsed cell reports the size it will open to, not its strip, and a
 * hidden cell the size it will come back at. The weighted cells beside a hidden one give
 * that size back out of theirs, so hiding and showing a cell never shifts their weights.
 */
export function fromDockview(
  layout: SerializedDockview,
  collapsed: ReadonlySet<string>,
  expandedSizes: ReadonlyMap<string, number>
): LayoutArrangement {
  const children = (nodes: GridNode[], orientation: Orientation): LayoutNode[] => {
    const read = nodes.map((child) => visit(child, flip(orientation)))
    const hiddenSum = nodes.reduce((sum, n, i) => sum + (n.visible === false ? read[i].size ?? 0 : 0), 0)
    // A fixed cell (low priority) keeps its own width; only the weighted ones give space back.
    const fixed = (n: GridNode) => n.type === 'leaf' && (n.data as { priority?: string }).priority === 'low'
    const shown = read.filter(
      (node, i) => nodes[i].visible !== false && !fixed(nodes[i]) && !(!isSplit(node) && node.collapsed)
    )
    const shownSum = shown.reduce((sum, n) => sum + (n.size ?? 0), 0)
    if (hiddenSum > 0 && shownSum > hiddenSum) {
      const factor = (shownSum - hiddenSum) / shownSum
      shown.forEach((node) => node.size !== undefined && (node.size = node.size * factor))
    }
    return read
  }
  const visit = (node: GridNode, orientation: Orientation): LayoutNode => {
    if (node.type === 'branch') {
      return {
        direction: directionOf(orientation),
        children: children(node.data as GridNode[], orientation),
        size: node.size,
      }
    }
    const data = node.data as { id: string; views: string[]; activeView?: string }
    const cell: LayoutCell = { id: data.id, panels: [...data.views] }
    if (data.activeView) cell.active = data.activeView
    if (collapsed.has(data.id)) {
      cell.collapsed = true
      cell.size = expandedSizes.get(data.id) ?? node.size
    } else if (node.size !== undefined) {
      cell.size = node.size
    }
    return cell
  }
  const root = layout.grid.root
  const orientation = layout.grid.orientation as Orientation
  // The root branch's own size is the cross extent, not a weight.
  const node: LayoutNode =
    root.type === 'branch'
      ? { direction: directionOf(orientation), children: children(root.data as GridNode[], orientation) }
      : visit(root, orientation)
  const floating = floatingCells(layout)
  const grid = isSplit(node) && node.children.length === 0 ? null : node
  return floating.length ? { root: grid, floating } : { root: grid }
}

type GroupData = { id: string; views: string[]; activeView?: string }

/** dockview's floating windows as cells; a window holding a split of its own gives one cell per group. */
function floatingCells(layout: SerializedDockview): LayoutCell[] {
  const leaves = (node: GridNode): GroupData[] =>
    node.type === 'branch' ? (node.data as GridNode[]).flatMap(leaves) : [node.data as GroupData]
  return (layout.floatingGroups ?? [])
    .flatMap((window) => (window.data ? [window.data as GroupData] : window.grid ? leaves(window.grid.root) : []))
    .map((data) => ({
      id: data.id,
      panels: [...data.views],
      ...(data.activeView ? { active: data.activeView } : {}),
    }))
}

/** Where each floating window sits now, by the panels it holds. */
export function floatBoxes(layout: SerializedDockview): { panels: string[]; box: FloatBox }[] {
  return (layout.floatingGroups ?? []).map((window) => {
    // dockview anchors a window to the nearer edges: one placed by its right or bottom edge
    // is measured back from the area's size.
    const { width, height } = window.position
    const at = window.position as Partial<Record<'left' | 'right' | 'top' | 'bottom', number>>
    const left = at.left ?? layout.grid.width - (at.right ?? 0) - width
    const top = at.top ?? layout.grid.height - (at.bottom ?? 0) - height
    const panels = floatingCells({ ...layout, floatingGroups: [window] }).flatMap((cell) => cell.panels)
    return { panels, box: { left, top, width, height } }
  })
}
