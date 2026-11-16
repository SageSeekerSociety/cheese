/**
 * 画布在预览站里的样子：一块「项目地图」，和一份只活在这一页里的读写。
 *
 * 读写照着后端的规矩在内存里做（`app/domain/canvas/services.py`）：改动按顺序生效，
 * 新格子拿下一个编号，删格子带走连着它的线。所以在预览站里拖格子、连线、改字都是真的，
 * 只是刷新就回到这一份。
 */
import type { CanvasBoard, CanvasCard, CanvasChange, CanvasData, CanvasEdge } from '@/types/canvas'

const DOC = '6f1c2e0a-0000-4000-8000-00000000d0c0'

function card(number: number, content: string, over: Partial<CanvasCard> = {}): CanvasCard {
  return {
    id: `6f1c2e0a-0000-4000-8000-${String(number).padStart(12, '0')}`,
    number,
    content,
    x: null,
    y: null,
    width: null,
    height: null,
    task: null,
    task_hidden: false,
    updated_by: 'cheese',
    updated_at: '2026-10-12T09:00:00Z',
    ...over,
  }
}

function line(from: CanvasCard, to: CanvasCard, label = ''): CanvasEdge {
  return { id: `${from.id.slice(0, -4)}e${from.number}${to.number}`, from: from.id, to: to.id, label }
}

/** 一个项目的地图：一个目标，三件事和它们之间的关系，一张便签；有两个格子关联了任务。 */
export function projectMap(): CanvasBoard {
  const goal = card(1, '**十月底上线新版搜索**\n- 搜索框常驻\n- 结果按相关度排')
  const index = card(2, '重建索引\n  - 先停写入，再全量重建', {
    task: {
      id: '7a2b0000-0000-4000-8000-000000000012',
      number: 12,
      title: '重建搜索索引',
      room_id: '7a2b0000-0000-4000-8000-0000000000c1',
      column: 'building',
      phrase: 'started',
      agent_name: '芝士',
      agent_name_source: 'default',
    },
  })
  const ui = card(3, '改搜索框和结果页', {
    task: {
      id: '7a2b0000-0000-4000-8000-000000000013',
      number: 13,
      title: '搜索结果页改版',
      room_id: '7a2b0000-0000-4000-8000-0000000000c1',
      column: 'needs_you',
      phrase: 'awaiting_review',
      agent_name: '芝士',
      agent_name_source: 'default',
    },
    updated_by: 'wang',
  })
  const rank = card(4, '定相关度的排序规则')
  const note = card(5, '便签：周五前和产品对一次排序规则')
  return {
    id: '6f1c2e0a-3b7d-4e58-9a51-0c2d4b8e7f13',
    document_id: DOC,
    height: null,
    writable: true,
    cards: [goal, index, ui, rank, note],
    edges: [line(index, goal, '做完才能'), line(ui, goal, '做完才能'), line(rank, ui, '决定')],
  }
}

/** 还没有格子的一块。 */
export function emptyBoard(): CanvasBoard {
  return { ...projectMap(), id: '6f1c2e0a-3b7d-4e58-9a51-0c2d4b8e7f14', cards: [], edges: [] }
}

/** Makes the changes to `board` in place, the way the backend does. */
export function applyChanges(board: CanvasBoard, changes: CanvasChange[]): void {
  const refs = new Map<string, number>()
  const find = (key: number | string) => {
    const number = typeof key === 'string' && refs.has(key) ? refs.get(key) : Number(String(key).replace(/\D/g, ''))
    const found = board.cards.find((c) => c.number === number)
    if (!found) throw new Error(`no card ${key}`)
    return found
  }
  for (const change of changes) {
    if (change.op === 'add') {
      const number = Math.max(0, ...board.cards.map((c) => c.number)) + 1
      board.cards.push(
        card(number, change.content ?? '', { x: change.x ?? null, y: change.y ?? null, updated_by: 'wang' })
      )
      if (change.ref) refs.set(change.ref, number)
    } else if (change.op === 'update') {
      const target = find(change.card)
      if (change.content !== undefined) target.content = change.content
    } else if (change.op === 'move') {
      Object.assign(find(change.card), {
        x: change.x,
        y: change.y,
        ...(change.width ? { width: change.width } : {}),
        ...(change.height ? { height: change.height } : {}),
      })
    } else if (change.op === 'delete') {
      const gone = find(change.card)
      board.cards = board.cards.filter((c) => c !== gone)
      board.edges = board.edges.filter((e) => e.from !== gone.id && e.to !== gone.id)
    } else if (change.op === 'connect') {
      const [from, to] = [find(change.from), find(change.to)]
      const existing = board.edges.find((e) => e.from === from.id && e.to === to.id)
      if (existing) existing.label = change.label ?? ''
      else board.edges.push(line(from, to, change.label ?? ''))
    } else {
      const [from, to] = [find(change.from), find(change.to)]
      board.edges = board.edges.filter((e) => !(e.from === from.id && e.to === to.id))
    }
  }
}

/** 一份在内存里的读写：几块画布按编号放着，改动照后端的规矩做。 */
export function memoryCanvas(boards: CanvasBoard[] = [projectMap()]): CanvasData {
  const kept = new Map(boards.map((board) => [board.id, structuredClone(board)]))
  const read = (id: string) => {
    const board = kept.get(id)
    if (!board) throw new Error(`no canvas ${id}`)
    return structuredClone(board)
  }
  let made = 0
  const keep = (board: CanvasBoard) => {
    kept.set(board.id, board)
    return board.id
  }
  return {
    load: async (id) => read(id),
    change: async (id, changes) => {
      const board = read(id)
      applyChanges(board, changes)
      kept.set(id, board)
      return read(id)
    },
    setHeight: async (id, height) => {
      const board = read(id)
      board.height = height
      kept.set(id, board)
      return read(id)
    },
    create: async () =>
      keep({ ...emptyBoard(), id: `6f1c2e0a-0000-4000-8000-1000000000${String(++made).padStart(2, '0')}` }),
    copy: async (id) =>
      keep({ ...read(id), id: `6f1c2e0a-0000-4000-8000-2000000000${String(++made).padStart(2, '0')}` }),
  }
}

/** 一篇放着这块画布的文档：正文前后各一段，中间是画布那一行。 */
export const CANVAS_DOC = `这一页说清楚新版搜索要做的几件事，以及它们之间的关系。

:::canvas ${projectMap().id} 项目地图
新版搜索要做的几件事和它们之间的关系。
:::

排期按地图上的先后来：索引和结果页可以并行，排序规则要先定。`
