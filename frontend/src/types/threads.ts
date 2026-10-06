// 支线：主线上一条消息下面的回复，自成一段对话。
import type { Block } from '../cx_types'

/** 支线里最后说的一句：谁、说了什么（前两行够用的长度）、什么时候。 */
export interface ThreadReply {
  author: string
  content: string
  created_at: string
}

/** 主线上一条消息下面的支线：它自己的 id（一段独立的对话），回复数和最后一句。 */
export interface ThreadSummary {
  id: string
  room_id: string
  root_block_id: string
  reply_count: number
  last_reply_at: string | null
  last_reply: ThreadReply | null
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
  participants: string[]
  task: { id: string; title: string; status: string } | null
  unread: boolean
}

// 主线上的一条消息下面有支线、而且里面有人回过话时，`GET /topics/{频道}/blocks` 在
// 那一条上带着这一行。写在这里而不是 cx_types 里那个接口上：那个文件已经超长，只许变短。
declare module '../cx_types' {
  interface Block {
    thread?: ThreadSummary | null
  }
}
