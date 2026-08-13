// 「这件事已经派出去了」—— 父话题时间线上的派生标记 (issue #314)。
//
// `cheese split` 建一个子话题，然后就什么都不留了：它不往父话题写任何 block，
// 请求体里也没有任何字段指向父话题的内容 (`SplitIn` 只有 title/created_by/brief)。
// 于是父话题的时间线上，一件活被派出去这回事是完全无痕的 —— 房间里的人（和下一
// 个进来的人）看不出第 N 项已经归别人了，就照着自己那份清单又做一遍。
//
// 所以标记只能**读时派生**，不能存：唯一还在的关系是子话题的 parent_id +
// created_at。好处是不用改 split 的行为，而且对已经发生过的 split 立刻生效。
//
// 派生得出的和派生不出的，界限很硬：
//   能 —— 「这个时刻，从这个房间派出去了《X》，去那边看」。
//   不能 —— 「以下这几条消息 / 这一项待办不归这里了」。那需要一条从子话题指回
//           父话题某个 block 的边，数据库里没有这条边。
import type { Block, Topic } from '../cx_types'

// 时间线上一条派生出来的「已派出」行。
export interface SplitMarker {
  topicId: string
  title: string
  // 'active' | 'archived' | ... —— 派出去的活现在到哪一步了，直接取子话题的状态。
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

// 一件活，不是一个房间。后端按层级分：根话题的孩子是房间（kind=topic），房间的
// 孩子才是一件事（kind=task，历史值 subtopic）—— 见 topic/services.py::_child_kind。
// 只有后者是「派出去的活」；在项目根的时间线上给每个房间标一行「已派出」是噪音。
const WORK_KINDS = new Set(['task', 'subtopic'])

/**
 * 本房间派出去的活 —— 直接子话题，按派出时间排序。
 *
 * 由「讨论升级 / 文档 🧩」生出来的子话题排除在外：那条路径已经在源 block 上留了
 * `upgraded_to_topic_id`，前端也已经把它渲染成「已升级为话题」链接了。同一件事再
 * 标一次就是重复。
 */
export function dispatchedChildren(
  parentTopicId: string | null | undefined,
  topics: readonly Topic[]
): SplitMarker[] {
  if (!parentTopicId) return []
  return topics
    .filter(
      (t) =>
        t.parent_id === parentTopicId && WORK_KINDS.has(t.kind) && !t.upgraded_from_block_id
    )
    .map((t) => ({ topicId: t.id, title: t.title, status: t.status, createdAt: t.created_at }))
    .sort((a, b) => at(a.createdAt) - at(b.createdAt))
}

/**
 * 把标记放到时间线窗口里：每条标记落在「比它早的最后一条消息」和「比它晚的第一条
 * 消息」之间。
 */
export function placeSplitMarkers(
  parentTopicId: string | null | undefined,
  topics: readonly Topic[],
  timeline: TimelineWindow
): SplitMarkerPlacement {
  const markers = dispatchedChildren(parentTopicId, topics)
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
