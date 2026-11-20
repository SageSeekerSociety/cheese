// 「这条通知画成什么样」的那张表：类型 → 画它的组件、它的图标。表本身不取任何
// 数据，也不认识路由，只决定画什么；所以它和它映射的这批渲染组件住在一起，
// `src/services/` 留给真正跟服务端说话的那些模块（`.claude/rules/architecture.md`）。
//
// 只收动态里会出现的那几种。叫人去处理一件事的通知（芝士的提问、邀请、加入申请、
// 等人审阅的那几句平台提示）不进动态，它们等的那件事列在「等你处理」里（后端
// `NotificationRepository._in_feed`），所以这里也没有画它们的组件。
import type { Component } from 'vue'
import type { Notification, NotificationType } from '@/types/apiNotifications'

import RenderAssistantNotification from '@/components/common/Notification/renders/RenderAssistantNotification.vue'
import RenderCreditWarningNotification from '@/components/common/Notification/renders/RenderCreditWarningNotification.vue'
import RenderDefaultNotification from '@/components/common/Notification/renders/RenderDefaultNotification.vue'
import RenderDeviceInUseNotification from '@/components/common/Notification/renders/RenderDeviceInUseNotification.vue'
import RenderMentionNotification from '@/components/common/Notification/renders/RenderMentionNotification.vue'
import RenderReactionNotification from '@/components/common/Notification/renders/RenderReactionNotification.vue'
import RenderReplyNotification from '@/components/common/Notification/renders/RenderReplyNotification.vue'
import RenderRoomNoticeNotification from '@/components/common/Notification/renders/RenderRoomNoticeNotification.vue'
import RenderSpaceAnnouncementNotification from '@/components/common/Notification/renders/RenderSpaceAnnouncementNotification.vue'
import RenderTaskNotification from '@/components/common/Notification/renders/RenderTaskNotification.vue'
import RenderTeamInvitationAcceptedNotification from '@/components/common/Notification/renders/RenderTeamInvitationAcceptedNotification.vue'
import RenderTeamInvitationCanceledNotification from '@/components/common/Notification/renders/RenderTeamInvitationCanceledNotification.vue'
import RenderTeamInvitationDeclinedNotification from '@/components/common/Notification/renders/RenderTeamInvitationDeclinedNotification.vue'
import RenderTeamRequestApprovedNotification from '@/components/common/Notification/renders/RenderTeamRequestApprovedNotification.vue'
import RenderTeamRequestCanceledNotification from '@/components/common/Notification/renders/RenderTeamRequestCanceledNotification.vue'
import RenderTeamRequestRejectedNotification from '@/components/common/Notification/renders/RenderTeamRequestRejectedNotification.vue'
import RenderThreadReplyNotification from '@/components/common/Notification/renders/RenderThreadReplyNotification.vue'

const notificationRendererRegistry: Partial<Record<NotificationType, Component>> = {
  MENTION: RenderMentionNotification,
  REPLY: RenderReplyNotification,
  THREAD_REPLY: RenderThreadReplyNotification,
  REACTION: RenderReactionNotification,
  TEAM_REQUEST_APPROVED: RenderTeamRequestApprovedNotification,
  TEAM_REQUEST_REJECTED: RenderTeamRequestRejectedNotification,
  TEAM_INVITATION_ACCEPTED: RenderTeamInvitationAcceptedNotification,
  TEAM_INVITATION_DECLINED: RenderTeamInvitationDeclinedNotification,
  TEAM_INVITATION_CANCELED: RenderTeamInvitationCanceledNotification,
  TEAM_REQUEST_CANCELED: RenderTeamRequestCanceledNotification,
  ROOM_NOTICE: RenderRoomNoticeNotification,
  DEVICE_IN_USE: RenderDeviceInUseNotification,
  SPACE_ANNOUNCEMENT: RenderSpaceAnnouncementNotification,
  TASK_NOTICE: RenderTaskNotification,
  TASK_DUE_SOON: RenderTaskNotification,
  ASSISTANT_NOTICE: RenderAssistantNotification,
  CREDIT_WARNING: RenderCreditWarningNotification,
}

/** 画这一类的组件；认不出的类型（服务端多了一种、这一版前端还不认识）用默认的那一个。 */
export function getNotificationRenderer(type: NotificationType): Component {
  return notificationRendererRegistry[type] ?? RenderDefaultNotification
}

// 平台提示一个类别码走完所有事，具体是哪件事看 `eventType`（后端 `notice_meta`）。
// 动态里会出现的那几件各有一个图标，认不出的用铃铛。
const NOTICE_ICONS: Record<string, string> = {
  task_quiet: 'mdi-clock-outline',
  timed_delivery: 'mdi-alarm',
  deploy_failed: 'mdi-alert-circle-outline',
  main_checks_failed: 'mdi-alert-circle-outline',
  policy_proposal: 'mdi-shield-check-outline',
  routine_run: 'mdi-repeat',
}

// 题目上的事一个类别码走完，具体是哪件看 `event`（后端 `task_notices`）。
const TASK_ICONS: Record<string, string> = {
  claimApproved: 'mdi-check-circle-outline',
  claimRejected: 'mdi-close-circle-outline',
  reviewed: 'mdi-clipboard-check-outline',
  requirementsChanged: 'mdi-file-document-edit-outline',
  deadlineMoved: 'mdi-calendar-clock',
  dueSoon: 'mdi-alarm',
  missed: 'mdi-account-clock-outline',
}

/** 没有人可画时，这一条左边那个小图标。 */
export function getNotificationIcon(notification: Notification): string {
  switch (notification.type) {
    case 'MENTION':
      return 'mdi-at'
    case 'REPLY':
      return 'mdi-reply'
    case 'THREAD_REPLY':
      return 'mdi-forum-outline'
    case 'REACTION':
      return 'mdi-emoticon-outline'
    case 'TEAM_REQUEST_APPROVED':
    case 'TEAM_INVITATION_ACCEPTED':
      return 'mdi-account-check-outline'
    case 'TEAM_REQUEST_REJECTED':
    case 'TEAM_INVITATION_DECLINED':
    case 'TEAM_INVITATION_CANCELED':
    case 'TEAM_REQUEST_CANCELED':
      return 'mdi-account-cancel-outline'
    case 'ROOM_NOTICE':
      return NOTICE_ICONS[String(notification.contextMetadata?.eventType ?? '')] ?? 'mdi-bell-outline'
    case 'DEVICE_IN_USE':
      return 'mdi-laptop'
    case 'SPACE_ANNOUNCEMENT':
      return 'mdi-bullhorn-outline'
    case 'TASK_DUE_SOON':
      return 'mdi-alarm'
    case 'ASSISTANT_NOTICE':
      return 'mdi-creation-outline'
    case 'TASK_NOTICE':
      return TASK_ICONS[String(notification.contextMetadata?.event ?? '')] ?? 'mdi-trophy-outline'
    case 'CREDIT_WARNING':
      // 和额度页顶上那一条同一个图形：警示三角（`CreditWarning.vue` 画的是同一颗）。
      return 'mdi-alert-outline'
    default:
      return 'mdi-bell-outline'
  }
}
