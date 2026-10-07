// 「这条通知画成什么样」的那张表：类型 → 画它的组件、它的图标与颜色。表本身不取任何
// 数据，也不认识路由，只决定画什么；所以它和它映射的这批渲染组件住在一起，
// `src/services/` 留给真正跟服务端说话的那些模块（`.claude/rules/architecture.md`）。
import type { Component } from 'vue'
import type { Notification, NotificationType } from '@/network/api/notifications/types'

import RenderCheeseQuestionNotification from '@/components/common/Notification/renders/RenderCheeseQuestionNotification.vue'
import RenderDeadlineRemindNotification from '@/components/common/Notification/renders/RenderDeadlineRemindNotification.vue'
import RenderDefaultNotification from '@/components/common/Notification/renders/RenderDefaultNotification.vue'
import RenderDeviceInUseNotification from '@/components/common/Notification/renders/RenderDeviceInUseNotification.vue'
// 导入渲染组件
import RenderMentionNotification from '@/components/common/Notification/renders/RenderMentionNotification.vue'
import RenderProjectInviteNotification from '@/components/common/Notification/renders/RenderProjectInviteNotification.vue'
import RenderReactionNotification from '@/components/common/Notification/renders/RenderReactionNotification.vue'
import RenderReplyNotification from '@/components/common/Notification/renders/RenderReplyNotification.vue'
import RenderRoomNoticeNotification from '@/components/common/Notification/renders/RenderRoomNoticeNotification.vue'
import RenderSpaceAnnouncementNotification from '@/components/common/Notification/renders/RenderSpaceAnnouncementNotification.vue'
import RenderTeamInvitationAcceptedNotification from '@/components/common/Notification/renders/RenderTeamInvitationAcceptedNotification.vue'
import RenderTeamInvitationCanceledNotification from '@/components/common/Notification/renders/RenderTeamInvitationCanceledNotification.vue'
import RenderTeamInvitationDeclinedNotification from '@/components/common/Notification/renders/RenderTeamInvitationDeclinedNotification.vue'
import RenderTeamInvitationNotification from '@/components/common/Notification/renders/RenderTeamInvitationNotification.vue'
import RenderTeamJoinRequestNotification from '@/components/common/Notification/renders/RenderTeamJoinRequestNotification.vue'
import RenderTeamRequestApprovedNotification from '@/components/common/Notification/renders/RenderTeamRequestApprovedNotification.vue'
import RenderTeamRequestCanceledNotification from '@/components/common/Notification/renders/RenderTeamRequestCanceledNotification.vue'
import RenderTeamRequestRejectedNotification from '@/components/common/Notification/renders/RenderTeamRequestRejectedNotification.vue'

// 通知类型到渲染组件的映射
const notificationRendererRegistry: Record<NotificationType, Component> = {
  MENTION: RenderMentionNotification,
  REPLY: RenderReplyNotification,
  // 标题和正文是后端写好的那一句，照原样显示。
  THREAD_REPLY: RenderDefaultNotification,
  REACTION: RenderReactionNotification,
  PROJECT_INVITE: RenderProjectInviteNotification,
  DEADLINE_REMIND: RenderDeadlineRemindNotification,
  TEAM_JOIN_REQUEST: RenderTeamJoinRequestNotification,
  TEAM_INVITATION: RenderTeamInvitationNotification,
  TEAM_REQUEST_APPROVED: RenderTeamRequestApprovedNotification,
  TEAM_REQUEST_REJECTED: RenderTeamRequestRejectedNotification,
  TEAM_INVITATION_ACCEPTED: RenderTeamInvitationAcceptedNotification,
  TEAM_INVITATION_DECLINED: RenderTeamInvitationDeclinedNotification,
  TEAM_INVITATION_CANCELED: RenderTeamInvitationCanceledNotification,
  TEAM_REQUEST_CANCELED: RenderTeamRequestCanceledNotification,
  ROOM_NOTICE: RenderRoomNoticeNotification,
  CHEESE_QUESTION: RenderCheeseQuestionNotification,
  DEVICE_IN_USE: RenderDeviceInUseNotification,
  SPACE_ANNOUNCEMENT: RenderSpaceAnnouncementNotification,
}

