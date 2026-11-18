/**
 * Our arrangement to dockview's serialised layout and back.
 *
 * Type-only imports from dockview: this module carries no dockview code, so the
 * conversion is tested without a browser and costs the main bundle nothing.
 */
import type { GroupviewPanelState, SerializedDockview } from 'dockview-vue'
import type { LayoutArrangement, LayoutCell, LayoutNode, SplitDirection } from './arrangement'

import { isSplit } from './arrangement'

/** Width (or height, in a column) of a collapsed cell. */
export const STRIP_SIZE = 40

type Orientation = 'HORIZONTAL' | 'VERTICAL'
type GridNode = SerializedDockview['grid']['root']

const orientationOf = (direction: SplitDirection): Orientation => (direction === 'row' ? 'HORIZONTAL' : 'VERTICAL')
const directionOf = (orientation: Orientation): SplitDirection => (orientation === 'HORIZONTAL' ? 'row' : 'column')
const flip = (orientation: Orientation): Orientation => (orientation === 'HORIZONTAL' ? 'VERTICAL' : 'HORIZONTAL')

export interface DockviewLoad {
  layout: SerializedDockview
  /** For each collapsed cell, the size it opens back to, in px along its split. */
  expandedSizes: Map<string, number>
}

function weight(node: LayoutNode): number {
  return typeof node.size === 'number' && node.size > 0 ? node.size : 1
}

/**
 * Lays an arrangement out in a box. Every cell must carry an id (`withCellIds`); it
 * becomes the dockview group id. A collapsed cell gets `STRIP_SIZE`, its siblings
 * share the rest by weight, and the size it had is returned for when it opens.
 */
export function toDockview(
  arrangement: LayoutArrangement & { root: LayoutNode },
  box: { width: number; height: number },
  panelState: (id: string) => GroupviewPanelState
): DockviewLoad {
  const panels: Record<string, GroupviewPanelState> = {}
  const expandedSizes = new Map<string, number>()

  const leaf = (cell: LayoutCell, size: number): GridNode => {
    cell.panels.forEach((id) => (panels[id] = panelState(id)))
    return {
      type: 'leaf',
      size,
      data: {
        id: cell.id as string,
        views: cell.panels,
        activeView: cell.active ?? cell.panels[0],
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
    const strips = children.filter((c) => !isSplit(c) && c.collapsed)
    const open = children.filter((c) => !strips.includes(c))
    const total = children.reduce((sum, c) => sum + weight(c), 0)
    const openTotal = open.reduce((sum, c) => sum + weight(c), 0)
    const free = Math.max(0, length - strips.length * STRIP_SIZE)
    return {
      type: 'branch',
      size,
      data: children.map((child) => {
        if (!isSplit(child) && child.collapsed) {
          expandedSizes.set(child.id as string, Math.round((weight(child) / total) * length))
          return leaf(child, STRIP_SIZE)
        }
        const extent = Math.round((weight(child) / openTotal) * free)
        return isSplit(child) ? branch(child.children, flip(orientation), cross, extent, extent) : leaf(child, extent)
      }),
    }
  }

  const root = arrangement.root
  const orientation: Orientation = isSplit(root) ? orientationOf(root.direction) : 'HORIZONTAL'
  const length = orientation === 'HORIZONTAL' ? box.width : box.height
  const cross = orientation === 'HORIZONTAL' ? box.height : box.width
  const rootNode = branch(isSplit(root) ? root.children : [root], orientation, length, cross, cross)
  return {
    layout: {
      grid: { root: rootNode, width: box.width, height: box.height, orientation } as SerializedDockview['grid'],
      panels,
    },
    expandedSizes,
  }
}

/**
 * Reads dockview's layout back as an arrangement. Sizes are dockview's px, which work as
 * weights; a collapsed cell reports the size it will open to, not its strip.
 */
export function fromDockview(
  layout: SerializedDockview,
  collapsed: ReadonlySet<string>,
  expandedSizes: ReadonlyMap<string, number>
): LayoutArrangement {
  const visit = (node: GridNode, orientation: Orientation): LayoutNode => {
    if (node.type === 'branch') {
      const children = (node.data as GridNode[]).map((child) => visit(child, flip(orientation)))
      return { direction: directionOf(orientation), children, size: node.size }
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
  // The root branch's own size is the cross extent, not a weight.
  const node =
    root.type === 'branch'
      ? {
          direction: directionOf(layout.grid.orientation as Orientation),
          children: (root.data as GridNode[]).map((child) =>
            visit(child, flip(layout.grid.orientation as Orientation))
          ),
        }
      : visit(root, layout.grid.orientation as Orientation)
  if (isSplit(node) && node.children.length === 0) return { root: null }
  return { root: node }
}
