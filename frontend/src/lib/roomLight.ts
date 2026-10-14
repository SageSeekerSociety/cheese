// 侧栏一行上那盏灯：这一行现在是什么状态，一眼看得出来。
//
// 灯是**颜色 + 动不动**两件事：
//
//   呼吸绿（动）  这里在往前走：有一轮在跑，或者活已经递出去、走在交付链路上
//                 （等闸门、等 CI、修检查、解冲突、换基）。
//   长绿（不动）  这里做过事、都做完交付完了，你还没点开看过。点开过就灭；之后
//                 再有新活，回到呼吸绿。
//   红（不动）    这里现在不对：显示在跑、但五分钟没动静，或者一轮停在报错上、
//                 五分钟没人接手（两件事长的是同一盏，`RowLight.why` 说清是哪一件）。
//   黄（不动）    有事等你：一道只有你能答的问题、一张等你采纳的卡，或者有人回复了
//                 我、我还没处理。
//   蓝（不动）    这条活归我，状态上没别的话可说。只有任务行用得着。
//   不亮          没开过、也没活在跑的，不亮灯。
//
// 六条判据全是**事实**，这一份不推状态、只排先后：在不在往前走看 `moving`
// （后端按「活的事实压过纸面」算，房间那一行还含它名下的活），等不等人看
// `awaits_me`（后端按看的人算），多久没输出看 `last_output_at`（后端取整到分钟），
// 交付完没看过看 `unseen_delivery`（后端比的是这个人的已读位和采纳时刻），有人回了
// 我没处理看 `repliedToMe`（后端比的是这个人上次打开这间房的时刻和那条回复落地的
// 时刻），还有没有一轮停在报错上没恢复、从什么时候起看 `incompleteSince`（后端
// `block/waits.py` 的 `unresolved_failures`：那一轮的队友又说话了，或者这里又开了
// 一轮，就算恢复了 —— 和「等了多久」无关，所以一条没人理的旧失败会一直红着，一条
// 已经被人接手的不会）。
//
// 蓝灯**不是判据**：谁负责写在数据里（`owner_handle`），`lightOf` 不看它，
// 也永远不会返回蓝灯——它是调用方盖的一枚印（`useTopicRail.taskLight`），而且只在
// 状态那几盏都没话说的时候才盖：它说的那件事（这条归我）比状态轻。
//
// 优先级：红 > 黄 > 呼吸绿 > 长绿 > 蓝 —— 一行上只画一盏灯，蓝最暗。蓝垫底是刻意
// 的：红黄绿说的是这条活现在怎么了，非说不可；蓝只说这条归我，没别的可说时才轮到
// 它。折叠开关在收起来的一堆行里挑一个颜色出来时也走同一个次序（`brightest`）。
//
// 「点开过就灭」不在这一份里：那是数据的事（服务端按已读位算 `unseen_delivery` 和
// `replied_to_me`，点开时本地先把那一行灭掉，见 `stores/workspace` 的
// `forgetReadLights`）。这里因此是个纯函数，测得起来。

import { t } from '@/i18n'

/** 一行上的灯画哪一盏。`green` 那一盏闪不闪由 `RowLight.breathe` 说。 */
export type LightKind = 'blue' | 'red' | 'yellow' | 'green'

/** 红灯为什么亮：两种都画同一盏，但嘴上说的是两件事（`lightTitle`）。 */
export type RedReason = 'quiet' | 'unfinished'

export interface RowLight {
  kind: LightKind
  /** 绿色动不动：呼吸绿闪，长绿不闪。蓝红黄一律不动。 */
  breathe: boolean
  /** 只对红灯有意义：`quiet` = 在跑、但五分钟没输出；`unfinished` = 一轮停在报错
   *  上、五分钟没人接手。说不出是哪一件时省略（悬停说明退回那句笼统的）。 */
  why?: RedReason
}

/** 我负责的活那一盏灯：`lightOf` 不产它，调用方在前面四盏都不亮时盖上（见文件头）。 */
export const MINE_LIGHT: RowLight = { kind: 'blue', breathe: false }

/** 亮到暗。`brightest` 用它排序。蓝垫底：它说的是这条归谁，不是这条怎么了。 */
const BRIGHTNESS: Record<LightKind, number> = { red: 3, yellow: 2, green: 1, blue: 0 }

