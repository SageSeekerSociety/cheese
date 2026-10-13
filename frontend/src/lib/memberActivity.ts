// 成员动态画成一行一行之前的样子：谁、在做什么、从什么时候开始、此刻在做哪一步。
// 名字和「哪一步」由调用方给（名册和轮次在它手上），这里只把它们拼成一种形状，
// 房间的输入框下面和现场那一格读的是同一份。

import type { MentionIdentity } from './personIdentity'

/** 一位成员此刻在一个房间里忙着的事。`since` 是 epoch 秒；打字带 `expires_in`（秒），
 * 过了这么久没有新的一下就算停了。 */
export interface MemberActivity {
  member: string
  kind: 'typing' | 'working'
  since: number
  expires_in?: number | null
}

/** 房间在等的一位成员。`member` 为 null：时间线上说不出是谁。`reason` 见 `lib/replyWait.ts`。 */
export interface MemberWait {
  member: string | null
  reason: string
  since: string
  pr?: number | null
}

/** 一位成员的「脸」：自己挑过的头像地址，和是不是 AI 队友。名册在手时由调用方给，
 *  拿不到时留空交给 `IdentityView` 按 handle 兜底。 */
export interface MemberFace {
  /** 这个人**自己挑过的**头像地址；没挑过、或名册上没他（已经退出项目）时是 null，
   *  画彩色首字母。别在这里退回默认头像 —— 那是所有没挑过的人共用的一张脸。 */
  avatar: string | null
  /** 是不是 AI 队友；名册认不出时是 null，由 `IdentityView` 按 handle 命名规则兜底。 */
  agent: boolean | null
}

/** 名册那一张表：handle → 这位自己挑过的头像地址 + 是不是队友（`lib/personIdentity` 那份）。 */
export type MentionFaceMap = Record<string, MentionIdentity>

/** 由名册那一张表算出「一位成员的脸」。表里没有的人退回 `avatarSrc`（项目名册那一份）；
 *  队友身份留 null 交给 `IdentityView` 按 handle 兜底 —— 那条规则只在一个地方写，别在
 *  这里再抄一份。 */
export function faceLookup(
  identities: MentionFaceMap,
  avatarSrc: (handle: string) => string
): (handle: string) => MemberFace {
  return (handle) => {
    const known = identities[handle]
    return { avatar: known?.avatar ?? avatarSrc(handle), agent: known?.agent ?? null }
  }
}

export interface MemberActivityLine {
  handle: string
  name: string
  /** 这一位的脸（头像地址 + 是不是队友）。只画名字、不给这个，就退回一个光秃秃的名字。 */
  face: MemberFace
  kind: 'typing' | 'working'
  /** epoch 秒。 */
  since: number
  /** 干活的队友此刻在做的那一步（「思考中」「正在读文件」）；没有就不说。 */
  detail?: string | null
  /** 更细的一步（「执行命令 pnpm test」），从 live 帧读的；有它就顶掉 `detail`。 */
  step?: string | null
  /** 最后一次收到这位成员一帧的时刻（epoch 毫秒）；不知道就是 null，不说卡住。 */
  lastFrameAt?: number | null
}

export function activityLines(
  entries: MemberActivity[],
  nameOf: (handle: string) => string,
  detailOf: (handle: string) => string | null = () => null,
  stepOf: (handle: string) => string | null = () => null,
  lastFrameOf: (handle: string) => number | null = () => null,
  faceOf: (handle: string) => MemberFace = () => ({ avatar: null, agent: null })
): MemberActivityLine[] {
  return [...entries]
    .sort((a, b) => a.since - b.since)
    .map((e) => ({
      handle: e.member,
      name: nameOf(e.member),
      face: faceOf(e.member),
      kind: e.kind,
      since: e.since,
      detail: e.kind === 'working' ? detailOf(e.member) : null,
      step: e.kind === 'working' ? stepOf(e.member) : null,
      lastFrameAt: e.kind === 'working' ? lastFrameOf(e.member) : null,
    }))
}

/** 侧栏一行上的一位成员：在干活的（绿点），或房间在等它、等太久了的（红点）。 */
export interface RailMemberMark {
  handle: string
  name: string
  /**
   * 这个人**自己挑过的**头像地址；没挑过、或名册上没他（已经退出项目）时是 null，
   * 画彩色首字母。别在这里退回默认头像 —— 那是所有没挑过的人共用的一张脸。
   */
  avatar: string | null
  agent: boolean
  state: 'working' | 'stalled'
  /** 悬停时说的那一句：谁、在做什么 / 为什么在等它。 */
  title: string
}
