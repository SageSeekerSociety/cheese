// 支线：主线上一条消息下面的回复，自成一段对话。
import type { Block, RoomTask } from '../cx_types'

/** 支线里最后说的一句：谁、说了什么（前两行够用的长度）、什么时候。 */
export interface ThreadReply {
  author: string
  content: string
  created_at: string
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
  /** 此刻在这条支线里有一轮在跑的 AI 队友（handle）。只在主线那一行上有。 */
  replying?: string[]
}

/** 一条支线本身，以及它挂着的那条主线消息。 */
export interface Thread {
  id: string
  room_id: string
  root_block_id: string
  reply_count: number
  last_reply_at: string | null
  created_by: string
  created_at: string
  root?: Block | null
}

/** 频道概览「支线」那一页的一行。 */
export interface ThreadRow extends ThreadSummary {
  root: ThreadReply | null
  unread: boolean
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
