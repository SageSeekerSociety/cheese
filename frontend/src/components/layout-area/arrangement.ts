/**
 * The arrangement of a layout area: which cells exist, how they split the space, which
 * panels sit in each cell as tabs. Plain data, serialisable as it is, and the only thing
 * the page and the browser's storage ever see — dockview's own JSON stays inside the
 * adapter (`dockviewJson.ts`).
 *
 * Everything here is pure: the adapter derives an arrangement from dockview after each
 * change and runs it through `reconcile`, the narrow layout edits it directly, and both
 * are tested without a browser.
 */

export type SplitDirection = 'row' | 'column'

/** One cell: a strip of tabs over the active panel. */
export interface LayoutCell {
  /** Stable while the page is open; assigned by the layer when missing. */
  id?: string
  /** Panel ids, in tab order. */
  panels: string[]
  active?: string
  /** Folded to a thin strip; it keeps its panels and its size for when it opens again. */
  collapsed?: boolean
  /** Relative weight inside the parent split. */
  size?: number
}

/** Children laid out side by side (`row`) or one above the other (`column`). */
export interface LayoutSplit {
  direction: SplitDirection
  children: LayoutNode[]
  size?: number
}

export type LayoutNode = LayoutCell | LayoutSplit

export interface LayoutArrangement {
  /** Null when every panel has been closed. */
  root: LayoutNode | null
}

export function isSplit(node: LayoutNode): node is LayoutSplit {
  return 'children' in node
}

/** Every cell, in reading order (depth first, left to right / top to bottom). */
export function cellsOf(arrangement: LayoutArrangement): LayoutCell[] {
  const out: LayoutCell[] = []
  const walk = (node: LayoutNode) => {
    if (isSplit(node)) node.children.forEach(walk)
    else out.push(node)
  }
  if (arrangement.root) walk(arrangement.root)
  return out
}

/** Panels that sit in some cell, in reading order. */
export function placedPanels(arrangement: LayoutArrangement): string[] {
  return cellsOf(arrangement).flatMap((cell) => cell.panels)
}

/** Declared panels that sit in no cell: what the "+" menu offers, in declaration order. */
export function unplacedPanels(arrangement: LayoutArrangement, declared: readonly string[]): string[] {
  const placed = new Set(placedPanels(arrangement))
  return declared.filter((id) => !placed.has(id))
}

export function cellOfPanel(arrangement: LayoutArrangement, panelId: string): LayoutCell | undefined {
  return cellsOf(arrangement).find((cell) => cell.panels.includes(panelId))
}

export function findCell(arrangement: LayoutArrangement, cellId: string): LayoutCell | undefined {
  return cellsOf(arrangement).find((cell) => cell.id === cellId)
}

/** True when a panel appears twice or an undeclared panel appears at all. */
export function hasPlacementFault(arrangement: LayoutArrangement, declared: readonly string[]): boolean {
  const known = new Set(declared)
  const seen = new Set<string>()
  for (const id of placedPanels(arrangement)) {
    if (!known.has(id) || seen.has(id)) return true
    seen.add(id)
  }
  return false
}

/** Missing sizes take the mean of the given ones, so a half-specified split still adds up. */
function weights(nodes: readonly LayoutNode[]): number[] {
  const given = nodes.map((n) => n.size).filter((s): s is number => typeof s === 'number' && s > 0)
  const fallback = given.length ? given.reduce((a, b) => a + b, 0) / given.length : 1
  return nodes.map((n) => (typeof n.size === 'number' && n.size > 0 ? n.size : fallback))
}

function isCollapsedCell(node: LayoutNode): boolean {
  return !isSplit(node) && node.collapsed === true
}

/**
 * The arrangement with its invariants restored:
 *
 * - every declared panel is in exactly one cell or in none (then it is listed under "+");
 *   undeclared panels and second occurrences are dropped, never added;
 * - no empty cell, no empty split, no split with a single child, no split nested directly
 *   in a split of the same direction (its children are spliced in, sizes kept in proportion);
 * - each cell's active panel is one of its panels;
 * - something stays open: the root cell is never collapsed, nor every child of a split.
 */
