import type { LayoutArrangement, LayoutCell } from './arrangement'

import { describe, expect, it } from 'vitest'

import {
  acceptsDrop,
  canCollapse,
  cellsOf,
  defaultWeights,
  placedPanels,
  placeInGroup,
  placePanel,
  reconcile,
  resetSizes,
  restoreArrangement,
  serializeArrangement,
  setCollapsed,
  unplacedPanels,
  withCellIds,
} from './arrangement'
import { fromDockview, STRIP_SIZE, toDockview } from './dockviewJson'

const DECLARED = ['nav', 'chat', 'files', 'preview', 'terminal']
const OPTIONS = { version: 3, declared: DECLARED, required: ['nav', 'chat'] }

const DEFAULT: LayoutArrangement = {
  root: {
    direction: 'row',
    children: [
      { id: 'left', panels: ['nav'], size: 1 },
      { id: 'middle', panels: ['chat'], size: 2 },
      {
        direction: 'column',
        size: 2,
        children: [
          { id: 'top', panels: ['files', 'preview'], active: 'preview', size: 3 },
          { id: 'bottom', panels: ['terminal'], size: 1 },
        ],
      },
    ],
  },
}

/** Each declared panel is in exactly one cell or listed under "+". */
function holdsEveryPanelOnce(arrangement: LayoutArrangement, declared: readonly string[]) {
  const placed = placedPanels(arrangement)
  const unplaced = unplacedPanels(arrangement, declared)
  expect(new Set(placed).size).toBe(placed.length)
  expect([...placed, ...unplaced].sort()).toEqual([...declared].sort())
}

describe('saving and restoring an arrangement', () => {
  it('comes back exactly as it was saved', () => {
    const raw = serializeArrangement(DEFAULT, 3)
    expect(restoreArrangement(raw, OPTIONS)).toEqual(reconcile(DEFAULT, DECLARED))
  })

  it('keeps a collapsed cell collapsed', () => {
    const folded = setCollapsed(DEFAULT, 'left', true)
    const restored = restoreArrangement(serializeArrangement(folded, 3), OPTIONS)!
    expect(cellsOf(restored).find((cell) => cell.id === 'left')?.collapsed).toBe(true)
  })

  it.each([
    ['nothing saved', null],
    ['not JSON', '{oops'],
    ['another version', serializeArrangement(DEFAULT, 2)],
    ['a shape this code does not write', JSON.stringify({ v: 3, root: { cells: [] } })],
    ['a cell with no panels', JSON.stringify({ v: 3, root: { panels: [] } })],
    ['a panel the page no longer declares', JSON.stringify({ v: 3, root: { panels: ['nav', 'gone'] } })],
    [
      'a panel twice',
      JSON.stringify({
        v: 3,
        root: { direction: 'row', children: [{ panels: ['nav'] }, { panels: ['nav', 'chat'] }] },
      }),
    ],
    ['a panel that cannot be closed missing', JSON.stringify({ v: 3, root: { panels: ['chat'] } })],
  ])('is ignored when it is %s, so the page starts from its default', (_case, raw) => {
    expect(restoreArrangement(raw, OPTIONS)).toBeNull()
  })

  it('accepts a save in which panels were closed, and lists them under "+"', () => {
    const raw = JSON.stringify({
      v: 3,
      root: { direction: 'row', children: [{ panels: ['nav'] }, { panels: ['chat'] }] },
    })
    const restored = restoreArrangement(raw, OPTIONS)!
    expect(unplacedPanels(restored, DECLARED)).toEqual(['files', 'preview', 'terminal'])
  })
})

describe('reconciling', () => {
  const messy: LayoutArrangement = {
    root: {
      direction: 'row',
      children: [
        { panels: ['nav', 'ghost'] },
        { panels: [] },
        { direction: 'row', children: [{ panels: ['chat', 'nav'] }, { panels: ['files'], active: 'terminal' }] },
        { direction: 'column', children: [{ panels: ['chat'] }] },
      ],
    },
  }

  it('leaves every declared panel in exactly one cell or under "+"', () => {
    for (const arrangement of [messy, DEFAULT, { root: null }, { root: { panels: ['x'] } }]) {
      holdsEveryPanelOnce(reconcile(arrangement, DECLARED), DECLARED)
    }
  })

  it('drops undeclared panels and second occurrences, never adds one', () => {
    const fixed = reconcile(messy, DECLARED)
    expect(placedPanels(fixed)).toEqual(['nav', 'chat', 'files'])
    expect(unplacedPanels(fixed, DECLARED)).toEqual(['preview', 'terminal'])
  })

  it('makes the active tab one of the cell’s own panels', () => {
    const fixed = reconcile(messy, DECLARED)
    for (const cell of cellsOf(fixed)) expect(cell.panels).toContain(cell.active)
  })

  it('keeps something open: a lone cell or a split of only collapsed cells opens one', () => {
    const lone = reconcile({ root: { panels: ['chat'], collapsed: true } }, DECLARED)
    expect(cellsOf(lone)[0].collapsed).toBeFalsy()
    const allFolded = reconcile(
      {
        root: {
          direction: 'row',
          children: [
            { panels: ['nav'], collapsed: true },
            { panels: ['chat'], collapsed: true },
          ],
        },
      },
      DECLARED
    )
    expect(cellsOf(allFolded).some((cell) => !cell.collapsed)).toBe(true)
  })

  it('a closed-then-reopened panel is placed once and becomes visible', () => {
    const closed = reconcile(
      { root: { direction: 'row', children: [{ panels: ['nav'] }, { panels: ['chat'] }] } },
      DECLARED
    )
    const reopened = placePanel(closed, 'files', 'chat')
    holdsEveryPanelOnce(reopened, DECLARED)
    const cell = cellsOf(reopened).find((c) => c.panels.includes('files'))!
    expect(cell.panels).toContain('chat')
    expect(cell.active).toBe('files')
    expect(placePanel(reopened, 'files')).toBe(reopened)
  })

  it('a panel brought back into an empty area gets a cell of its own', () => {
    expect(placedPanels(placePanel({ root: null }, 'chat'))).toEqual(['chat'])
  })
})

