// 侧栏一行上那盏灯：这一行现在有没有一件要你看的事，一眼看得出来。
//
// 灯是**颜色 + 动不动**两件事：
//
//   呼吸绿（动）  这里在往前走：有 runner 报着在跑的会话，或者活已经递出去、走在
//                 交付链路上（等闸门、等 CI、修检查、解冲突、换基）。
//   长绿（不动）  这里做过事、都做完交付完了，你还没点开看过。点开过就灭；之后
//                 再有新活，回到呼吸绿。
//   红（不动）    这里现在不对：一轮停在报错上、五分钟没人接手，或者房间在等一位
//                 队友、等太久了，或者一条活被退回还没有人来改（三件事长的是同一盏，
//                 `RowLight.why` 说清是哪一件）。
//   黄（不动）    有事等你：一道只有你能答的问题、一张等你采纳的卡，或者有人回复了
//                 我、我还没处理。
//   蓝（不动）    这条活归我，状态上没别的话可说。只有任务行用得着。
//   不亮          别的都不亮。
//
// 呼吸绿说的是「有一轮在跑」这个事实（runner 自己报的会话状态），不是队友在任务上
// 说的「在做」——后者是纸面状态，会话可能已经停了；那一位看板和任务页头照它说，
// 侧栏不另推一份。
//
// 判据全是**事实**，这一份不推状态、只排先后：在不在往前走看 `moving`（后端把房间、
// 支线、名下的活和交付链路聚合成一个布尔），等不等人看 `awaits_me`（后端按看的人
// 算），交付完没看过看 `unseen_delivery`（后端比的是这个人的已读位和采纳时刻），有人
// 回了我没处理看 `repliedToMe`（后端比的是这个人上次打开这间房的时刻和那条回复落地的
// 时刻），还有没有一轮停在报错上没恢复、从什么时候起看 `incompleteSince`（后端
// `block/waits.py` 的 `unresolved_failures`：那一轮的队友又说话了，或者这里又开了
// 一轮，就算恢复了 —— 和「等了多久」无关，所以一条没人理的旧失败会一直红着，一条
// 已经被人接手的不会）。
//
// 蓝灯**不是判据**：谁负责写在数据里（`owner_handle`），`lightOf` 不看它，
// 也永远不会返回蓝灯——它是调用方盖的一枚印（`useTopicRail.taskLight`），而且只在
// 状态那几盏都没话说的时候才盖：它说的那件事（这条归我）比状态轻。
//
// 优先级：红 > 黄 > 呼吸绿 > 长绿 > 蓝 —— 一行上只画一盏灯，蓝最暗。折叠开关在
// 收起来的一堆行里挑一个颜色出来时也走同一个次序（`brightest`）。
//
// 「点开过就灭」不在这一份里：那是数据的事（服务端按已读位算 `unseen_delivery` 和
// `replied_to_me`，点开时本地先把那一行灭掉，见 `stores/workspace` 的
// `forgetReadLights`）。这里因此是个纯函数，测得起来。

import { t } from '@/i18n'

/** 一行上的灯画哪一盏。 */
export type LightKind = 'blue' | 'red' | 'yellow' | 'green'

/** 红灯为什么亮：三种都画同一盏，但嘴上说的是三件事（`lightTitle`）。 */
export type RedReason = 'unfinished' | 'waiting' | 'bounced'

/** 一盏灯。红灯总说得出是哪一件（`why`）：`unfinished` = 一轮停在报错上、五分钟没人
 *  接手；`waiting` = 房间在等一位队友、等太久了；`bounced` = 验收被退回，还没有人来改。
 *  绿灯用 `breathe` 分两档：闪的是有活在往前走，不闪的是做完了还没看过。
 *  `title` 是悬停时说的那一句，调用方知道得更具体时给（「在等谁、为什么」），
 *  `lightTitle` 照用；没给就按 `kind` / `why` 说那句通用的。 */
export type RowLight =
  | { kind: 'red'; why: RedReason; title?: string }
  | { kind: 'yellow' | 'blue'; title?: string }
  | { kind: 'green'; breathe: boolean; title?: string }

/** 我负责的活那一盏灯：`lightOf` 不产它，调用方在前面几盏都不亮时盖上（见文件头）。 */
export const MINE_LIGHT: RowLight = { kind: 'blue' }

/** 亮到暗。`brightest` 用它排序。蓝垫底：它说的是这条归谁，不是这条怎么了。 */
const BRIGHTNESS: Record<LightKind, number> = { red: 3, yellow: 2, green: 1, blue: 0 }

