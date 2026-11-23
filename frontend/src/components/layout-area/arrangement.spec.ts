import type { LayoutArrangement, LayoutCell } from './arrangement'

import { describe, expect, it } from 'vitest'

import {
  acceptsDrop,
  canCollapse,
  cellOfPanel,
  cellsOf,
  defaultWeights,
  defaultWeightsAmong,
  hidesTabs,
  homeCell,
  placeAsInDefault,
  placeBelow,
  placeBeside,
  placedPanels,
  placeInGroup,
  placePanel,
  placeTransient,
  reconcile,
  resetSizes,
  restoreArrangement,
  sameArrangement,
  serializeArrangement,
  setCollapsed,
  spawnLanding,
  unplacedPanels,
  withCellIds,
  withoutFloating,
} from './arrangement'
import { cellSizing, floatBoxes, fromDockview, STRIP_SIZE, toDockview } from './dockviewJson'

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

describe("a group's home cell", () => {
  it('is the cell holding most of the group, and moves when most of the group does', () => {
    const groupOf = (id: string) => (id === 'chat' ? undefined : 'work')
    const area = withCellIds({
      root: {
        direction: 'row',
        children: [
          { id: 'talk', panels: ['chat', 'files'] },
          { id: 'work', panels: ['preview', 'terminal'] },
        ],
      },
    })
    expect(homeCell(area, 'work', groupOf)?.id).toBe('work')
    const moved = withCellIds({
      root: {
        direction: 'row',
        children: [
          { id: 'talk', panels: ['chat', 'files', 'preview'] },
          { id: 'work', panels: ['terminal'] },
        ],
      },
    })
    expect(homeCell(moved, 'work', groupOf)?.id).toBe('talk')
    expect(homeCell(area, undefined, groupOf)).toBeUndefined()
  })
})

describe("a cell's tab strip", () => {
  it('is absent while a panel that wants one only when shared is alone, and back once another joins', () => {
    const conversation = { stripWhenShared: true }
    expect(hidesTabs([conversation])).toBe(true)
    expect(hidesTabs([conversation, {}])).toBe(false)
  })

  it('is absent over bare panels, present over ordinary ones', () => {
    expect(hidesTabs([{ bare: true }])).toBe(true)
    expect(hidesTabs([{}])).toBe(false)
    expect(hidesTabs([])).toBe(false)
  })
})

