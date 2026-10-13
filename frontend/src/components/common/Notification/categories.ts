// 「动态」按事由分档：一档是若干种通知类型。分档只在这里说一次——筛选问服务端要哪
// 几种（`?type=` 逗号分开的那一组），每一行上那颗小标也照它写。
//
// 和 registry.ts 一样，它只认类型名，不取数据、不认识路由。
import type { NotificationType } from '@/types/apiNotifications'

/** 三档：要你动手的、只是告诉你有这么回事的、平台自己在说事的。 */
export type NotificationCategory = 'mine' | 'notice' | 'system'

/** 画出来的先后，也是「默认停在哪一档」——需要你的排最前，进来先看它。 */
export const NOTIFICATION_CATEGORIES = ['mine', 'notice', 'system'] as const

// 两种「要你动手」的形状：请你回答或拍板（芝士的提问、平台那条提示背后的卡、审批
// 加入申请、项目/团队的邀请、截止提醒），和其余的都只是通知。系统那两种和你的项目
// 数据无关，是平台自己在说事（设备被回收、空间公告）。
const CATEGORY_OF: Record<NotificationType, NotificationCategory> = {
  CHEESE_QUESTION: 'mine',
  ROOM_NOTICE: 'mine',
  DEADLINE_REMIND: 'mine',
  PROJECT_INVITE: 'mine',
  TEAM_JOIN_REQUEST: 'mine',
  TEAM_INVITATION: 'mine',
  MENTION: 'notice',
  REPLY: 'notice',
  THREAD_REPLY: 'notice',
  REACTION: 'notice',
  TEAM_REQUEST_APPROVED: 'notice',
  TEAM_REQUEST_REJECTED: 'notice',
  TEAM_INVITATION_ACCEPTED: 'notice',
  TEAM_INVITATION_DECLINED: 'notice',
  TEAM_INVITATION_CANCELED: 'notice',
  TEAM_REQUEST_CANCELED: 'notice',
  DEVICE_IN_USE: 'system',
  SPACE_ANNOUNCEMENT: 'system',
}

/** 这一条属于哪一档。认不出的类型当「仅通知」：将来服务端多一种类型，也不会从三档
 *  里漏掉、更不会闯进「需我处理」那一档催人。 */
export function notificationCategoryOf(type: NotificationType | undefined): NotificationCategory {
  return (type && CATEGORY_OF[type]) || 'notice'
}

/** 这一档包含哪几种类型（问服务端时按这个筛）。 */
export function typesOfCategory(category: NotificationCategory): NotificationType[] {
  return (Object.keys(CATEGORY_OF) as NotificationType[]).filter((type) => CATEGORY_OF[type] === category)
}
