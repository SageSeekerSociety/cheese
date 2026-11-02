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
  /** `retrying`：这位队友的这一轮调用模型出错了，在等平台自动重试（useRunRecords）。 */
  kind: 'typing' | 'working' | 'retrying'
  /** epoch 秒。 */
  since: number
  /** 干活的队友此刻在做的那一步（「思考中」「正在读文件」）；没有就不说。 */
  detail?: string | null
  /** 更细的一步（「执行命令 pnpm test」），从 live 帧读的；有它就顶掉 `detail`。 */
  step?: string | null
  /** 最后一次收到这位成员一帧的时刻（epoch 毫秒），用来认出上一轮留下的帧；不知道就是 null。 */
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

/** 侧栏一行上的一位成员：此刻在这个房间里干活的队友（绿点）。
 *
 *  房间在等一位队友、等太久了不是在这里画的：那是这一行的状态，由行尾那盏红灯说
 *  （`lib/roomLight.ts` 的 `waiting`）——一行上的点说的是「这一行现在怎么了」，
 *  和任务行同一个规矩。 */
export interface RailMemberMark {
  handle: string
  name: string
  /**
   * 这个人**自己挑过的**头像地址；没挑过、或名册上没他（已经退出项目）时是 null，
   * 画彩色首字母。别在这里退回默认头像 —— 那是所有没挑过的人共用的一张脸。
   */
  avatar: string | null
  agent: boolean
  /** 悬停时说的那一句：谁、在做什么。 */
  title: string
}