/**
 * 根据通知类型获取对应的渲染组件
 * @param type 通知类型
 * @returns 对应的渲染组件，如果找不到则返回默认组件
 */
export function getNotificationRenderer(type: NotificationType): Component {
  return notificationRendererRegistry[type] || RenderDefaultNotification
}

/**
 * 获取通知图标
 * @param type 通知类型
 * @returns 对应的图标名称
 */
export function getNotificationIcon(type: NotificationType): string {
  switch (type) {
    case 'MENTION':
      return 'mdi-at'
    case 'REPLY':
      return 'mdi-reply'
    case 'THREAD_REPLY':
      return 'mdi-forum-outline'
    case 'REACTION':
      return 'mdi-emoticon'
    case 'PROJECT_INVITE':
      return 'mdi-account-multiple-plus'
    case 'DEADLINE_REMIND':
      return 'mdi-calendar-clock'
    case 'TEAM_JOIN_REQUEST':
      return 'mdi-account-question'
    case 'TEAM_INVITATION':
      return 'mdi-account-multiple-plus'
    case 'TEAM_REQUEST_APPROVED':
      return 'mdi-check-circle'
    case 'TEAM_REQUEST_REJECTED':
      return 'mdi-close-circle'
    case 'TEAM_INVITATION_ACCEPTED':
      return 'mdi-check-circle'
    case 'TEAM_INVITATION_DECLINED':
      return 'mdi-close-circle'
    case 'TEAM_INVITATION_CANCELED':
      return 'mdi-cancel'
    case 'TEAM_REQUEST_CANCELED':
      return 'mdi-cancel'
    case 'ROOM_NOTICE':
      return 'mdi-bell-ring-outline'
    case 'CHEESE_QUESTION':
      return 'mdi-help-circle-outline'
    case 'DEVICE_IN_USE':
      return 'mdi-laptop-account'
    case 'SPACE_ANNOUNCEMENT':
      return 'mdi-bullhorn-outline'
    default:
      return 'mdi-bell'
  }
}

/**
 * 获取通知颜色
 * @param type 通知类型
 * @returns 对应的颜色
 */
export function getNotificationColor(type: NotificationType): string {
  switch (type) {
    case 'TEAM_REQUEST_APPROVED':
    case 'TEAM_INVITATION_ACCEPTED':
      return 'success'
    case 'TEAM_REQUEST_REJECTED':
    case 'TEAM_INVITATION_DECLINED':
    case 'TEAM_INVITATION_CANCELED':
    case 'TEAM_REQUEST_CANCELED':
      return 'error'
    // 这两条都只在事情落到人手上时才发出，所以它们总是「待你处理」——和看板上
    // 那一列同一个暖色。CHEESE_QUESTION 更甚：本轮已经停在那个问题上。
    case 'DEADLINE_REMIND':
    case 'ROOM_NOTICE':
    case 'CHEESE_QUESTION':
      return 'warning'
    default:
      return 'primary'
  }
}

/**
 * 这一条通知此刻的图标和颜色。大多数类型只看类型；芝士的提问答过之后（服务端
 * `ledger.settle` 把回答并进 `answered`）就不再是「待你处理」，换成和房间里那张
 * 卡一样的已回答对勾，颜色从 warning 退成 success —— 还画成警示色，等于说它还在等人。
 */
export function getNotificationMark(notification: Notification): { icon: string; color: string } {
  if (notification.type === 'CHEESE_QUESTION' && notification.contextMetadata?.answered) {
    return { icon: 'mdi-check-circle-outline', color: 'success' }
  }
  return { icon: getNotificationIcon(notification.type), color: getNotificationColor(notification.type) }
}
