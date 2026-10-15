import type { BoardPhrase } from '../cx_types'

/** 待我处理清单里的一行（后端 `/awaiting-me`）：项目里的一件事，或一条等我回答的
 *  邀请、申请。两种按 `kind` 分。 */
export type WaitingItem = WaitingWork | WaitingAsk

/** 项目里等我的一件事（后端 `room_task/awaiting.py`）。
 *
 *  `phrase` 就是看板卡面上那一句的码，后端算好的 —— 前端不推状态，理由和
 *  `Presentation` 那一段一样。`reason` 说的是这件事为什么点到我：递给我验收
 *  (`reviewer`)、我提的需求有了结果 (`reporter`)、或者芝士停在一个只有我能回答的
 *  待回答的问题上 (`asked`)。 */
export interface WaitingWork {
  kind: 'work'
  projectId: string
  projectName: string
  /** 在哪个频道；来自通知、不指向频道的那几件没有。 */
  topicId: string | null
  topicTitle: string
  taskId: string | null
  taskTitle: string | null
  taskTitleSource?: string | null
  /** 任务那一格的短语；来自通知的两种不是哪一格，写 `decision` 或 `change_alert`。 */
  phrase: BoardPhrase | 'decision' | 'change_alert'
  /** 为什么在等你：审阅人、提需求的人、被问的人、任务负责人，有一件事要你拍板，
   *  或者芝士告诉你改了什么、你还没读。 */
  reason: 'reviewer' | 'reporter' | 'asked' | 'owner' | 'decide' | 'read'
  blockId?: string | null
  /** 问题是芝士在哪条支线里问的；不在支线里时没有。 */
  threadId?: string | null
  /** 第二行：等的是什么（提问的原话、改动的主题、停住的原因、拍板的说明）；没有就空。 */
  detail?: string
  /** 来自通知的才有：哪一条、它的标题（要拍板的问题、改了什么），决策请求可选的答案。 */
  alertId?: number | null
  headline?: string
  options?: string[]
  at: string
}

/** 一行上画的一个人或一个团队。头像只有自己挑过才有，没有就画彩色首字母。 */
export interface WaitingFace {
  name: string
  handle: string | null
  avatarUrl: string | null
}

/** 等我回答的一条邀请或申请（后端 `api/awaiting_asks.py`）：有人邀请我加入项目或
 *  团队，或有人申请加入我管的团队。答了就不在清单上了。 */
export interface WaitingAsk {
  kind: 'project_invite' | 'team_invite' | 'join_request'
  /** 回答时交回去的号：项目邀请是 uuid，团队的邀请和申请是数字的字符串。 */
  id: string
  /** 加入的是哪里：项目或团队的 id。 */
  placeId: string
  place: WaitingFace
  /** 发起的人：邀请人，或提出申请的人。 */
  person: WaitingFace
  /** 团队邀请给的角色；其余两种没有。 */
  role: 'MEMBER' | 'ADMIN' | 'OWNER' | null
  /** 邀请或申请时附的那句话；没写是空串。 */
  message: string
  at: string
}
