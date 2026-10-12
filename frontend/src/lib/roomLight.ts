// 侧栏一行上那盏灯：这一行现在是什么状态，一眼看得出来。
//
// 灯是**颜色 + 动不动**两件事：
//
//   呼吸绿（动）  这里在往前走：有一轮在跑，或者活已经递出去、走在交付链路上
//                 （等闸门、等 CI、修检查、解冲突、换基）。
//   长绿（不动）  这里做过事、都做完交付完了，你还没点开看过。点开过就灭；之后
//                 再有新活，回到呼吸绿。
//   红（不动）    显示在跑，但五分钟没动静。
//   黄（不动）    有事等你：一道只有你能答的问题、一张等你采纳的卡。
//   不亮          没开过、也没活在跑的，不亮灯。
//
// 四条判据全是**事实**，这一份不推状态、只排先后：在不在往前走看 `moving`
// （后端按「活的事实压过纸面」算，房间那一行还含它名下的活），等不等人看
// `awaits_me`（后端按看的人算），多久没输出看 `last_output_at`（后端取整到分钟），
// 交付完没看过看 `unseen_delivery`（后端比的是这个人的已读位和采纳时刻）。
//
// 优先级：红 > 黄 > 呼吸绿 > 长绿 —— 一行上只画一盏灯，红最亮。折叠开关在收起来
// 的一堆行里挑一个颜色出来时也走同一个次序（`brightest`）。
//
// 「点开过就灭」不在这一份里：那是数据的事（服务端按已读位算 `unseen_delivery`，
// 点开时本地先把那一行灭掉，见 `stores/workspace` 的 `forgetUnseenDelivery`）。
// 这里因此是个纯函数，测得起来。

import { t } from '@/i18n'

/** 一行上的灯画哪一盏。`green` 那一盏闪不闪由 `RowLight.breathe` 说。 */
export type LightKind = 'red' | 'yellow' | 'green'

export interface RowLight {
  kind: LightKind
  /** 绿色动不动：呼吸绿闪，长绿不闪。红黄一律不动。 */
  breathe: boolean
}

/** 亮到暗。`brightest` 用它排序。 */
const BRIGHTNESS: Record<LightKind, number> = { red: 3, yellow: 2, green: 1 }

/** 「显示在跑、但五分钟没动静」的那五分钟，和 `lib/replyWait.ts` 里等一位成员
 * 回话的阈值是同一个数：两条判据说的是同一件事——五分钟没有新东西出来。 */
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
 * `moving` 是「这里在不在往前走」，**四种灯里它管两盏**：往前走的画呼吸绿，往前
 * 走、又已经五分钟没有新输出的画红。后者要 `lastOutputAt` 说得出话说得准 —— 说不
 * 出来（没在跑、或那个进程说不了）就不画红：不知道不等于卡住。
 *
 * `now` 是调用方自己的钟：五分钟是时间走到的，不是数据变出来的（`useTopicRail`
 * 有一只为这件事拨的慢钟）。
 */
export function lightOf(
  facts: {
    /** 有事在等我（一道只有我能答的问题、一张等我采纳的卡）。 */
    awaitsMe: boolean
    /** 这里有活在往前走：有一轮在跑，或者走在交付链路上。 */
    moving: boolean
    /** 做完了、交付完了，而我还没点开看过。 */
    unseenDelivery: boolean
    /** 在跑的那一轮上一次有输出是什么时候。 */
    lastOutputAt: string | null | undefined
  },
  now: number
): RowLight | null {
  const quiet = quietForMs(facts.lastOutputAt, now)
  if (facts.moving && quiet !== null && quiet >= LIGHT_STALL_MS) {
    return { kind: 'red', breathe: false }
  }
  if (facts.awaitsMe) return { kind: 'yellow', breathe: false }
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
  if (light.kind === 'red') return t('work.sidebar.light.red')
  if (light.kind === 'yellow') return t('work.sidebar.light.yellow')
  return t(light.breathe ? 'work.sidebar.light.moving' : 'work.sidebar.light.delivered')
}
