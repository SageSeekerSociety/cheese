/**
 * 预览站喂给那 10 个「通知渲染」组件的数据（`catalogNotifications.ts` 的条目读它）。
 *
 * 全部是 `/notifications` 真会返回的形状，照着各渲染组件自己的 `.spec.ts` 造：`entities`
 * 按角色给（`mentioner`、`reactor`、`replier`、`team`、`accepter`……），`contextMetadata`
 * 是那一句话的原料（`discussionTitle`、`reactionEmoji`、`reason`、`machineAccess`……）。
 * 渲染组件正文都靠 `getEntity` / `getStringMetadata` 从这两处取，所以这里给的就是它们
 * 真正会读到的那几个键。
 *
 * 每一件给两格：**有实体**（`entities` 里点了名的那一位在）和**没有实体**（后端没解析出
 * 那个人，渲染组件退回「有人……」的匿名标题）。走得是组件里 `computed` 真会有的岔路，
 * 不是编出来的。
 *
 * 这里只出通知对象，不引任何依赖：哪一份配哪个组件、每一格看什么，在 `catalogNotifications.ts`。
 */
import type { EntityInfo, Notification, NotificationType } from '@/network/api/notifications/types'

/** 一条通知的外壳：`id` / `createdAt` 取固定值，测试和预览站都不看它们。 */
function notif(
  type: NotificationType,
  entities: Record<string, EntityInfo | null>,
  contextMetadata: Record<string, unknown>
): Notification {
  return { id: 1, type, read: false, createdAt: 0, entities, contextMetadata }
}

/** 句子里那几个人。名字和 handle 就是产品里的一行名册。 */
const LINXIA: EntityInfo = { id: 'u-linxia', type: 'user', name: '林夏', handle: 'linxia' }
const BOBI: EntityInfo = { id: 'u-bobi', type: 'user', name: '波比', handle: 'bobi' }

/** 一个团队实体：`url` 是后端解析好的 `/teams/<handle>`，`teamHandle` 从它上面读。 */
const TEAM_DATA: EntityInfo = { id: 't-data', type: 'team', name: '数据组', url: '/teams/data' }

// ---- 提醒 · 提及 / 回复 / 表情回应（MENTION / REPLY / REACTION）----------------

/**
 * 「有人在讨论中提到了你」。有实体那一格带上被 @ 的那句话预览；没有实体那一格退回
 * 匿名标题，正文说「未知讨论」，也不画预览框。
 */
export function mentionNotification(withMentioner: boolean): Notification {
  return notif(
    'MENTION',
    withMentioner ? { mentioner: LINXIA } : {},
    withMentioner ? { discussionTitle: '课程安排', previewContent: '下周三的课改到线上，有课件的提前发我。' } : {}
  )
}

/** 「有人回复了你的评论」。两格差在有没有 `replier` 和那条回复的预览。 */
export function replyNotification(withReplier: boolean): Notification {
  return notif(
    'REPLY',
    withReplier ? { replier: LINXIA } : {},
    withReplier ? { discussionTitle: '作业提交', previewContent: '我按你说的把第二节重写了一遍。' } : {}
  )
}

/** 「有人对你的内容做出了反应」。`reactionEmoji` 不给时渲染组件退回默认的 👍。 */
export function reactionNotification(withReactor: boolean): Notification {
  return notif('REACTION', withReactor ? { reactor: BOBI } : {}, withReactor ? { reactionEmoji: '🎉' } : {})
}

// ---- 设备 · 那位队友开始在你这台机器上工作（DEVICE_IN_USE）-------------------

/**
 * 「芝士开始在「工作站」上工作」。`machineAccess` 决定正文尾巴上要不要挂一句
 * 「能访问整台电脑」——能不能看到整台机器是这句话里唯一真正要告诉房主的事。
 */
export function deviceInUseNotification(machineAccess: boolean): Notification {
  return notif(
    'DEVICE_IN_USE',
    {},
    {
      projectName: '课程平台',
      topicTitle: '课件评审',
      agentName: '芝士',
      agentHandle: 'cheese',
      deviceName: '工作站',
      machineAccess,
      teamHandle: 'crew',
    }
  )
}

// ---- 团队邀请的后续（ACCEPTED / DECLINED / CANCELED）--------------------------

/** 「你的邀请被接受了」。有实体那格点到是谁接受的、哪个团队；没有则是「未知团队」。 */
export function teamInvitationAcceptedNotification(withAccepter: boolean): Notification {
  return notif('TEAM_INVITATION_ACCEPTED', withAccepter ? { accepter: LINXIA, team: TEAM_DATA } : {}, {
    applicationId: 'app-1',
  })
}

/** 「你的邀请被拒绝了」。有实体再加一条 `reason`，渲染成引号里那段理由。 */
export function teamInvitationDeclinedNotification(withDecliner: boolean): Notification {
  return notif(
    'TEAM_INVITATION_DECLINED',
    withDecliner ? { decliner: BOBI, team: TEAM_DATA } : {},
    withDecliner ? { applicationId: 'app-1', reason: '最近太忙，下个季度再说。' } : {}
  )
}

/** 「有人撤回了发给你的邀请」。没有实体的那格，正文用「团队管理员」替了撤回的人。 */
export function teamInvitationCanceledNotification(withCanceler: boolean): Notification {
  return notif('TEAM_INVITATION_CANCELED', withCanceler ? { canceler: LINXIA, team: TEAM_DATA } : {}, {
    applicationId: 'app-1',
  })
}

// ---- 加入请求的后续（APPROVED / REJECTED / CANCELED）-------------------------

/** 「你的加入请求获批了」。有实体那格说出批准的人和团队。 */
export function teamRequestApprovedNotification(withApprover: boolean): Notification {
  return notif('TEAM_REQUEST_APPROVED', withApprover ? { approver: LINXIA, team: TEAM_DATA } : {}, {
    applicationId: 'app-1',
  })
}

/** 「你的加入请求被拒了」。有实体再加一条 `reason`，渲染成引号里那段理由。 */
export function teamRequestRejectedNotification(withRejector: boolean): Notification {
  return notif(
    'TEAM_REQUEST_REJECTED',
    withRejector ? { rejector: LINXIA, team: TEAM_DATA } : {},
    withRejector ? { applicationId: 'app-1', reason: '这一期名额已经满了。' } : {}
  )
}

/** 「有人取消了（自己发起的）加入请求」。没有实体时正文说「未知团队」。 */
export function teamRequestCanceledNotification(withCanceler: boolean): Notification {
  return notif('TEAM_REQUEST_CANCELED', withCanceler ? { canceler: LINXIA, team: TEAM_DATA } : {}, {
    applicationId: 'app-1',
  })
}
