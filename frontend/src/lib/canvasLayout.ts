// Where a canvas's cards go when nobody has placed them: laid out by their
// lines, the card a line leaves before the card it reaches, left to right.
//
// Only cards without a place are laid out, and they keep clear of the placed
// ones: what somebody arranged by hand is never moved. Nothing here is saved;
// a card gets a place of its own the first time somebody drags it.
import type { CanvasCard, CanvasEdge } from '../types/canvas'

import dagre from '@dagrejs/dagre'

export const CARD_WIDTH = 220
const CARD_MIN_HEIGHT = 56
const CARD_MAX_HEIGHT = 320
/** Roughly how many characters fit on a line of a card at its default width. */
const CHARS_PER_LINE = 14
const LINE_HEIGHT = 21
const PADDING = 24
/** The gap between the placed cards and the ones laid out beside them. */
const GAP = 80

export interface Box {
  x: number
  y: number
  width: number
  height: number
}

/** A card's size: its own, or a guess from how much it says. */
export function cardSize(card: Pick<CanvasCard, 'content' | 'width' | 'height'>): { width: number; height: number } {
  const width = card.width ?? CARD_WIDTH
  if (card.height) return { width, height: card.height }
  const lines = (card.content || ' ')
    .split('\n')
    .reduce((sum, line) => sum + Math.max(1, Math.ceil([...line].length / CHARS_PER_LINE)), 0)
  const height = Math.min(CARD_MAX_HEIGHT, Math.max(CARD_MIN_HEIGHT, PADDING + lines * LINE_HEIGHT))
  return { width, height }
}

/** Every card's box: its saved place, or one laid out by the lines. */
export function layoutCards(cards: CanvasCard[], edges: CanvasEdge[]): Map<string, Box> {
  const boxes = new Map<string, Box>()
  const loose = cards.filter((card) => card.x == null || card.y == null)
  for (const card of cards) {
    if (card.x != null && card.y != null) boxes.set(card.id, { x: card.x, y: card.y, ...cardSize(card) })
  }
  if (!loose.length) return boxes

  const graph = new dagre.graphlib.Graph()
  graph.setGraph({ rankdir: 'LR', nodesep: 32, ranksep: 72, marginx: 0, marginy: 0 })
  graph.setDefaultEdgeLabel(() => ({}))
  const ids = new Set(loose.map((card) => card.id))
  for (const card of loose) graph.setNode(card.id, cardSize(card))
  for (const edge of edges) {
    if (ids.has(edge.from) && ids.has(edge.to)) graph.setEdge(edge.from, edge.to)
  }
  dagre.layout(graph)

  const placed = [...boxes.values()]
  const left = placed.length ? Math.max(...placed.map((box) => box.x + box.width)) + GAP : 0
  const top = placed.length ? Math.min(...placed.map((box) => box.y)) : 0
  for (const card of loose) {
    const node = graph.node(card.id)
    const size = cardSize(card)
    // dagre gives each node's centre.
    boxes.set(card.id, { x: left + node.x - size.width / 2, y: top + node.y - size.height / 2, ...size })
  }
  return boxes
}
