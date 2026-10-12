// A canvas block in a document: what is changed on the board is saved, and the
// board read again afterwards (a refresh, another reader) shows it. The host
// here keeps canvases the way the backend does (views/demo/catalogCanvasFixtures).
import type { CanvasData, CanvasHost } from '@/types/canvas'

import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import CanvasBlockView from './CanvasBlockView.vue'

import { setLocale } from '@/i18n'
import { announceCanvas } from '@/lib/docCanvasSignals'
import { promiseCanvas } from '@/lib/docSchema/canvas'
import { memoryCanvas, projectMap } from '@/views/demo/catalogCanvasFixtures'

beforeEach(() => setLocale('en'))
afterEach(cleanup)

const MAP = projectMap()
const GOAL = MAP.cards[0]
const RANK = MAP.cards[3]

function open(data: CanvasData, attrs: Record<string, unknown> = {}, editable = true) {
  const host: CanvasHost = { ...data, openTask: vi.fn() }
  const updateAttributes = vi.fn()
  const view = render(CanvasBlockView, {
    props: {
      editor: { isEditable: editable },
      node: {
        type: { name: 'canvas' },
        attrs: { canvas: MAP.id, title: '项目地图', about: '', pending: '', ...attrs },
      },
      decorations: [],
      selected: false,
      extension: { name: 'canvas', options: { host } },
      getPos: () => 0,
      updateAttributes,
      deleteNode: () => {},
      view: {},
      innerDecorations: {},
      HTMLAttributes: {},
    } as never,
  })
  return { view, updateAttributes }
}

/** The card's element on the board, once the board has been read. */
async function cardOn(view: ReturnType<typeof open>['view'], id: string): Promise<HTMLElement> {
  return waitFor(() => {
    const node = view.container.querySelector<HTMLElement>(`.vue-flow__node[data-id="${id}"]`)
    if (!node) throw new Error(`card ${id} is not drawn`)
    return node
  })
}

/** Where the board draws a card: its `translate(x, y)`. */
function placeOf(node: HTMLElement): { x: number; y: number } {
  const [, x, y] = /translate\((-?[\d.]+)px,\s*(-?[\d.]+)px\)/.exec(node.style.transform) ?? []
  return { x: Number(x), y: Number(y) }
}

async function drag(node: HTMLElement, by: { x: number; y: number }) {
  const at = { clientX: 10, clientY: 10, button: 0, view: window }
  await fireEvent.mouseDown(node, at)
  await fireEvent.mouseMove(window, { ...at, clientX: 10 + by.x / 2, clientY: 10 + by.y / 2 })
  await fireEvent.mouseMove(window, { ...at, clientX: 10 + by.x, clientY: 10 + by.y })
  await fireEvent.mouseUp(window, { ...at, clientX: 10 + by.x, clientY: 10 + by.y })
}

describe('a canvas block', () => {
  it('saves where a card was dragged, and draws it there when read again', async () => {
    const data = memoryCanvas()
    const first = open(data)
    const before = placeOf(await cardOn(first.view, GOAL.id))

    await drag(await cardOn(first.view, GOAL.id), { x: 120, y: 40 })
    await waitFor(async () => expect((await data.load(MAP.id)).cards[0].x).not.toBeNull())
    // How far it went on the board depends on the zoom; where it was left is what counts.
    const left = placeOf(await cardOn(first.view, GOAL.id))
    expect(left).not.toEqual(before)
    first.view.unmount()

    const again = open(data)
    await waitFor(async () => expect(placeOf(await cardOn(again.view, GOAL.id))).toEqual(left))
  })

  it('saves what was written in a card after a double click, and shows it when read again', async () => {
    const data = memoryCanvas()
    const first = open(data)

    await fireEvent.dblClick((await cardOn(first.view, RANK.id)).querySelector('.canvas-card')!)
    const input = await waitFor(() => {
      const box = first.view.container.querySelector<HTMLTextAreaElement>('textarea[aria-label="Card text"]')
      if (!box) throw new Error('the card did not open for typing')
      return box
    })
    await fireEvent.update(input, '排序规则：按点击率')
    await fireEvent.blur(input)
    await waitFor(async () => expect((await data.load(MAP.id)).cards[3].content).toBe('排序规则：按点击率'))
    first.view.unmount()

    const again = open(data)
    expect(await again.view.findByText('排序规则：按点击率')).toBeTruthy()
  })

  it('changes nothing for a reader who may not write', async () => {
    const data = memoryCanvas()
    const change = vi.spyOn(data, 'change')
    const { view } = open(data, {}, false)

    await fireEvent.dblClick((await cardOn(view, RANK.id)).querySelector('.canvas-card')!)
    await drag(await cardOn(view, RANK.id), { x: 120, y: 40 })

    expect(view.container.querySelector('textarea[aria-label="Card text"]')).toBeNull()
    expect(change).not.toHaveBeenCalled()
  })

  it('reads the canvas again when somebody else changed it', async () => {
    const data = memoryCanvas()
    const { view } = open(data)
    await cardOn(view, GOAL.id)

    // Somebody else, from their own page: the board here hears of it and reads again.
    await data.change(MAP.id, [{ op: 'add', content: '别人加的格子' }])
    announceCanvas(MAP.id)

    expect(await view.findByText('别人加的格子')).toBeTruthy()
  })
})

describe('a canvas block pasted into a document', () => {
  it('gets a copy of the canvas, which changes apart from the one it was copied from', async () => {
    const data = memoryCanvas()
    const { updateAttributes } = open(data, { pending: promiseCanvas('copy') })

    await waitFor(() => expect(updateAttributes).toHaveBeenCalled())
    const [[attrs]] = updateAttributes.mock.calls
    expect(attrs.canvas).not.toBe(MAP.id)
    expect(attrs.pending).toBe('')
    await data.change(attrs.canvas, [{ op: 'delete', card: 1 }])
    expect((await data.load(MAP.id)).cards).toHaveLength(MAP.cards.length)
  })

  it('is left to the page that pasted it', async () => {
    const data = memoryCanvas()
    const copy = vi.spyOn(data, 'copy')
    // Another page's mark: this page owes it nothing.
    const { view, updateAttributes } = open(data, { pending: 'someone-elses-mark' })
    await cardOn(view, GOAL.id)

    expect(copy).not.toHaveBeenCalled()
    expect(updateAttributes).not.toHaveBeenCalled()
  })
})
