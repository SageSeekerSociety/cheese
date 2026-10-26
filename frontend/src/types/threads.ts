// 支线：主线上一条消息下面的回复，自成一段对话。
import type { AuthorType, Block, ChecklistMeta, RoomTask } from '../cx_types'

/** 支线里最后说的一句：谁、说了什么（前两行够用的长度）、什么时候。 */
export interface ThreadReply {
  author: string
  /** 谁那一档写的、是消息还是附件：同 `Block` 上那两列。 */
  author_type?: AuthorType
  kind?: string
  /** 前两行够用的长度；`truncated` 说它是不是被截过。 */
  content: string
  truncated?: boolean
  created_at: string
  /** 这一句是队友的步骤清单时它那份结构。摘要只显示一步，而正文里一步一行、折成
   * 一行之后只剩一串记号，所以结构得由后端随回复带上（不是清单就是 null）。迁移前的
   * 清单没有结构，只有一个 `true`：那一步从正文里读。 */
  checklist?: ChecklistMeta | true | null
}

/** 从支线（和它挂着的那条消息）出来的一件任务。 */
export interface ThreadTask {
  id: string
  title: string
  status: string
}

/** 主线上一条消息下面的支线：它自己的 id（一段独立的对话），回复数、最后一句、谁说过
 * 话（挂着的那条消息的作者在前）、出了哪几件任务，以及此刻哪几位 AI 队友正在里面回答。 */
export interface ThreadSummary {
  id: string
  room_id: string
  root_block_id: string
  reply_count: number
  last_reply_at: string | null
  last_reply: ThreadReply | null
  participants: string[]
  tasks: ThreadTask[]
  /** 最后一条回复之后，AI 队友在这条支线里的一轮出错了。 */
  failed?: boolean
  /** 那一轮为什么出错：`credits_exhausted` 是额度用完，重试没有用。 */
  failed_code?: string | null
  /** 此刻在这条支线里有一轮在跑的 AI 队友（handle）。只在主线那一行上有。 */
  replying?: string[]
  /** 收起来的时刻（`thread/recycle.py`），开着的是 null。收起来的支线仍然读得到，
   *  只是从默认那一栏挪到了「已回收」。 */
  recycled_at?: string | null
}

/** 一条支线本身，以及它挂着的那条主线消息。 */
export interface Thread {
  id: string
  room_id: string
  root_block_id: string
  reply_count: number
  last_reply_at: string | null
  /** 还在读这一条时先画的那一份（拿手上那块主线消息拼的，`query/room`）没有这两个：
   *  那块消息只带着支线的 id、回复数和最后一句。谁都不读它们。 */
  created_by?: string
  created_at?: string
  root?: Block | null
  /** 它挂着的那条消息一起发出去的附件（图片、文件）：留在主线里，画这段对话最上面
   *  那条消息时，也按主线的样子画在它下面。还没读回来、或手上只有预览时没有这一项。 */
  files?: Block[]
  /** 这条支线正在和它说话的 AI 队友（席位 handle）：最近被叫来的那位，还在频道里时。
   *  没人叫过、或它已离开频道时是 null。在这里不 @ 任何人说的话就是说给它的。 */
  teammate?: string | null
  /** 这条支线已经原地转成了任务：任务的 id，就是支线自己的 id。还没转是 null。 */
  task?: string | null
}

/** 频道概览「支线」那一页的一行。 */
export interface ThreadRow extends ThreadSummary {
  root: ThreadReply | null
  unread: boolean
}

/** 一个频道的支线，两栏：开着的和收起来的。收起来的仍然读得到，只是从默认那一栏
 *  挪开了。 */
export interface ThreadLists {
  open: ThreadRow[]
  recycled: ThreadRow[]
}

/** 一位 AI 队友在频道的某条支线里开始或停下回答：频道主线收到这一帧，那条消息下面
 * 那一行据此写「正在回复」。支线自己的帧主线听不到。 */
export interface ThreadActivityFrame {
  type: 'thread_activity'
  thread_id: string
  member: string
  active: boolean
}

// 主线上的一条消息下面有支线、而且里面有人回过话时，`GET /topics/{频道}/blocks` 在
// 那一条上带着这一行。写在这里而不是 cx_types 里那个接口上：那个文件已经超长，只许变短。
declare module '../cx_types' {
  interface Block {
    thread?: ThreadSummary | null
  }
}

// `GET /topics/{频道}/blocks` 在一块上带着它那几件任务：从这条消息拆出去的，或者这一行
// 说在这里开始的那一件（composables/useTimelineTasks）。
declare module '../cx_types' {
  interface Block {
    tasks?: RoomTask[]
  }
}

// `GET /topics` 的每一行带着侧栏挂在它下面的任务：和我有关的全部，不足三条时补上这个
// 频道别人的几条（那些行带 `filling_in`），加这个频道进行中的一共几件
// （views/workspace/ProjectSidebar）。
declare module '../cx_types' {
  interface Topic {
    my_tasks?: { shown: RoomTask[]; open: number }
  }
}

// 侧栏那一块里的一条：这一条不是和我有关的，是从这个频道里别人的活借来把这一块凑够三条
// 的（`projects.rail_tasks`）。侧栏把它画淡一档，悬浮说明写清它为什么在这儿。只有
// `my_tasks.shown` 这份行带它；别的调用方（项目级列表、频道里的全部任务）不带，读作
// false——那是「和我无关」，不是「说不清」。
declare module '../cx_types' {
  interface RoomTask {
    filling_in?: boolean
  }
}