export function reconcile(arrangement: LayoutArrangement, declared: readonly string[]): LayoutArrangement {
  const known = new Set(declared)
  const seen = new Set<string>()

  const visit = (node: LayoutNode): LayoutNode | null => {
    if (!isSplit(node)) {
      const panels = node.panels.filter((id) => known.has(id) && !seen.has(id))
      panels.forEach((id) => seen.add(id))
      if (!panels.length) return null
      const cell: LayoutCell = { panels, active: node.active && panels.includes(node.active) ? node.active : panels[0] }
      if (node.id) cell.id = node.id
      if (node.collapsed) cell.collapsed = true
      if (node.size !== undefined) cell.size = node.size
      return cell
    }
    const kept = node.children.map(visit).filter((child): child is LayoutNode => child !== null)
    const sizes = weights(kept)
    const children: LayoutNode[] = []
    kept.forEach((child, i) => {
      if (isSplit(child) && child.direction === node.direction) {
        const inner = weights(child.children)
        const total = inner.reduce((a, b) => a + b, 0)
        child.children.forEach((grand, j) => children.push({ ...grand, size: (sizes[i] * inner[j]) / total }))
      } else {
        children.push({ ...child, size: sizes[i] })
      }
    })
    if (!children.length) return null
    if (children.length === 1) {
      const only = { ...children[0] }
      if (node.size !== undefined) only.size = node.size
      else delete only.size
      return only
    }
    if (children.every(isCollapsedCell)) {
      const last = children[children.length - 1] as LayoutCell
      children[children.length - 1] = { ...last, collapsed: undefined }
      delete (children[children.length - 1] as LayoutCell).collapsed
    }
    const split: LayoutSplit = { direction: node.direction, children }
    if (node.size !== undefined) split.size = node.size
    return split
  }

  let root = arrangement.root ? visit(arrangement.root) : null
  if (root) {
    root = { ...root }
    delete root.size
    if (!isSplit(root)) delete root.collapsed
  }
  return { root }
}

/** Gives every cell an id, keeping the ones it has unless two cells share one. */
export function withCellIds(arrangement: LayoutArrangement): LayoutArrangement {
  const used = new Set<string>()
  let next = 1
  const fresh = () => {
    while (used.has(`cell-${next}`)) next++
    return `cell-${next++}`
  }
  cellsOf(arrangement).forEach((cell) => cell.id && used.add(cell.id))
  const taken = new Set<string>()
  const visit = (node: LayoutNode): LayoutNode => {
    if (isSplit(node)) return { ...node, children: node.children.map(visit) }
    let id = node.id
    if (!id || taken.has(id)) id = fresh()
    taken.add(id)
    used.add(id)
    return { ...node, id }
  }
  return { root: arrangement.root ? visit(arrangement.root) : null }
}

function mapCells(arrangement: LayoutArrangement, fn: (cell: LayoutCell) => LayoutCell): LayoutArrangement {
  const visit = (node: LayoutNode): LayoutNode =>
    isSplit(node) ? { ...node, children: node.children.map(visit) } : fn(node)
  return { root: arrangement.root ? visit(arrangement.root) : null }
}

function parentOf(arrangement: LayoutArrangement, cellId: string): LayoutSplit | null {
  let found: LayoutSplit | null = null
  const walk = (node: LayoutNode, parent: LayoutSplit | null) => {
    if (isSplit(node)) node.children.forEach((child) => walk(child, node))
    else if (node.id === cellId) found = parent
  }
  if (arrangement.root) walk(arrangement.root, null)
  return found
}

/** The direction of the split a cell sits in; undefined for a cell that is the whole area. */
export function parentDirection(arrangement: LayoutArrangement, cellId: string): SplitDirection | undefined {
  return parentOf(arrangement, cellId)?.direction
}

/** A cell can fold away only when a sibling stays open to take the space. */
export function canCollapse(arrangement: LayoutArrangement, cellId: string): boolean {
  const cell = findCell(arrangement, cellId)
  const parent = parentOf(arrangement, cellId)
  if (!cell || cell.collapsed || !parent) return false
  return parent.children.some((child) => child !== cell && !isCollapsedCell(child))
}

/** A collapsed cell shows no tab strip and takes no drops: a tab dragged onto it would vanish. */
export function acceptsDrop(arrangement: LayoutArrangement, cellId: string): boolean {
  const cell = findCell(arrangement, cellId)
  return !!cell && !cell.collapsed
}

export function setCollapsed(arrangement: LayoutArrangement, cellId: string, collapsed: boolean): LayoutArrangement {
  if (collapsed && !canCollapse(arrangement, cellId)) return arrangement
  return mapCells(arrangement, (cell) => {
    if (cell.id !== cellId) return cell
    const next = { ...cell }
    if (collapsed) next.collapsed = true
    else delete next.collapsed
    return next
  })
}

/** Makes a placed panel the visible tab of its cell. */
export function activatePanel(arrangement: LayoutArrangement, panelId: string): LayoutArrangement {
  return mapCells(arrangement, (cell) => (cell.panels.includes(panelId) ? { ...cell, active: panelId } : cell))
}

/**
 * Brings an unplaced panel back: into the cell of `nearPanelId` when given, else into the
 * first open cell, else as the only cell. It becomes the visible tab there. A panel that
 * is already placed is left where it is.
 */
