// 「这件事已经派出去了」—— 房间时间线上的派生标记 (issue #314)。
//
// `cheese split` 开一条支线，然后就什么都不留了：它不往房间主线写任何 block，
// 请求体里也没有任何字段指向房间的内容 (`SplitIn` 只有 title/created_by/brief)。
// 于是房间的时间线上，一件活被派出去这回事是完全无痕的 —— 房间里的人（和下一
// 个进来的人）看不出第 N 项已经归别人了，就照着自己那份清单又做一遍。
//
// 所以标记只能**读时派生**，不能存：唯一还在的关系是这条支线的 room_id +
// created_at。好处是不用改 split 的行为，而且对已经发生过的 split 立刻生效。
//
// 派生得出的和派生不出的，界限很硬：
//   能 —— 「这个时刻，从这个房间派出去了《X》，去那边看」。
//   不能 —— 「以下这几条消息 / 这一项待办不归这里了」。那需要一条从支线指回
//           房间某个 block 的边，数据库里没有这条边。
import type { Block, RoomTask } from '../cx_types'

// 时间线上一条派生出来的「已派出」行。
export interface SplitMarker {
  // 这条支线自己的 id —— 平台里一个「地点」就是用一个 id 定位的，所以这个 id
  // 既能打开它，也能拿去调任何按地点寻址的接口。
  taskId: string
  title: string
  // 'open' | 'closed' —— 派出去的活现在到哪一步了，直接取这条支线的状态。
  status: string
  createdAt: string
}

export interface SplitMarkerPlacement {
  // 要渲染在这个 block 之前的标记（key = block id）。
  before: Map<string, SplitMarker[]>
  // 比窗口里每一条消息都新的标记：渲染在最后一行之后。
  tail: SplitMarker[]
}

// 时间线是一个**窗口**（最新的一页，往上滚才加载更早的，见 blockPaging.ts）。
// 标记必须按时间落在正确的两条消息之间，所以窗口顶部那一段要特别小心：一个比
// 窗口最老那条还早的标记，真实位置在窗口**上面**，此刻放在顶端是错的位置。
// hasMore 为真时就先不显示，等用户滚回去、更早的消息加载进来，它自己会归位。
export interface TimelineWindow {
  blocks: readonly Block[]
  hasMore: boolean
}

// 每次现造一个，不共享一个常量：返回值里带着可变的 Map 和数组，共享出去等于把它们
// 交给所有调用方一起改。
function empty(): SplitMarkerPlacement {
  return { before: new Map(), tail: [] }
}

function at(iso: string | null | undefined): number {
  const t = Date.parse(iso ?? '')
  return Number.isNaN(t) ? Number.POSITIVE_INFINITY : t
}

/**
 * 本房间派出去的活，按派出时间排序。
 *
 * 不用再按 kind 挑了：调用方传进来的就是这个房间的支线（`GET /topics/{id}/tasks`），
 * 而支线只有一种。以前那个 `WORK_KINDS` 过滤器存在，是因为「一件活」和「一个房间」
 * 同住在 topics 表里、只能靠一列区分。
 *
 * 由「讨论升级 / 文档 🧩」生出来的支线排除在外：那条路径已经在源 block 上留了
 * `upgraded_to_task_id`，前端也已经把它渲染成「已升级」链接了。同一件事再标一次
 * 就是重复。
 */
export function dispatchedTasks(tasks: readonly RoomTask[]): SplitMarker[] {
  return tasks
    .filter((t) => !t.upgraded_from_block_id)
    .map((t) => ({ taskId: t.id, title: t.title, status: t.status, createdAt: t.created_at }))
    .sort((a, b) => at(a.createdAt) - at(b.createdAt))
}

/**
 * 把标记放到时间线窗口里：每条标记落在「比它早的最后一条消息」和「比它晚的第一条
 * 消息」之间。
 */
export function placeSplitMarkers(
  tasks: readonly RoomTask[],
  timeline: TimelineWindow
): SplitMarkerPlacement {
  const markers = dispatchedTasks(tasks)
  if (!markers.length) return empty()

  const { blocks, hasMore } = timeline
  // 空窗口：还没有任何消息在屏幕上。整段历史都在手里（hasMore=false）时，标记就是
  // 仅有的几行；否则位置未知，先不显示。
  if (!blocks.length) return { before: new Map(), tail: hasMore ? [] : markers.slice() }

  const before = new Map<string, SplitMarker[]>()
  const tail: SplitMarker[] = []
  for (const marker of markers) {
    const t = at(marker.createdAt)
    const anchor = blocks.find((b) => at(b.created_at) >= t)
    if (!anchor) {
      tail.push(marker)
      continue
    }
    // 锚在窗口第一条上，而上面还有没加载的历史 —— 真实位置可能更靠上，先不显示。
    if (anchor.id === blocks[0].id && hasMore) continue
    const list = before.get(anchor.id)
    if (list) list.push(marker)
    else before.set(anchor.id, [marker])
  }
  return { before, tail }
}
