// 通知设置页的数据形状：`/notifications/preferences` 读回来、写回去的是这一份。
// 键名是后端 `preferences.to_dict` 给的驼峰形状，一一对应。
//
// 事件类型有八类（矩阵里的八行），渠道有三种。哪一类走哪个渠道是用户的偏好，
// 后端在投递时读它（聊天通知、推送、邮件都听这一份）。

export type NotificationEmailMode = 'instant' | 'digest' | 'off'

export type NotificationDigestCadence = 'daily' | 'weekly' | 'off'

/** 矩阵里的八行。值是后端 `PreferenceCategory` 的稳定键（驼峰）。 */
export type NotificationEventCategory =
  | 'mention'
  | 'reply'
  | 'reaction'
  | 'waitsOnMe'
  | 'deviceInUse'
  | 'invitation'
  | 'announcement'
  | 'billing'

export type NotificationEventChannel = 'inApp' | 'push' | 'email'

export interface NotificationEventChoice {
  inApp: boolean
  push: boolean
  email: boolean
}

export interface NotificationPreferences {
  inAppEnabled: boolean
  pushEnabled: boolean
  emailMode: NotificationEmailMode
  quietHoursEnabled: boolean
  /** 安静时段起止，`HH:MM`。 */
  quietHoursStart: string
  quietHoursEnd: string
  digestCadence: NotificationDigestCadence
  events: Record<NotificationEventCategory, NotificationEventChoice>
}

/** 矩阵的行序：和后端 `CATEGORY_ORDER` 一致，也是设计稿自上而下的顺序。 */
export const NOTIFICATION_EVENT_CATEGORIES: NotificationEventCategory[] = [
  'mention',
  'reply',
  'reaction',
  'waitsOnMe',
  'deviceInUse',
  'invitation',
  'announcement',
  'billing',
]

export const NOTIFICATION_EVENT_CHANNELS: NotificationEventChannel[] = ['inApp', 'push', 'email']

/** 整份里能单独改的顶层字段（矩阵那八行走另一个事件）。 */
export type NotificationField = 'inAppEnabled' | 'pushEnabled' | 'emailMode' | 'quietHoursEnabled' | 'digestCadence'

/** 矩阵一格的键：`类别.渠道`。和字段名同处一个命名空间，两者不会撞。 */
export type NotificationCellKey = `${NotificationEventCategory}.${NotificationEventChannel}`

/** 这一页改一次只动一处：一个顶层字段，或矩阵里的一格。界面靠它认「正在存的是哪一处」。 */
export type NotificationChangeKey = NotificationField | NotificationCellKey

export function eventCellKey(
  category: NotificationEventCategory,
  channel: NotificationEventChannel
): NotificationCellKey {
  return `${category}.${channel}`
}

/** 词条键逐字写全：i18n 闸门照源码字面量认「这个键有人用」。 */
export const CATEGORY_LABEL_KEY: Record<NotificationEventCategory, string> = {
  mention: 'account.notifications.matrix.mention',
  reply: 'account.notifications.matrix.reply',
  reaction: 'account.notifications.matrix.reaction',
  waitsOnMe: 'account.notifications.matrix.waitsOnMe',
  deviceInUse: 'account.notifications.matrix.deviceInUse',
  invitation: 'account.notifications.matrix.invitation',
  announcement: 'account.notifications.matrix.announcement',
  billing: 'account.notifications.matrix.billing',
}

export const EVENT_CHANNEL_LABEL_KEY: Record<NotificationEventChannel, string> = {
  inApp: 'account.notifications.matrix.inApp',
  push: 'account.notifications.matrix.push',
  email: 'account.notifications.matrix.email',
}
