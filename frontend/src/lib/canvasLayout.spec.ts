// Cards nobody placed are laid out by their lines, and never on top of the
// cards somebody did place.
import type { CanvasCard, CanvasEdge } from '@/types/canvas'

import { describe, expect, it } from 'vitest'

import { layoutCards } from './canvasLayout'

function card(number: number, over: Partial<CanvasCard> = {}): CanvasCard {
  return {
    id: `card-${number}`,
    number,
    content: `第 ${number} 件事`,
    x: null,
    y: null,
    width: null,
    height: null,
    task: null,
    task_hidden: false,
    updated_by: 'cheese',
    updated_at: '2026-10-12T00:00:00Z',
    ...over,
  }
}

const line = (from: number, to: number): CanvasEdge => ({
  id: `line-${from}-${to}`,
  from: `card-${from}`,
  to: `card-${to}`,
  label: '',
})

const overlap = (a: { x: number; y: number; width: number; height: number }, b: typeof a) =>
  a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height

describe('laying out a canvas', () => {
  it('puts the card a line leaves before the card it reaches', () => {
    const boxes = layoutCards([card(1), card(2), card(3)], [line(2, 1), line(3, 2)])
    expect(boxes.get('card-3')!.x).toBeLessThan(boxes.get('card-2')!.x)
    expect(boxes.get('card-2')!.x).toBeLessThan(boxes.get('card-1')!.x)
  })

  it('leaves a placed card where it was put, and keeps the others off it', () => {
    const placed = card(1, { x: 40, y: 60 })
    const boxes = layoutCards([placed, card(2), card(3)], [line(2, 3)])
    expect(boxes.get('card-1')).toMatchObject({ x: 40, y: 60 })
    for (const id of ['card-2', 'card-3']) expect(overlap(boxes.get(id)!, boxes.get('card-1')!)).toBe(false)
    expect(overlap(boxes.get('card-2')!, boxes.get('card-3')!)).toBe(false)
  })
})
