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
  /**
   * Cells popped out of the grid into windows floating over it, each in a place of its own
   * that the area remembers per panel. Left out when there are none.
   */
  floating?: LayoutCell[]
}

export function isSplit(node: LayoutNode): node is LayoutSplit {
  return 'children' in node
}

/** The cells of the grid, in reading order (depth first, left to right / top to bottom). */
export function gridCellsOf(arrangement: LayoutArrangement): LayoutCell[] {
  const out: LayoutCell[] = []
  const walk = (node: LayoutNode) => {
    if (isSplit(node)) node.children.forEach(walk)
    else out.push(node)
  }
  if (arrangement.root) walk(arrangement.root)
  return out
}

/** Every cell: the grid's in reading order, then the floating ones. */
export function cellsOf(arrangement: LayoutArrangement): LayoutCell[] {
  return [...gridCellsOf(arrangement), ...(arrangement.floating ?? [])]
}

/** The same arrangement with another grid; the floating cells stay as they are. */
function withRoot(arrangement: LayoutArrangement, root: LayoutNode | null): LayoutArrangement {
  return arrangement.floating?.length ? { root, floating: arrangement.floating } : { root }
}

/** The cell holding this panel floats over the grid. */
export function isFloating(arrangement: LayoutArrangement, panelId: string): boolean {
  return !!arrangement.floating?.some((cell) => cell.panels.includes(panelId))
}

/** The panels in floating cells. */
export function floatingPanels(arrangement: LayoutArrangement): string[] {
  return (arrangement.floating ?? []).flatMap((cell) => cell.panels)
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

/** The grid cell holding this panel; undefined when it floats or sits nowhere. */
function gridCellOfPanel(arrangement: LayoutArrangement, panelId: string): LayoutCell | undefined {
  return gridCellsOf(arrangement).find((cell) => cell.panels.includes(panelId))
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
 * - something stays open: the root cell is never collapsed, nor every child of a split;
 * - a floating cell is never collapsed and has no weight; a panel in the grid and in a floating
 *   cell stays in the grid.
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
  const floating = (arrangement.floating ?? [])
    .map((cell) => visit({ panels: cell.panels, active: cell.active, ...(cell.id ? { id: cell.id } : {}) }))
    .filter((cell): cell is LayoutCell => cell !== null)
  return floating.length ? { root, floating } : { root }
}

/** Gives every cell an id (floating ones too), keeping the ones it has unless two cells share one. */
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
  const root = arrangement.root ? visit(arrangement.root) : null
  const floating = (arrangement.floating ?? []).map((cell) => visit(cell) as LayoutCell)
  return floating.length ? { root, floating } : { root }
}

function mapCells(arrangement: LayoutArrangement, fn: (cell: LayoutCell) => LayoutCell): LayoutArrangement {
  const visit = (node: LayoutNode): LayoutNode =>
    isSplit(node) ? { ...node, children: node.children.map(visit) } : fn(node)
  const root = arrangement.root ? visit(arrangement.root) : null
  const floating = (arrangement.floating ?? []).map(fn)
  return floating.length ? { root, floating } : { root }
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
  if (!arrangement.root) return withRoot(arrangement, { panels: [panelId], active: panelId })
  const cells = gridCellsOf(arrangement)
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
  const near = gridCellOfPanel(arrangement, nearPanelId)
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
  return withRoot(arrangement, visit(arrangement.root))
}

/**
 * Places a panel in a new cell of its own directly below the cell holding `nearPanelId`:
 * under it in that column, or in a new column with it. With that panel not placed, the
 * panel goes where any newly placed panel goes.
 */
export function placeBelow(arrangement: LayoutArrangement, panelId: string, nearPanelId: string): LayoutArrangement {
  if (placedPanels(arrangement).includes(panelId)) return arrangement
  const near = gridCellOfPanel(arrangement, nearPanelId)
  if (!near || !arrangement.root) return placePanel(arrangement, panelId)
  const fresh: LayoutCell = { panels: [panelId], active: panelId }
  const visit = (node: LayoutNode): LayoutNode => {
    // The panel it lands under keeps the larger share: what opens below is the helper.
    if (!isSplit(node)) {
      if (node !== near) return node
      return {
        direction: 'column',
        children: [
          { ...node, size: 70 },
          { ...fresh, size: 30 },
        ],
        size: node.size,
      }
    }
    const at = node.children.indexOf(near)
    if (at >= 0 && node.direction === 'column') {
      const children = [...node.children]
      children.splice(at + 1, 0, fresh)
      return { ...node, children }
    }
    return { ...node, children: node.children.map(visit) }
  }
  return withRoot(arrangement, visit(arrangement.root))
}

