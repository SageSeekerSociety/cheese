// A canvas as the API answers it (`app/domain/canvas/schemas.py`), and the
// changes a board sends back.
import type { components } from '../api/openapi'

type Schemas = components['schemas']

export type CanvasBoard = Schemas['CanvasOut']
export type CanvasCard = Schemas['CanvasCardOut']
export type CanvasEdge = Schemas['CanvasEdgeOut']
export type CanvasTask = Schemas['CanvasTaskOut']

/** A card, by its number on the canvas, or by the `ref` an earlier `add` gave it. */
export type CardKey = number | string

/** One change; a list of them is made in order, all or none. */
export type CanvasChange =
  | {
      op: 'add'
      ref?: string
      content?: string
      task?: string | null
      x?: number
      y?: number
      width?: number
      height?: number
    }
  | { op: 'update'; card: CardKey; content?: string; task?: string | null }
  | { op: 'move'; card: CardKey; x: number; y: number; width?: number; height?: number }
  | { op: 'delete'; card: CardKey }
  | { op: 'connect'; from: CardKey; to: CardKey; label?: string }
  | { op: 'disconnect'; from: CardKey; to: CardKey }

/**
 * What a canvas block in a document reads and writes with: the document's
 * container supplies it (composables/usePanelDoc), so the block itself knows
 * no server.
 */
export interface CanvasHost {
  load: (canvas: string) => Promise<CanvasBoard>
  change: (canvas: string, changes: CanvasChange[]) => Promise<CanvasBoard>
  setHeight: (canvas: string, height: number | null) => Promise<CanvasBoard>
  /** A new, empty canvas of this document: its id. */
  create: () => Promise<string>
  /** A copy of that canvas (of this document or another) for this document: its id. */
  copy: (canvas: string) => Promise<string>
  /** Open the task a card points at. */
  openTask: (taskId: string) => void
}

/** What the document's container supplies; the editor adds where a task opens. */
export type CanvasData = Omit<CanvasHost, 'openTask'>