export function placePanel(arrangement: LayoutArrangement, panelId: string, nearPanelId?: string): LayoutArrangement {
  if (placedPanels(arrangement).includes(panelId)) return arrangement
  if (!arrangement.root) return { root: { panels: [panelId], active: panelId } }
  const cells = cellsOf(arrangement)
  const target =
    (nearPanelId && cells.find((cell) => cell.panels.includes(nearPanelId))) ||
    cells.find((cell) => !cell.collapsed) ||
    cells[0]
  let done = false
  return mapCells(arrangement, (cell) => {
    if (done || cell !== target) return cell
    done = true
    return { ...cell, panels: [...cell.panels, panelId], active: panelId }
  })
}

/**
 * Places a panel that has just appeared in the cell where the other panels of its group
 * are — the cell holding most of them, the first such in reading order on a tie — behind
 * that cell's visible tab. With no such cell it goes where `placePanel` would put it. A
 * panel already placed stays put.
 */
export function placeInGroup(
  arrangement: LayoutArrangement,
  panelId: string,
  groupOf: (id: string) => string | undefined
): LayoutArrangement {
  if (placedPanels(arrangement).includes(panelId)) return arrangement
  const home = homeCell(arrangement, groupOf(panelId), groupOf)
  if (!home) return placePanel(arrangement, panelId)
  return mapCells(arrangement, (cell) => (cell === home ? { ...cell, panels: [...cell.panels, panelId] } : cell))
}

/**
 * Places a panel that has just appeared in a cell of its own, to the right of the cell
 * holding `nearPanelId`: beside it in that row, or in a new row with it. With that panel
 * not placed it goes where `placePanel` would put it. A panel already placed stays put.
 */
export function placeBeside(arrangement: LayoutArrangement, panelId: string, nearPanelId: string): LayoutArrangement {
  if (placedPanels(arrangement).includes(panelId)) return arrangement
  const near = cellOfPanel(arrangement, nearPanelId)
  if (!near || !arrangement.root) return placePanel(arrangement, panelId)
  const fresh: LayoutCell = { panels: [panelId], active: panelId }
  const visit = (node: LayoutNode): LayoutNode => {
    if (!isSplit(node)) return node === near ? { direction: 'row', children: [node, fresh], size: node.size } : node
    const at = node.children.indexOf(near)
    if (at >= 0 && node.direction === 'row') {
      const children = [...node.children]
      children.splice(at + 1, 0, fresh)
      return { ...node, children }
    }
    return { ...node, children: node.children.map(visit) }
  }
  return { root: visit(arrangement.root) }
}

/**
 * The cell a panel opened for a spawning panel lands in, by id: beside the panel it names,
 * in its group's home cell, or where a newly placed panel goes when the group has none.
 * Undefined when that is a cell that does not exist yet.
 */
export function spawnLanding(
  arrangement: LayoutArrangement,
  spawner: { id: string; group?: string; beside?: string },
  groupOf: (id: string) => string | undefined
): string | undefined {
  if (spawner.beside) return cellOfPanel(arrangement, spawner.beside)?.id
  const home = homeCell(arrangement, spawner.group, groupOf)
  return (home ?? cellOfPanel(placePanel(arrangement, spawner.id), spawner.id))?.id
}

/** A group's home: the cell holding most of its panels (the first such, in reading order). */
export function homeCell(
  arrangement: LayoutArrangement,
  group: string | undefined,
  groupOf: (id: string) => string | undefined
): LayoutCell | undefined {
  if (!group) return undefined
  const members = (cell: LayoutCell) => cell.panels.filter((id) => groupOf(id) === group).length
  return cellsOf(arrangement).reduce<LayoutCell | undefined>(
    (best, cell) => (members(cell) > (best ? members(best) : 0) ? cell : best),
    undefined
  )
}

/** What a cell's panels say about its tab strip. */
export interface StripPanel {
  bare?: boolean
  stripWhenShared?: boolean
}

/**
 * A cell shows no tab strip when its panels are all bare, or when it holds one panel that
 * asks for a strip only once it shares the cell: there is nothing to pick between.
 */
export function hidesTabs(panels: readonly StripPanel[]): boolean {
  if (!panels.length) return false
  return panels.every((panel) => panel.bare) || (panels.length === 1 && panels[0].stripWhenShared === true)
}

/**
 * The same arrangement for the reader: the same cells, splits, tab order and folds.
 * Sizes, the visible tab and cell ids do not count.
 */