/** 红灯那五分钟：一轮停在报错上没人接手多久算出了问题。它和 `lib/replyWait.ts` 里等
 * 一位成员回话的阈值是同一个数：两条判据说的是同一件事——五分钟没有人来接。 */
export const LIGHT_STALL_MS = 5 * 60_000

/**
 * 这一行画哪盏灯；不该亮灯的行是 null。
 *
 * `moving` 是呼吸绿：这一行（或它名下的活）此刻有会话在跑，或者一条活走在交付链路
 * 上。它是后端按会话生命周期算好的事实，和「五分钟没动静」无关——那一套静默推断
 * 已经删掉了。
 *
 * `incompleteSince` 是红灯的第一种：一轮停在报错上、过了五分钟还没人接手。（后端只在
 * 失败没被恢复时才给这个时刻 —— 队友又说话、或者这里又开了一轮，它就不再给了，所以
 * 一条已经被人接手的失败不会一直红着。）
 *
 * `waitStalled` 是第二种：房间在等一位队友，等过了阈值他还没回来接
 * （`lib/replyWait.ts` 判的，和这里同一个五分钟）。它不画头像——一行上的点说的是
 * 「这一行现在怎么了」，和任务行同一个规矩；谁卡住了不在这颗点上说。
 *
 * `bounced` 是第三种：一条活交付上去、被人退回了，还没有人来改。它不是「有事等
 * 你」——退回那一刻平台已经把球交回给队友。
 *
 * `awaitsMe` 和 `repliedToMe` 画同一盏黄灯 —— 都是「有事等你」：一道只有你能答的问
 * 题、一张等你采纳的卡，或者一条别人回给你的消息。两样各自都有别的记法（待办、未
 * 读角标），这里的灯只负责让这一行在侧栏里先被看见。
 *
 * `now` 是调用方自己的钟：五分钟是时间走到的，不是数据变出来的（`useTopicRail`
 * 有一只为这件事拨的慢钟）。
 */
export function lightOf(
  facts: {
    /** 有事在等我（一道只有我能答的问题、一张等我采纳的卡）。 */
    awaitsMe: boolean
    /** 有人回复了我、我还没处理（回复我发的消息，或者 @ 我）。 */
    repliedToMe: boolean
    /** 有会话在跑，或者活走在交付链路上：这一行在往前走（呼吸绿）。 */
    moving: boolean
    /** 做完了、交付完了，而我还没点开看过。 */
    unseenDelivery: boolean
    /** 尚未恢复的失败从什么时候开始。 */
    incompleteSince?: string | null
    /** 房间在等一位队友，而且等过了阈值（`lib/replyWait.ts` 的 `waitStalled`）。 */
    waitStalled: boolean
    /** 这条活交付上去被退回了，还没有人来改（`presentation.phrase` 是 `bounced`）。 */
    bounced: boolean
  },
  now: number
): RowLight | null {
  const since = facts.incompleteSince ? Date.parse(facts.incompleteSince) : NaN
  if (!Number.isNaN(since) && now - since > LIGHT_STALL_MS) return { kind: 'red', why: 'unfinished' }
  if (facts.waitStalled) return { kind: 'red', why: 'waiting' }
  if (facts.bounced) return { kind: 'red', why: 'bounced' }
  if (facts.awaitsMe || facts.repliedToMe) return { kind: 'yellow' }
  if (facts.moving) return { kind: 'green', breathe: true }
  if (facts.unseenDelivery) return { kind: 'green', breathe: false }
  return null
}

/** 这一堆灯里最亮的那一盏；一盏都没有就是 null（不亮）。折叠开关在收起来的一堆
 * 行里挑一个颜色出来时用它。 */
export function brightest(lights: Iterable<RowLight | null | undefined>): RowLight | null {
  let best: RowLight | null = null
  for (const light of lights) {
    if (light && (best === null || BRIGHTNESS[light.kind] > BRIGHTNESS[best.kind])) best = light
  }
  return best
}

/** 灯上悬停（和读屏）说的那一个词：这一行现在怎么了。 */
export function lightTitle(light: RowLight): string {
  if (light.title) return light.title
  if (light.kind === 'blue') return t('work.sidebar.light.mine')
  if (light.kind === 'red') {
    if (light.why === 'unfinished') return t('work.sidebar.light.redUnfinished')
    if (light.why === 'waiting') return t('work.sidebar.light.redWaiting')
    return t('work.sidebar.light.redBounced')
  }
  if (light.kind === 'yellow') return t('work.sidebar.light.yellow')
  return t(light.breathe ? 'work.sidebar.light.moving' : 'work.sidebar.light.delivered')
}