/** 红灯那五分钟：显示在跑却没动静，和一轮停在报错上没人接手，共用一个数。
 * 它和 `lib/replyWait.ts` 里等一位成员回话的阈值也是同一个数：三条判据说的是同一件
 * 事——五分钟没有新东西出来。 */
export const LIGHT_STALL_MS = 5 * 60_000

/** 上一次有输出，距今多久（毫秒）。说不出（没在跑、或那个戳没记着）就是 null。 */
function quietForMs(lastOutputAt: string | null | undefined, now: number): number | null {
  if (!lastOutputAt) return null
  const at = Date.parse(lastOutputAt)
  if (Number.isNaN(at)) return null
  return now - at
}

/**
 * 这一行画哪盏灯；不该亮灯的行是 null。
 *
 * `moving` 是「这里在不在往前走」，**它管两盏**：往前走的画呼吸绿，往前
 * 走、又已经五分钟没有新输出的画红。后者要 `lastOutputAt` 说得出话说得准 —— 说不
 * 出来（没在跑、或那个进程说不了）就不画红：不知道不等于卡住。
 *
 * `incompleteSince` 是红灯的另一半，**不看它跑不跑**：一轮停在报错上、过了五分钟
 * 还没人接手，也是「出问题了」。（后端只在失败没被恢复时才给这个时刻 —— 队友又说
 * 话、或者这里又开了一轮，它就不再给了，所以一条已经被人接手的失败不会一直红着。）
 * 它比 `moving` 更重：真有东西坏着的时候，读数要从坏的这一刻起，而不是从最后一次
 * 输出起。两个都够五分钟时说的是同一盏红，`why` 记着是哪一件。
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
    /** 这里有活在往前走：有一轮在跑，或者走在交付链路上。 */
    moving: boolean
    /** 做完了、交付完了，而我还没点开看过。 */
    unseenDelivery: boolean
    /** 尚未恢复的失败从什么时候开始；不依赖这一行是否还在跑。 */
    incompleteSince?: string | null
    /** 在跑的那一轮上一次有输出是什么时候。 */
    lastOutputAt: string | null | undefined
  },
  now: number
): RowLight | null {
  const incomplete = quietForMs(facts.incompleteSince, now)
  const quiet = quietForMs(facts.lastOutputAt, now)
  if (incomplete !== null && incomplete > LIGHT_STALL_MS) {
    return { kind: 'red', breathe: false, why: 'unfinished' }
  }
  if (facts.moving && quiet !== null && quiet >= LIGHT_STALL_MS) {
    return { kind: 'red', breathe: false, why: 'quiet' }
  }
  if (facts.awaitsMe || facts.repliedToMe) return { kind: 'yellow', breathe: false }
  if (facts.moving) return { kind: 'green', breathe: true }
  if (facts.unseenDelivery) return { kind: 'green', breathe: false }
  return null
}

/** 亮到暗排出来的名次。同一种颜色里，动的那一盏压过不动的：一堆收起来的行里既
 * 有在往前走的、也有做完了的，开关该说的是前者。 */
function rank(light: RowLight): number {
  return BRIGHTNESS[light.kind] * 2 + (light.breathe ? 1 : 0)
}

/** 这一堆灯里最亮的那一盏；一盏都没有就是 null（不亮）。折叠开关在收起来的一堆
 * 行里挑一个颜色出来时用它。 */
export function brightest(lights: Iterable<RowLight | null | undefined>): RowLight | null {
  let best: RowLight | null = null
  for (const light of lights) {
    if (light && (best === null || rank(light) > rank(best))) best = light
  }
  return best
}

/** 灯上悬停（和读屏）说的那一个词：这一行现在怎么了。 */
export function lightTitle(light: RowLight): string {
  if (light.kind === 'blue') return t('work.sidebar.light.mine')
  if (light.kind === 'red') {
    // 两种红说的是两件事：在跑却没动静，和停下报错没人接手。说得出是哪一件就说哪
    // 一件；说不出（`why` 缺省，比如折叠开关那一盏）退回那句笼统的。
    if (light.why === 'unfinished') return t('work.sidebar.light.redUnfinished')
    return t('work.sidebar.light.red')
  }
  if (light.kind === 'yellow') return t('work.sidebar.light.yellow')
  return t(light.breathe ? 'work.sidebar.light.moving' : 'work.sidebar.light.delivered')
}