/**
 * Where a transient panel goes when it appears: beside the panel it names; in its group's
 * home cell; when the group has no cell yet, below the panel it names; otherwise where any
 * newly placed panel goes.
 */
export function placeTransient(
  arrangement: LayoutArrangement,
  panel: { id: string; group?: string; beside?: string; below?: string },
  groupOf: (id: string) => string | undefined
): LayoutArrangement {
  if (panel.beside) return placeBeside(arrangement, panel.id, panel.beside)
  if (panel.below && !homeCell(arrangement, panel.group, groupOf)) return placeBelow(arrangement, panel.id, panel.below)
  return placeInGroup(arrangement, panel.id, groupOf)
}

// ---------------------------------------------------------------- floating

/** The arrangement without this panel among the floating cells; a floating cell left empty goes. */
export function withoutFloating(arrangement: LayoutArrangement, panelId: string): LayoutArrangement {
  const floating = (arrangement.floating ?? [])
    .map((cell) => {
      const panels = cell.panels.filter((id) => id !== panelId)
      return { ...cell, panels, active: cell.active === panelId ? panels[0] : cell.active }
    })
    .filter((cell) => cell.panels.length > 0)
  return floating.length ? { root: arrangement.root, floating } : { root: arrangement.root }
}

/** The nodes from the root down to the cell holding this panel, or null. */
function pathTo(node: LayoutNode | null, panelId: string): LayoutNode[] | null {
  if (!node) return null
  if (!isSplit(node)) return node.panels.includes(panelId) ? [node] : null
  for (const child of node.children) {
    const below = pathTo(child, panelId)
    if (below) return [node, ...below]
  }
  return null
}

/**
 * Puts a panel that sits in no grid cell back where the page's default arrangement has it:
 * into the cell of a panel it shares its default cell with; else in a cell of its own
 * beside what stands for its nearest default neighbour — the same side, the same direction.
 * When nothing of its default surroundings is in the grid, or the default does not place
 * it, it goes where `placePanel` puts a panel.
 */
export function placeAsInDefault(
  arrangement: LayoutArrangement,
  panelId: string,
  defaults: LayoutArrangement
): LayoutArrangement {
  if (gridCellOfPanel(arrangement, panelId)) return arrangement
  const path = pathTo(defaults.root, panelId)
  if (!arrangement.root || !path) return placePanel(arrangement, panelId)
  const inGrid = new Set(gridCellsOf(arrangement).flatMap((cell) => cell.panels))
  const home = path[path.length - 1] as LayoutCell
  const mate = home.panels.find((id) => id !== panelId && inGrid.has(id))
  if (mate) {
    return mapCells(arrangement, (cell) =>
      gridCellOfPanel(arrangement, mate) === cell
        ? { ...cell, panels: [...cell.panels, panelId], active: panelId }
        : cell
    )
  }
  const fresh: LayoutCell = { panels: [panelId], active: panelId }
  for (let depth = path.length - 1; depth > 0; depth--) {
    const parent = path[depth - 1] as LayoutSplit
    const at = parent.children.indexOf(path[depth])
    // Nearest first; on a tie the one before, so the panel lands after it as in the default.
    const order = parent.children
      .map((_, j) => j)
      .filter((j) => j !== at)
      .sort((a, b) => Math.abs(a - at) - Math.abs(b - at) || a - b)
    for (const j of order) {
      const sibling = new Set(panelsUnder(parent.children[j]))
      const anchor = [...sibling].find((id) => inGrid.has(id))
      if (!anchor) continue
      const stand = standIn(arrangement.root, anchor, sibling)
      return withRoot(arrangement, insertBeside(arrangement.root, stand, fresh, parent.direction, j > at))
    }
  }
  return placePanel(arrangement, panelId)
}

/**
 * What stands in the grid for a default node holding `allowed`: the highest node above the
 * cell of `panelId` whose panels are all among them.
 */
function standIn(root: LayoutNode, panelId: string, allowed: ReadonlySet<string>): LayoutNode {
  const path = pathTo(root, panelId) as LayoutNode[]
  let stand = path[path.length - 1]
  for (let i = path.length - 2; i >= 0; i--) {
    if (!panelsUnder(path[i]).every((id) => allowed.has(id))) break
    stand = path[i]
  }
  return stand
}

