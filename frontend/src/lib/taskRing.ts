// 一条活的圆环显示哪个状态 —— 「现在有什么在动」，一眼。
//
// 圆环不显示百分比，因为没有百分比可显示：一件活没有分母。它显示的是这条活此刻
// 处在哪一格，而那一格全部由已有的事实推出来，没有一个是新造的枚举：
//
//   已交付  accepted_at 有值 —— 交付和 open/closed 不是同一个问题，一条活可以
//           已交付但还开着，也可以关掉却什么都没交付。
//   等验收  有一张未决的验收卡。它也是安静的：光看 residency 和「闲着」一模一样，
//           而这两者对看的人意味着相反的下一步（去验收 vs 去催）。
//   排队中  queued_at 有值 —— 它想跑，房间的四个槽位满了。和「闲着」分开，因为
//           闲着是没人找它，排队是它被拦住了。
//   在跑    residency === 'running'。
//   闲着    其余。不是「做完了」—— 一条活收工就放开槽位，谁跟它说话就立刻回来。
//   已收工  status === 'closed' 且没交付过。
//
// 后端的 `Residency` 只有 running / idle 两个值，而且它的注释明说故意不做
// `waiting`（活不嵌套，那会是个没人写也没人读的词）。所以「等验收」这一格是从
// 卡上读出来的，不是从 residency —— 不然就是在前端偷偷发明一个后端拒绝过的状态。

import type { RoomTask } from '@/cx_types'

export type TaskRingState = 'running' | 'queued' | 'reviewing' | 'delivered' | 'closed' | 'idle'

export interface TaskRing {
  state: TaskRingState
  label: string
  /** Class suffix the host styles: `ring--running` 等。 */
  cls: string
}

/** 一张卡还没结算完 —— 还在等人或者等 CI，都算「这条活卡在别人身上」。 */
const SETTLED_CARD = new Set(['accepted', 'rejected', 'revoked', 'gate_failed', 'gate_blocked'])

export function taskRing(
  task: Pick<RoomTask, 'status' | 'residency' | 'queued_at' | 'accepted_at' | 'card'>
): TaskRing {
  if (task.accepted_at) return { state: 'delivered', label: '已交付', cls: 'ring--delivered' }
  // 在跑压过卡：卡描述的是它可能马上就要顶掉的那一版，而「在跑」是此刻真的成立
  // 的那件事。和话题头部 `topicPhase` 里 working 压过 card 是同一条规矩。
  if (task.residency === 'running') return { state: 'running', label: '在跑', cls: 'ring--running' }
  const card = task.card
  if (card && !SETTLED_CARD.has(card.status)) {
    return { state: 'reviewing', label: '等验收', cls: 'ring--reviewing' }
  }
  if (task.queued_at) return { state: 'queued', label: '排队中', cls: 'ring--queued' }
  // 关掉且什么都没交付。放在最后：一条已交付的活即使关掉了，它首先是已交付的。
  if (task.status === 'closed') return { state: 'closed', label: '已收工', cls: 'ring--closed' }
  return { state: 'idle', label: '闲着', cls: 'ring--idle' }
}

/** 在动的活排前面 —— 你打开一个房间是想知道现在有什么在跑。 */
const ORDER: Record<TaskRingState, number> = {
  running: 0,
  queued: 1,
  reviewing: 2,
  idle: 3,
  delivered: 4,
  closed: 5,
}

export function ringRank(state: TaskRingState): number {
  return ORDER[state]
}