describe('collapsing', () => {
  const area = withCellIds(DEFAULT)

  it('a collapsed cell accepts no drops; an open one does', () => {
    const folded = setCollapsed(area, 'middle', true)
    expect(acceptsDrop(folded, 'middle')).toBe(false)
    expect(acceptsDrop(folded, 'left')).toBe(true)
  })

  it('a cell can fold only while a sibling stays open', () => {
    const pair = withCellIds({
      root: {
        direction: 'row',
        children: [
          { id: 'a', panels: ['nav'] },
          { id: 'b', panels: ['chat'] },
        ],
      },
    })
    expect(canCollapse(pair, 'a')).toBe(true)
    const oneFolded = setCollapsed(pair, 'a', true)
    expect(canCollapse(oneFolded, 'b')).toBe(false)
    expect(setCollapsed(oneFolded, 'b', true)).toBe(oneFolded)
  })

  it('the whole area cannot fold', () => {
    const single = withCellIds({ root: { id: 'only', panels: ['chat'] } })
    expect(canCollapse(single, 'only')).toBe(false)
  })
})

describe('dockview layout round trip', () => {
  const panelState = (id: string) => ({ id, contentComponent: 'host', title: id })
  const box = { width: 1000, height: 600 }

  it('reads back the same cells, tabs, active tabs and directions it laid out', () => {
    const area = withCellIds(reconcile(DEFAULT, DECLARED))
    const { layout } = toDockview({ root: area.root! }, box, panelState)
    const back = reconcile(fromDockview(layout, new Set(), new Map()), DECLARED)
    const shape = (a: LayoutArrangement) => cellsOf(a).map(({ id, panels, active }) => ({ id, panels, active }))
    expect(shape(back)).toEqual(shape(area))
    expect(back.root && 'direction' in back.root && back.root.direction).toBe('row')
  })

  it('keeps sizes in proportion', () => {
    const area = withCellIds(DEFAULT)
    const { layout } = toDockview({ root: area.root! }, box, panelState)
    const sizes = cellsOf(fromDockview(layout, new Set(), new Map())).map((cell) => cell.size!)
    // left : middle : right column = 1 : 2 : 2 of 1000px; top : bottom = 3 : 1 of 600px
    expect(sizes).toEqual([200, 400, 450, 150])
  })

  it('lays a collapsed cell out as a strip and remembers the size it opens to', () => {
    const area = setCollapsed(withCellIds(DEFAULT), 'left', true)
    const { layout, expandedSizes } = toDockview({ root: area.root! }, box, panelState)
    expect(expandedSizes.get('left')).toBe(200)
    const back = fromDockview(layout, new Set(['left']), expandedSizes)
    const left = cellsOf(back).find((cell) => cell.id === 'left')!
    expect(left.collapsed).toBe(true)
    expect(left.size).toBe(200)
    const rawLeft = (layout.grid.root.data as Array<{ size?: number }>)[0]
    expect(rawLeft.size).toBe(STRIP_SIZE)
  })
})