describe('whether the reader has moved anything', () => {
  it('ignores sizes, the visible tab and cell ids', () => {
    const resized = reconcile(DEFAULT, DECLARED)
    const other = JSON.parse(JSON.stringify(resized)) as LayoutArrangement
    const top = cellsOf(other).find((cell) => cell.id === 'top')!
    top.active = 'files'
    top.size = 7
    delete top.id
    expect(sameArrangement(resized, other)).toBe(true)
  })

  it('counts a tab moved to another cell, a reordered tab and a folded cell', () => {
    const base = reconcile(DEFAULT, DECLARED)
    expect(
      sameArrangement(base, placePanel(reconcile(DEFAULT, ['nav', 'chat', 'files', 'terminal']), 'preview', 'chat'))
    ).toBe(false)
    const reordered = JSON.parse(JSON.stringify(base)) as LayoutArrangement
    cellsOf(reordered)
      .find((cell) => cell.id === 'top')!
      .panels.reverse()
    expect(sameArrangement(base, reordered)).toBe(false)
    expect(sameArrangement(base, setCollapsed(base, 'bottom', true))).toBe(false)
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

  it('the default shares of the cells in sight, when others of the split are hidden', () => {
    const ROW = withCellIds({
      root: {
        direction: 'row',
        children: [
          { panels: ['chat'], size: 50 },
          { panels: ['overview'] },
          { panels: ['threads'], size: 50 },
          { panels: ['files', 'routines'], size: 40 },
        ],
      },
    })
    expect(defaultWeightsAmong(ROW, [['chat'], ['threads']])).toEqual([50, 50])
    // A cell holding more than its default (an opened file beside the list) still counts as that cell.
    expect(defaultWeightsAmong(ROW, [['chat'], ['threads'], ['routines', 'file:a.md']])).toEqual([50, 50, 40])
    // Out of the default's order: the reader arranged them some other way.
    expect(defaultWeightsAmong(ROW, [['threads'], ['chat']])).toBeNull()
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

  it('a split whose every cell is hidden is out of sight too, and the cells in sight share its room', () => {
    const NESTED = withCellIds({
      root: {
        direction: 'row',
        children: [
          { id: 'chat', panels: ['chat'], size: 1 },
          {
            direction: 'column',
            size: 1,
            children: [
              { id: 'a', panels: ['a'] },
              { id: 'b', panels: ['b'] },
            ],
          },
        ],
      },
    })
    const hiddenColumn = (cell: LayoutCell) => (cell.id === 'a' || cell.id === 'b' ? { hidden: true } : {})
    const [chat, column] = toDockview({ root: NESTED.root! }, box, panelState, hiddenColumn).layout.grid.root.data as {
      size: number
      visible?: boolean
    }[]
    expect(chat.size).toBe(1200)
    expect(column.visible).toBe(false)
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

describe('a cell with a height of its own', () => {
  const panelState = (id: string) => ({ id, contentComponent: 'host', title: id })
  const TERMINAL = { id: 'terminal', height: { initial: 200, min: 100, max: 500 } }
  const PANELS: Record<string, { id: string; height?: unknown; hidden?: boolean }> = {
    code: { id: 'code' },
    preview: { id: 'preview' },
    terminal: TERMINAL,
  }
  const COLUMN = withCellIds({
    root: {
      direction: 'column',
      children: [
        { id: 'code', panels: ['code'], size: 2 },
        { id: 'preview', panels: ['preview'], size: 2 },
        { id: 'terminal', panels: ['terminal'], size: 1 },
      ],
    },
  })
  const sizing = (cell: LayoutCell, along: 'row' | 'column') =>
    cellSizing(
      cell.panels.map((id) => PANELS[id]),
      along,
      (id, axis) => (id === 'terminal' && axis === 'height' ? 200 : 0),
      {}
    )
  type Leaf = { size: number; data: { id: string; priority?: string } }
  const leaves = () =>
    toDockview({ root: COLUMN.root! }, { width: 600, height: 800 }, panelState, sizing).layout.grid.root.data as Leaf[]

  it('keeps that height in a column, and the cells above share the rest by weight', () => {
    expect(leaves().map((leaf) => leaf.size)).toEqual([300, 300, 200])
  })

  it('is the cell that keeps its size when a neighbour comes or goes', () => {
    expect(leaves().map((leaf) => leaf.data.priority)).toEqual([undefined, undefined, 'low'])
  })

  it('takes its share by weight when it sits in a row', () => {
    expect(cellSizing([TERMINAL], 'row', () => 200, {}).fixed).toBeUndefined()
  })
})

describe('a panel opened beside another', () => {
  const panelsInRows = (a: LayoutArrangement) =>
    a.root && 'children' in a.root ? a.root.children.map((c) => ('panels' in c ? c.panels : [])) : []

  it('gets a cell of its own to the right of that panel, in the same row', () => {
    const start: LayoutArrangement = {
      root: { direction: 'row', children: [{ panels: ['chat'] }, { panels: ['files', 'changes'] }] },
    }
    const next = placeBeside(start, 'file:a', 'files')
    expect(panelsInRows(next)).toEqual([['chat'], ['files', 'changes'], ['file:a']])
  })

  it('makes a row with the cell when that cell sits in a column', () => {
    const start: LayoutArrangement = {
      root: { direction: 'column', children: [{ panels: ['files'] }, { panels: ['term'] }] },
    }
    const next = placeBeside(start, 'file:a', 'files')
    const top = next.root && 'children' in next.root ? next.root.children[0] : null
    expect(top && 'children' in top ? top.direction : null).toBe('row')
    expect(cellsOf(next).map((c) => c.panels)).toEqual([['files'], ['file:a'], ['term']])
  })

  it('goes where any panel would when the other panel is not placed', () => {
    const start: LayoutArrangement = { root: { panels: ['chat'] } }
    expect(placedPanels(placeBeside(start, 'file:a', 'files'))).toEqual(['chat', 'file:a'])
  })
})

describe('a panel that spawns others', () => {
  const work = (id: string) => (['files', 'preview', 'terminal'].includes(id) ? 'work' : undefined)

  it("is offered on the cell its new panels land in: its group's home, not the conversation", () => {
    expect(spawnLanding(DEFAULT, { id: 'shell', group: 'work' }, work)).toBe('top')
  })

  it('follows its group when the group moves', () => {
    const moved: LayoutArrangement = {
      root: {
        direction: 'row',
        children: [
          { id: 'a', panels: ['chat', 'files', 'preview'] },
          { id: 'b', panels: ['nav'] },
        ],
      },
    }
    expect(spawnLanding(moved, { id: 'shell', group: 'work' }, work)).toBe('a')
  })

  it('with no cell of its group, is offered where a new panel is placed', () => {
    expect(spawnLanding(DEFAULT, { id: 'shell', group: 'elsewhere' }, work)).toBe('left')
  })

  it("opening beside a panel, is offered on that panel's cell", () => {
    expect(spawnLanding(DEFAULT, { id: 'shell', beside: 'chat' }, work)).toBe('middle')
  })

  it("opening below a panel, is offered on its group's cell, and nowhere while there is none", () => {
    const terms = (id: string) => (id.startsWith('term') ? 'terms' : undefined)
    const editor: LayoutArrangement = {
      root: {
        direction: 'row',
        children: [
          { id: 'mid', panels: ['code'] },
          { id: 'side', panels: ['chat'] },
        ],
      },
    }
    expect(spawnLanding(editor, { id: 'shell', group: 'terms', below: 'code' }, terms)).toBeUndefined()
    const withTerm = placeBelow(withCellIds(editor), 'term:1', 'code')
    const cell = cellOfPanel(withCellIds(withTerm), 'term:1')!
    expect(spawnLanding(withCellIds(withTerm), { id: 'shell', group: 'terms', below: 'code' }, terms)).toBe(cell.id)
  })
})

describe('a panel opened below another', () => {
  const terms = (id: string) => (id.startsWith('term') ? 'terms' : undefined)
  const EDITOR: LayoutArrangement = {
    root: { direction: 'row', children: [{ panels: ['tree'] }, { panels: ['code'] }, { panels: ['chat'] }] },
  }

  it('gets a cell of its own under that panel, the row around it unchanged', () => {
    const next = placeBelow(EDITOR, 'term:1', 'code')
    const row = next.root && 'children' in next.root ? next.root : null
    expect(row?.direction).toBe('row')
    const middle = row?.children[1]
    expect(middle && 'children' in middle ? middle.direction : null).toBe('column')
    expect(cellsOf(next).map((c) => c.panels)).toEqual([['tree'], ['code'], ['term:1'], ['chat']])
  })

  it('the next panel of its group joins that cell instead of opening another', () => {
    const one = placeTransient(EDITOR, { id: 'term:1', group: 'terms', below: 'code' }, terms)
    const two = placeTransient(one, { id: 'term:2', group: 'terms', below: 'code' }, terms)
    expect(cellsOf(two).map((c) => c.panels)).toEqual([['tree'], ['code'], ['term:1', 'term:2'], ['chat']])
  })

  it('goes where any panel would when the other panel is not placed', () => {
    const start: LayoutArrangement = { root: { panels: ['chat'] } }
    expect(placedPanels(placeBelow(start, 'term:1', 'code'))).toEqual(['chat', 'term:1'])
  })
})

describe('a cell floating over the grid', () => {
  /** DEFAULT with the terminal popped out of its cell into a window of its own. */
  const FLOATED: LayoutArrangement = {
    root: {
      direction: 'row',
      children: [
        { id: 'left', panels: ['nav'], size: 1 },
        { id: 'middle', panels: ['chat'], size: 2 },
        { id: 'top', panels: ['files', 'preview'], active: 'preview', size: 2 },
      ],
    },
    floating: [{ id: 'win', panels: ['terminal'], active: 'terminal' }],
  }

  it('holds its panel: it is placed, not offered under "+"', () => {
    holdsEveryPanelOnce(FLOATED, DECLARED)
    expect(unplacedPanels(FLOATED, DECLARED)).toEqual([])
  })

  it('is remembered floating: a reload brings it back as a window', () => {
    const restored = restoreArrangement(serializeArrangement(FLOATED, 3), OPTIONS)!
    expect(restored.floating?.map((cell) => cell.panels)).toEqual([['terminal']])
    expect(placedPanels({ root: restored.root })).not.toContain('terminal')
  })

  it('an arrangement saved before anything could float still loads as it was', () => {
    const raw = JSON.stringify({ v: 3, root: DEFAULT.root })
    expect(restoreArrangement(raw, OPTIONS)).toEqual(reconcile(DEFAULT, DECLARED))
  })

  it('a save that has the same panel in a cell and floating is not used', () => {
    const raw = JSON.stringify({ v: 3, root: DEFAULT.root, floating: [{ panels: ['terminal'] }] })
    expect(restoreArrangement(raw, OPTIONS)).toBeNull()
  })

  it('counts as a change from the default arrangement', () => {
    expect(sameArrangement(FLOATED, reconcile(DEFAULT, DECLARED))).toBe(false)
  })

  it('docked back, goes to its place in the default: beside its default neighbour, the same way round', () => {
    const back = placeAsInDefault(withoutFloating(FLOATED, 'terminal'), 'terminal', DEFAULT)
    expect(back.floating).toBeUndefined()
    // In the default it sits under files/preview: a column with it below, in that cell's place.
    const right = back.root && 'children' in back.root ? back.root.children[2] : null
    expect(right && 'children' in right ? right.direction : null).toBe('column')
    expect(cellsOf(back).map((cell) => cell.panels)).toEqual([['nav'], ['chat'], ['files', 'preview'], ['terminal']])
  })

  it('docked back, joins the cell of a panel it shares its default cell with', () => {
    const previewOut: LayoutArrangement = {
      root: {
        direction: 'row',
        children: [
          { panels: ['nav'] },
          { panels: ['chat'] },
          { direction: 'column', children: [{ panels: ['files'] }, { panels: ['terminal'] }] },
        ],
      },
      floating: [{ panels: ['preview'] }],
    }
    const back = placeAsInDefault(withoutFloating(previewOut, 'preview'), 'preview', DEFAULT)
    expect(cellOfPanel(back, 'preview')?.panels).toEqual(['files', 'preview'])
    expect(cellOfPanel(back, 'preview')?.active).toBe('preview')
  })

  it('docked back with none of its default surroundings left, goes where any panel goes', () => {
    const lone: LayoutArrangement = { root: { panels: ['chat'] } }
    expect(placedPanels(placeAsInDefault(lone, 'terminal', { root: { panels: ['terminal'] } }))).toEqual([
      'chat',
      'terminal',
    ])
  })

  it('reads back from dockview as a window, with the place it floats at', () => {
    const box = { width: 1000, height: 600 }
    const panelState = (id: string) => ({ id, contentComponent: 'host', title: id })
    const at = { left: 100, top: 80, width: 400, height: 300 }
    const { layout } = toDockview(
      { root: FLOATED.root!, floating: FLOATED.floating },
      box,
      panelState,
      undefined,
      () => at
    )
    const back = fromDockview(layout, new Set(), new Map())
    expect(back.floating?.map((cell) => cell.panels)).toEqual([['terminal']])
    expect(floatBoxes(layout)).toEqual([{ panels: ['terminal'], box: at }])
  })

  it('a window remembered outside the area, or larger than it, opens wholly inside it', () => {
    const box = { width: 1000, height: 600 }
    const panelState = (id: string) => ({ id, contentComponent: 'host', title: id })
    const { layout } = toDockview(
      { root: FLOATED.root!, floating: FLOATED.floating },
      box,
      panelState,
      undefined,
      () => ({ left: 1800, top: -50, width: 1400, height: 200 })
    )
    const [window] = floatBoxes(layout)
    expect(window.box).toEqual({ left: 0, top: 0, width: 1000, height: 200 })
    const { layout: off } = toDockview(
      { root: FLOATED.root!, floating: FLOATED.floating },
      box,
      panelState,
      undefined,
      () => ({ left: 900, top: 500, width: 400, height: 300 })
    )
    const [moved] = floatBoxes(off)
    expect(moved.box).toEqual({ left: 600, top: 300, width: 400, height: 300 })
  })
})
