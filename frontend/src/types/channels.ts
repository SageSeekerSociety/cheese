import type { Block } from '../cx_types'

// 我和一个频道之间按人记的那几样：在等我的东西，和我设的通知档位。

/**
 * 一个频道或任务在等我的东西：`count` 是行上的数字（频道按我设的通知档位算），
 * `new` 是名字要不要加粗，`messages` 是我上次读之后来了几条消息（打开时那条
 * 「新消息」线画在哪）。
 */
export interface TopicUnread {
  count: number
  new: boolean
  messages: number
}

/**
 * 我对一个频道的通知档位：`all` 所有新消息，`mentions` 只在 @我和我参与的支线有
 * 回复时（默认），`mute` 静音（只有 @我）。
 */
export type TopicNotifyLevel = 'all' | 'mentions' | 'mute'

/** 一个频道不在默认档位时的设置；静音可以有截止时间，`null` = 直到我取消。 */
export interface TopicNotifySetting {
  level: TopicNotifyLevel
  muted_until: string | null
}

/** 频道的一条置顶（`GET /topics/{id}/pins`）：钉住的那一条，和谁、什么时候钉的。 */
export interface ChannelPin {
  pinned_by: string
  pinned_at: string
  /** 钉住的那一条：消息，或一个文件（附件、AI 队友摆出来的东西）。 */
  block: Block
}