describe('a panel that appears while the area is open', () => {
  const groups: Record<string, string> = { files: 'work', preview: 'work', 'file:a': 'work', chat: 'talk' }
  const groupOf = (id: string) => groups[id]

  it('goes to the cell where the other panels of its group are, behind its visible tab', () => {
    const area = withCellIds(DEFAULT)
    const next = placeInGroup(area, 'file:a', groupOf)
    const cell = cellsOf(next).find((c) => c.panels.includes('file:a'))!
    expect(cell.id).toBe('top')
    expect(cell.active).toBe('preview')
  })

  it('goes to the cell with most of its group when the group is spread over several', () => {
    // One work panel dragged next to the chat; the rest of the group stays together.
    const spread = withCellIds({
      root: {
        direction: 'row',
        children: [
          { id: 'talk', panels: ['chat', 'files'] },
          { id: 'work', panels: ['preview', 'file:b'] },
        ],
      },
    })
    const next = placeInGroup(spread, 'file:a', (id) => (id === 'chat' ? 'talk' : 'work'))
    expect(cellsOf(next).find((c) => c.panels.includes('file:a'))?.id).toBe('work')
  })

  it('goes where any closed panel would when its group has no cell yet', () => {
    const lonely = withCellIds({
      root: {
        direction: 'row',
        children: [
          { id: 'a', panels: ['nav'] },
          { id: 'b', panels: ['chat'] },
        ],
      },
    })
    const next = placeInGroup(lonely, 'file:a', groupOf)
    expect(cellsOf(next).find((c) => c.panels.includes('file:a'))?.id).toBe('a')
  })

  it('stays where it is once placed', () => {
    const area = withCellIds(DEFAULT)
    expect(placeInGroup(area, 'chat', groupOf)).toBe(area)
  })
})

describe('a double-clicked divider gives its split the default sizes back', () => {
  const PAGE_DEFAULT: LayoutArrangement = {
    root: {
      direction: 'row',
      children: [
        { panels: ['nav'] },
        {
          direction: 'column',
          children: [
            { panels: ['head'] },
            {
              direction: 'row',
              children: [
                { panels: ['chat'], size: 45 },
                { panels: ['files'], size: 55 },
              ],
            },
          ],
        },
      ],
    },
  }

  it('finds the default weights of the split those panels sit in, wherever it is nested', () => {
    expect(defaultWeights(PAGE_DEFAULT, [['chat'], ['files']])).toEqual([45, 55])
    expect(defaultWeights(PAGE_DEFAULT, [['nav'], ['head', 'chat', 'files']])).toEqual([1, 1])
  })

  it('has nothing to give back for a split the default never had', () => {
    expect(defaultWeights(PAGE_DEFAULT, [['files'], ['chat']])).toBeNull()
    expect(defaultWeights(PAGE_DEFAULT, [['chat'], ['nav']])).toBeNull()
  })

  it('the cells share the space they fill now by their default weights', () => {
    expect(resetSizes(1000, [45, 55], [undefined, undefined])).toEqual([450, 550])
  })

  it('a cell with a width of its own goes back to its initial width, the rest takes what is left', () => {
    expect(resetSizes(1440, [1, 1], [280, undefined])).toEqual([280, 1160])
    expect(resetSizes(200, [1, 1], [280, undefined])).toEqual([200, 0])
  })
})

describe('cells with a width of their own, and hidden cells', () => {
  const panelState = (id: string) => ({ id, contentComponent: 'host', title: id })
  const box = { width: 1200, height: 800 }
  const ROOM: LayoutArrangement = withCellIds({
    root: {
      direction: 'row',
      children: [
        { id: 'nav', panels: ['nav'], size: 900 },
        { id: 'chat', panels: ['chat'], size: 1 },
        { id: 'files', panels: ['files'], size: 3 },
      ],
    },
  })
  type Leaf = { size: number; visible?: boolean; data: { id: string; priority?: string } }
  const leaves = (load: ReturnType<typeof toDockview>) => load.layout.grid.root.data as Leaf[]
  const fixedNav = (cell: LayoutCell) => (cell.id === 'nav' ? { fixed: 280 } : {})
  const hiddenFiles = (cell: LayoutCell) =>
    cell.id === 'nav' ? { fixed: 280 } : cell.id === 'files' ? { hidden: true } : {}

  it('a fixed cell gets its own width whatever its weight; the others share the rest by weight', () => {
    const load = toDockview({ root: ROOM.root! }, box, panelState, fixedNav)
    expect(leaves(load).map((leaf) => leaf.size)).toEqual([280, 230, 690])
  })

  it('a fixed cell is the one that keeps its width when the area resizes', () => {
    const load = toDockview({ root: ROOM.root! }, box, panelState, fixedNav)
    expect(leaves(load).map((leaf) => leaf.data.priority)).toEqual(['low', undefined, undefined])
  })

  it('a hidden cell takes no space now and keeps the share it comes back with', () => {
    const [nav, chat, files] = leaves(toDockview({ root: ROOM.root! }, box, panelState, hiddenFiles))
    expect([nav.size, chat.size]).toEqual([280, 920])
    expect(files.visible).toBe(false)
    expect(files.size).toBe(690)
  })

  it('reading a layout back while a cell is hidden leaves the weights of the others as they were', () => {
    const ratio = (load: ReturnType<typeof toDockview>) => {
      const cells = cellsOf(fromDockview(load.layout, new Set(), new Map()))
      const size = (id: string) => cells.find((cell) => cell.id === id)!.size!
      return size('chat') / size('files')
    }
    const shown = toDockview({ root: ROOM.root! }, box, panelState, fixedNav)
    const hidden = toDockview({ root: ROOM.root! }, box, panelState, hiddenFiles)
    expect(ratio(hidden)).toBeCloseTo(ratio(shown), 2)
  })
})