/** Inserts `fresh` next to `anchor` along `direction`: before it or after it. */
function insertBeside(
  root: LayoutNode,
  anchor: LayoutNode,
  fresh: LayoutCell,
  direction: SplitDirection,
  before: boolean
): LayoutNode {
  const pair = (node: LayoutNode): LayoutSplit => ({
    direction,
    children: before ? [fresh, { ...node, size: undefined }] : [{ ...node, size: undefined }, fresh],
    ...(node.size !== undefined ? { size: node.size } : {}),
  })
  if (root === anchor) return pair(root)
  const visit = (node: LayoutNode): LayoutNode => {
    if (!isSplit(node)) return node
    const at = node.children.indexOf(anchor)
    if (at < 0) return { ...node, children: node.children.map(visit) }
    const children = [...node.children]
    if (node.direction === direction) children.splice(before ? at : at + 1, 0, fresh)
    else children[at] = pair(anchor)
    return { ...node, children }
  }
  return visit(root)
}

/**
 * The cell a panel opened for a spawning panel lands in, by id: beside the panel it names,
 * in its group's home cell, or where a newly placed panel goes when the group has none.
 * Undefined when that is a cell that does not exist yet (beside a panel, or below one).
 */
export function spawnLanding(
  arrangement: LayoutArrangement,
  spawner: { id: string; group?: string; beside?: string; below?: string },
  groupOf: (id: string) => string | undefined
): string | undefined {
  if (spawner.beside) return cellOfPanel(arrangement, spawner.beside)?.id
  const home = homeCell(arrangement, spawner.group, groupOf)
  if (spawner.below) return home?.id
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
  titled?: boolean
  stripWhenShared?: boolean
}

/**
 * A cell shows no tab strip when its panels are all bare or carry a title bar of their own,
 * or when it holds one panel that asks for a strip only once it shares the cell: there is
 * nothing to pick between.
 */
export function hidesTabs(panels: readonly StripPanel[]): boolean {
  if (!panels.length) return false
  return (
    panels.every((panel) => panel.bare || panel.titled) || (panels.length === 1 && panels[0].stripWhenShared === true)
  )
}

/**
 * The same arrangement for the reader: the same cells, splits, tab order, folds and floating cells.
 * Sizes, the visible tab and cell ids do not count.
 */
export function sameArrangement(a: LayoutArrangement, b: LayoutArrangement): boolean {
  const shape = (node: LayoutNode | null): unknown =>
    !node
      ? null
      : isSplit(node)
        ? { d: node.direction, c: node.children.map(shape) }
        : { p: node.panels, f: !!node.collapsed }
  const whole = (arrangement: LayoutArrangement) => ({
    root: shape(arrangement.root),
    floating: (arrangement.floating ?? []).map((cell) => cell.panels),
  })
  return JSON.stringify(whole(a)) === JSON.stringify(whole(b))
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
 * The weights the page's default gives some of a split's children — the ones in sight,
 * the others hidden — in order: each list of panels names the default child it shares a
 * panel with. Null when no split of the default holds them all, each in a child of its own.
 */
export function defaultWeightsAmong(
  defaults: LayoutArrangement,
  children: readonly (readonly string[])[]
): number[] | null {
  const walk = (node: LayoutNode): number[] | null => {
    if (!isSplit(node)) return null
    const at = children.map((panels) =>
      node.children.findIndex((child) => panelsUnder(child).some((id) => panels.includes(id)))
    )
    const ordered = at.every((i, k) => i >= 0 && (k === 0 || i > at[k - 1]))
    if (ordered && children.length > 0) {
      const all = weights(node.children)
      return at.map((i) => all[i])
    }
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
  return JSON.stringify({
    v: version,
    root: arrangement.root,
    ...(arrangement.floating?.length ? { floating: arrangement.floating } : {}),
  })
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
  let floating: LayoutCell[] = []
  if (parsed.floating !== undefined) {
    if (!Array.isArray(parsed.floating)) return null
    const cells = parsed.floating.map(readNode)
    if (cells.some((cell) => !cell || isSplit(cell))) return null
    floating = cells as LayoutCell[]
  }
  const arrangement: LayoutArrangement = floating.length ? { root, floating } : { root }
  if (hasPlacementFault(arrangement, options.declared)) return null
  const placed = new Set(placedPanels(arrangement))
  if (options.required.some((id) => !placed.has(id))) return null
  return reconcile(arrangement, options.declared)
}