export function sameArrangement(a: LayoutArrangement, b: LayoutArrangement): boolean {
  const shape = (node: LayoutNode | null): unknown =>
    !node
      ? null
      : isSplit(node)
        ? { d: node.direction, c: node.children.map(shape) }
        : { p: node.panels, f: !!node.collapsed }
  return JSON.stringify(shape(a.root)) === JSON.stringify(shape(b.root))
}

// ---------------------------------------------------------------- back to the default sizes

function panelsUnder(node: LayoutNode): string[] {
  return isSplit(node) ? node.children.flatMap(panelsUnder) : node.panels
}

const sameSet = (a: readonly string[], b: readonly string[]) =>
  a.length === b.length && [...a].sort().join('\n') === [...b].sort().join('\n')

/**
 * The weights the page's default gives a split whose children hold these panels (one
 * list per child, in order), or null when the default has no such split — the reader
 * has arranged those panels in a way the page never offered.
 */
export function defaultWeights(defaults: LayoutArrangement, children: readonly (readonly string[])[]): number[] | null {
  const walk = (node: LayoutNode): number[] | null => {
    if (!isSplit(node)) return null
    const matches =
      node.children.length === children.length && node.children.every((c, i) => sameSet(panelsUnder(c), children[i]))
    if (matches) return weights(node.children)
    for (const child of node.children) {
      const inner = walk(child)
      if (inner) return inner
    }
    return null
  }
  return defaults.root ? walk(defaults.root) : null
}

/**
 * The sizes a split's children go back to, in px out of the `total` they fill now: a
 * child with a size of its own gets it (never more than the total leaves room for), the
 * others share the rest by their default weights.
 */
export function resetSizes(
  total: number,
  weights: readonly number[],
  fixed: readonly (number | undefined)[]
): number[] {
  let free = total
  const out = weights.map((_, i) => {
    const own = fixed[i]
    if (own === undefined) return 0
    const size = Math.max(0, Math.min(own, free))
    free -= size
    return size
  })
  const shared = weights.reduce((sum, w, i) => sum + (fixed[i] === undefined ? w : 0), 0)
  return out.map((size, i) => (fixed[i] === undefined && shared > 0 ? Math.round((weights[i] / shared) * free) : size))
}

// ---------------------------------------------------------------- persistence

export interface RestoreOptions {
  /** The page's own version of its arrangement; anything saved under another is ignored. */
  version: number
  /** Every panel the page declares. */
  declared: readonly string[]
  /** Panels that must be placed (they cannot be closed): a saved arrangement without one is ignored. */
  required: readonly string[]
}

export function serializeArrangement(arrangement: LayoutArrangement, version: number): string {
  return JSON.stringify({ v: version, root: arrangement.root })
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function validSize(value: unknown): boolean {
  return value === undefined || (typeof value === 'number' && Number.isFinite(value) && value > 0)
}

function readNode(value: unknown): LayoutNode | null {
  if (!isRecord(value) || !validSize(value.size)) return null
  if ('children' in value) {
    if (value.direction !== 'row' && value.direction !== 'column') return null
    if (!Array.isArray(value.children) || !value.children.length) return null
    const children = value.children.map(readNode)
    if (children.some((child) => child === null)) return null
    const split: LayoutSplit = { direction: value.direction, children: children as LayoutNode[] }
    if (value.size !== undefined) split.size = value.size as number
    return split
  }
  const { panels, active, collapsed, id } = value
  if (!Array.isArray(panels) || !panels.length || !panels.every((p) => typeof p === 'string')) return null
  if (active !== undefined && typeof active !== 'string') return null
  if (collapsed !== undefined && typeof collapsed !== 'boolean') return null
  if (id !== undefined && typeof id !== 'string') return null
  const cell: LayoutCell = { panels: [...panels] }
  if (id !== undefined) cell.id = id
  if (active !== undefined) cell.active = active
  if (collapsed) cell.collapsed = true
  if (value.size !== undefined) cell.size = value.size as number
  return cell
}

/**
 * What was saved, or null when it cannot be used as it is: not JSON, another version,
 * a shape this code does not write, an undeclared panel, a panel twice, or a panel that
 * cannot be closed missing. The caller then starts from the page's default; nothing is half-applied.
 */
export function restoreArrangement(raw: string | null, options: RestoreOptions): LayoutArrangement | null {
  if (!raw) return null
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }
  if (!isRecord(parsed) || parsed.v !== options.version) return null
  let root: LayoutNode | null = null
  if (parsed.root !== null) {
    root = readNode(parsed.root)
    if (!root) return null
  }
  const arrangement: LayoutArrangement = { root }
  if (hasPlacementFault(arrangement, options.declared)) return null
  const placed = new Set(placedPanels(arrangement))
  if (options.required.some((id) => !placed.has(id))) return null
  return reconcile(arrangement, options.declared)
}
