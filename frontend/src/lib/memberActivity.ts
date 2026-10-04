// 成员动态画成一行一行之前的样子：谁、在做什么、从什么时候开始、此刻在做哪一步。
// 名字和「哪一步」由调用方给（名册和轮次在它手上），这里只把它们拼成一种形状，
// 房间的输入框下面和现场那一格读的是同一份。

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

export interface MemberActivityLine {
  handle: string
  name: string
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
  lastFrameOf: (handle: string) => number | null = () => null
): MemberActivityLine[] {
  return [...entries]
    .sort((a, b) => a.since - b.since)
    .map((e) => ({
      handle: e.member,
      name: nameOf(e.member),
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
  agent: boolean
  state: 'working' | 'stalled'
  /** 悬停时说的那一句：谁、在做什么 / 为什么在等它。 */
  title: string
}
