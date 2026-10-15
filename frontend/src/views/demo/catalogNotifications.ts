/**
 * 「通知」那一组在预览站里的条目：每一条动态的脸（`renders/Render*Notification.vue`）、
 * 那一行本身（`NotificationItem.vue`）和它画在左边的头像（`NotificationAvatar.vue`）。
 *
 * 主题的每一条动态都有自己的一张脸：`Render*Notification.vue` 收一个 `Notification`，
 * 画出标题、正文（有时还有一句预览或一条理由），并 `defineExpose({ content })` 把去处和
 * 那一行自己的动作交给外面。它们以前只能靠整个 `NotificationItem`（跟着列表、图标、已读
 * 状态一起）才看得见，现在每一件都只吃 props，于是能单独摆在预览站里看。
 *
 * 行里就能办的三类（项目邀请、团队邀请、加入申请）多一个 `answering` 桥：请求和「答过
 * 没有」住在容器侧（`views/home/useNotificationAnswers.ts`），渲染件只读桥上递进来的
 * 状态、只点桥上的动作，自己不取数 —— 预览站给一份同样形状的桩（`answeringBridge`）。
 *
 * 它们和别的条目没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单独一份是因为 `catalog.ts` 已经顶到一千行的上限 —— 和 `catalogRail.ts`、
 * `catalogModels.ts` 同一个理由。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `NOTIFICATION_ENTRIES`
 * 这个值，运行时不构成循环。数据见 `catalogNotificationsFixtures.ts`。
 *
 * 渲染件那几件给两格，走的是组件里 `computed` 真会有的岔路：`entities` 里点名的那一位在，
 * 与不在。在时标题里就是这个人（正文那一句也一样）；不在时组件退回「有人……」的匿名标题，
 * 团队名字退回「未知团队」。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  answeringBridge,
  channelMessageNotification,
  deviceInUseNotification,
  mentionNotification,
  projectInviteNotification,
  reactionNotification,
  replyNotification,
  teamInvitationAcceptedNotification,
  teamInvitationCanceledNotification,
  teamInvitationDeclinedNotification,
  teamInvitationNotification,
  teamJoinRequestNotification,
  teamRequestApprovedNotification,
  teamRequestCanceledNotification,
  teamRequestRejectedNotification,
} from './catalogNotificationsFixtures'

import NotificationAvatar from '@/components/common/Notification/NotificationAvatar.vue'
import NotificationItem from '@/components/common/Notification/NotificationItem.vue'
import RenderDeviceInUseNotification from '@/components/common/Notification/renders/RenderDeviceInUseNotification.vue'
import RenderMentionNotification from '@/components/common/Notification/renders/RenderMentionNotification.vue'
import RenderProjectInviteNotification from '@/components/common/Notification/renders/RenderProjectInviteNotification.vue'
import RenderReactionNotification from '@/components/common/Notification/renders/RenderReactionNotification.vue'
import RenderReplyNotification from '@/components/common/Notification/renders/RenderReplyNotification.vue'
import RenderTeamInvitationAcceptedNotification from '@/components/common/Notification/renders/RenderTeamInvitationAcceptedNotification.vue'
import RenderTeamInvitationCanceledNotification from '@/components/common/Notification/renders/RenderTeamInvitationCanceledNotification.vue'
import RenderTeamInvitationDeclinedNotification from '@/components/common/Notification/renders/RenderTeamInvitationDeclinedNotification.vue'
import RenderTeamInvitationNotification from '@/components/common/Notification/renders/RenderTeamInvitationNotification.vue'
import RenderTeamJoinRequestNotification from '@/components/common/Notification/renders/RenderTeamJoinRequestNotification.vue'
import RenderTeamRequestApprovedNotification from '@/components/common/Notification/renders/RenderTeamRequestApprovedNotification.vue'
import RenderTeamRequestCanceledNotification from '@/components/common/Notification/renders/RenderTeamRequestCanceledNotification.vue'
import RenderTeamRequestRejectedNotification from '@/components/common/Notification/renders/RenderTeamRequestRejectedNotification.vue'
import RenderThreadReplyNotification from '@/components/common/Notification/renders/RenderThreadReplyNotification.vue'

/** 只画词条和人名（`UserRef` 认 props）：`useI18n` 那几件够了。 */
const TEXT: CatalogNeed[] = ['i18n']

/** 提及 / 回复那两件还带一枚 `v-icon`（预览框前面那个记号），所以多要 Vuetify。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

/** 一件只画 Vuetify 那几样（头像那颗 `v-avatar` / `v-icon`），没有词条要译。 */
const UI: CatalogNeed[] = ['vuetify']

/** 一整行（`NotificationItem`）还要按名字指路（点开落到那件事上），所以多要一条路由。 */
const VIEW_T: CatalogNeed[] = ['vuetify', 'i18n', 'router']

export const NOTIFICATION_ENTRIES: CatalogEntry[] = [
  {
    id: 'render-device-in-use-notification',
    title: 'RenderDeviceInUseNotification',
    about: '一条「谁开始在你这台机器上工作」的动态：哪个项目、哪个房间、哪个队友，以及它能不能看到整台电脑。',
    file: 'src/components/common/Notification/renders/RenderDeviceInUseNotification.vue',
    component: RenderDeviceInUseNotification,
    needs: TEXT,
    states: [
      {
        name: '能访问整台电脑',
        note: '机器的房主最想知道的就这一件：正文尾巴上多一句「能访问整台电脑」。队友名画成 @芝士 那颗 chip（点得动、全项目通用）。',
        props: { notification: deviceInUseNotification(true) },
        expect: '能访问整台电脑',
      },
      {
        name: '只在这一台机器上',
        note: '拿不到整台机器时那句整个不画，正文就剩「项目 · 房间」。少一句话，不是一句「不能访问整台电脑」——这里没有坏消息要说。',
        props: { notification: deviceInUseNotification(false) },
        expect: '课程平台 · 课件评审',
      },
    ],
  },
  {
    id: 'render-mention-notification',
    title: 'RenderMentionNotification',
    about: '一条「有人在讨论里 @ 了你」的动态：谁 @ 的、在哪个讨论里，下面还有被 @ 那句的预览。',
    file: 'src/components/common/Notification/renders/RenderMentionNotification.vue',
    component: RenderMentionNotification,
    needs: UI_T,
    states: [
      {
        name: '认得出是谁 @ 的',
        note: '后端把 `mentioner` 解析出来了：标题里是 @林夏 那颗 chip，正文说在哪条讨论里，下面那格预览是被 @ 的那句话。',
        props: { notification: mentionNotification(true) },
        expect: '有课件的提前发我',
      },
      {
        name: '认不出是谁',
        note: '`mentioner` 没解析出来：标题退回「有人在讨论中提到了你」，讨论名也没有，正文写「未知讨论」，预览那格跟着整个不画。',
        props: { notification: mentionNotification(false) },
        expect: '有人在讨论中提到了你',
      },
      {
        name: '频道里的一条消息 @ 了你',
        note: '带着 `topicId` 的是频道里的 @：标题说谁、在哪个频道，正文是那条消息的开头，点开落在那条消息上。',
        props: { notification: channelMessageNotification('MENTION') },
        expect: '林夏 在「课程安排」提到了你',
      },
    ],
  },
  {
    id: 'render-thread-reply-notification',
    title: 'RenderThreadReplyNotification',
    about: '一条「你参与过的支线里有人回复了」的动态：谁回复的、在哪个频道，下面是那条回复的开头。',
    file: 'src/components/common/Notification/renders/RenderThreadReplyNotification.vue',
    component: RenderThreadReplyNotification,
    needs: TEXT,
    states: [
      {
        name: '支线里的一条回复',
        note: '点开落在支线里的那条回复上（`threadId` + `blockId`）。',
        props: { notification: channelMessageNotification('THREAD_REPLY') },
        expect: '林夏 在「课程安排」的支线里回复了',
      },
    ],
  },
  {
    id: 'render-reaction-notification',
    title: 'RenderReactionNotification',
    about: '一条「有人对你的内容做出了反应」的动态：谁回的、用什么表情回的。',
    file: 'src/components/common/Notification/renders/RenderReactionNotification.vue',
    component: RenderReactionNotification,
    needs: TEXT,
    states: [
      {
        name: '带表情的那一下',
        note: '`reactionEmoji` 给了就用它：正文里写「用 🎉 回应了你的评论」，下面那格再把表情放大画一遍。',
        props: { notification: reactionNotification(true) },
        expect: '🎉',
      },
      {
        name: '认不出是谁回的',
        note: '`reactor` 没解析出来：标题退回「有人对你的内容做出了反应」；表情也没带，退回默认的 👍。',
        props: { notification: reactionNotification(false) },
        expect: '有人对你的内容做出了反应',
      },
    ],
  },
  {
    id: 'render-reply-notification',
    title: 'RenderReplyNotification',
    about: '一条「有人回复了你的评论」的动态：谁回的、回在哪条讨论里，下面是被回复那段话的预览。',
    file: 'src/components/common/Notification/renders/RenderReplyNotification.vue',
    component: RenderReplyNotification,
    needs: UI_T,
    states: [
      {
        name: '认得出是谁回的',
        note: '`replier` 在：标题里是 @林夏 那颗 chip，正文说在哪条讨论里，下面那格预览是回复的开头那句。',
        props: { notification: replyNotification(true) },
        expect: '我按你说的把第二节重写了一遍',
      },
      {
        name: '认不出是谁',
        note: '`replier` 没解析出来：标题退回「有人回复了你的评论」，讨论名也没有，正文写「未知讨论」，预览那格跟着整个不画。',
        props: { notification: replyNotification(false) },
        expect: '有人回复了你的评论',
      },
    ],
  },
  {
    id: 'render-team-invitation-accepted-notification',
    title: 'RenderTeamInvitationAcceptedNotification',
    about: '一条「你发出去的团队邀请被接受了」的动态：谁接受的、进了哪个团队。',
    file: 'src/components/common/Notification/renders/RenderTeamInvitationAcceptedNotification.vue',
    component: RenderTeamInvitationAcceptedNotification,
    needs: TEXT,
    states: [
      {
        name: '认得出是谁接受的',
        note: '`accepter` 和 `team` 都在：标题里是 @林夏 那颗 chip，正文说「接受了加入团队 "数据组" 的邀请」。',
        props: { notification: teamInvitationAcceptedNotification(true) },
        expect: '数据组',
      },
      {
        name: '认不出是谁',
        note: '`accepter` / `team` 都没解析出来：标题退回「你的团队邀请已被接受」，团队名退回「未知团队」。',
        props: { notification: teamInvitationAcceptedNotification(false) },
        expect: '未知团队',
      },
    ],
  },
  {
    id: 'render-team-invitation-canceled-notification',
    title: 'RenderTeamInvitationCanceledNotification',
    about: '一条「发给你的团队邀请被撤回了」的动态：谁撤的、撤的是哪个团队。',
    file: 'src/components/common/Notification/renders/RenderTeamInvitationCanceledNotification.vue',
    component: RenderTeamInvitationCanceledNotification,
    needs: TEXT,
    states: [
      {
        name: '认得出是谁撤的',
        note: '`canceler` 在：正文里是 @林夏 那颗 chip，跟着「取消了邀请你加入团队 "数据组" 的邀请」。标题是固定的那一句。',
        props: { notification: teamInvitationCanceledNotification(true) },
        expect: '数据组',
      },
      {
        name: '认不出是谁',
        note: '`canceler` 没解析出来：正文里撤回的人写成「团队管理员」，团队名写成「未知团队」。',
        props: { notification: teamInvitationCanceledNotification(false) },
        expect: '团队管理员',
      },
    ],
  },
  {
    id: 'render-team-invitation-declined-notification',
    title: 'RenderTeamInvitationDeclinedNotification',
    about: '一条「你发出去的团队邀请被拒绝了」的动态：谁拒绝的、拒了哪个团队，下面还有拒绝的理由。',
    file: 'src/components/common/Notification/renders/RenderTeamInvitationDeclinedNotification.vue',
    component: RenderTeamInvitationDeclinedNotification,
    needs: TEXT,
    states: [
      {
        name: '带了拒绝的理由',
        note: '`decliner` / `team` 都在，`reason` 也带上了：正文说拒绝了哪个团队，下面那格把理由引出来。',
        props: { notification: teamInvitationDeclinedNotification(true) },
        expect: '下个季度再说',
      },
      {
        name: '没说理由',
        note: '`decliner` / `team` 都没解析出来，`reason` 也没有：标题退回「你的团队邀请已被拒绝」，理由那一格整个不画。',
        props: { notification: teamInvitationDeclinedNotification(false) },
        expect: '未知团队',
      },
    ],
  },
  {
    id: 'render-team-request-approved-notification',
    title: 'RenderTeamRequestApprovedNotification',
    about: '一条「你的加入请求批准了」的动态：谁批的、进的是哪个团队。',
    file: 'src/components/common/Notification/renders/RenderTeamRequestApprovedNotification.vue',
    component: RenderTeamRequestApprovedNotification,
    needs: TEXT,
    states: [
      {
        name: '认得出是谁批的',
        note: '`approver` 和 `team` 都在：正文里是 @林夏 那颗 chip，跟着「已批准你加入团队 "数据组" 的请求」。',
        props: { notification: teamRequestApprovedNotification(true) },
        expect: '数据组',
      },
      {
        name: '认不出是谁',
        note: '`approver` / `team` 都没解析出来：正文里批准的人写成「团队管理员」，团队名写成「未知团队」。',
        props: { notification: teamRequestApprovedNotification(false) },
        expect: '团队管理员',
      },
    ],
  },
  {
    id: 'render-team-request-canceled-notification',
    title: 'RenderTeamRequestCanceledNotification',
    about: '一条「你的加入请求被撤回了」的动态：谁撤的、撤的是哪个团队。',
    file: 'src/components/common/Notification/renders/RenderTeamRequestCanceledNotification.vue',
    component: RenderTeamRequestCanceledNotification,
    needs: TEXT,
    states: [
      {
        name: '认得出是谁撤的',
        note: '`canceler` 在：标题里是 @林夏 那颗 chip，「取消了加入请求」，正文说撤的是哪个团队。',
        props: { notification: teamRequestCanceledNotification(true) },
        expect: '数据组',
      },
      {
        name: '认不出是谁',
        note: '`canceler` 没解析出来：标题退回「加入请求已取消」，团队名退回「未知团队」。',
        props: { notification: teamRequestCanceledNotification(false) },
        expect: '加入请求已取消',
      },
    ],
  },
  {
    id: 'render-team-request-rejected-notification',
    title: 'RenderTeamRequestRejectedNotification',
    about: '一条「你的加入请求被拒绝了」的动态：谁拒的、拒的是哪个团队，下面还有拒绝的理由。',
    file: 'src/components/common/Notification/renders/RenderTeamRequestRejectedNotification.vue',
    component: RenderTeamRequestRejectedNotification,
    needs: TEXT,
    states: [
      {
        name: '带了拒绝的理由',
        note: '`rejector` / `team` 都在，`reason` 也带上了：正文说拒绝了哪个团队，下面那格把理由引出来。',
        props: { notification: teamRequestRejectedNotification(true) },
        expect: '这一期名额已经满了',
      },
      {
        name: '没说理由',
        note: '`rejector` / `team` 都没解析出来，`reason` 也没有：正文里拒绝的人写成「团队管理员」，理由那一格整个不画。',
        props: { notification: teamRequestRejectedNotification(false) },
        expect: '团队管理员',
      },
    ],
  },
  // ---- 行里就能办的三类：项目邀请 / 团队邀请 / 加入申请 -----------------------------
  {
    id: 'render-project-invite-notification',
    title: 'RenderProjectInviteNotification',
    about:
      '一条「有人邀请你加入项目」的动态那张脸：谁邀请的、进的是哪个项目。还在等答复时那两颗「接受 / 拒绝」不在这张脸上，由外面那一行（`NotificationItem`）按它交出来的 `actions` 画。',
    file: 'src/components/common/Notification/renders/RenderProjectInviteNotification.vue',
    component: RenderProjectInviteNotification,
    needs: UI_T,
    args: { answering: answeringBridge() },
    states: [
      {
        name: '认得出是谁邀请的',
        note: '`inviter` 和 `project` 都在：标题里是 @林夏 那颗 chip，正文说进的是哪个项目。还在等答复，所以它交出去的去处是「待定邀请」那一页。',
        props: { notification: projectInviteNotification(true) },
        expect: '课件平台',
      },
      {
        name: '认不出是谁',
        note: '`inviter` 没解析出来：标题退回「你收到了项目邀请」，正文只剩项目名。',
        props: { notification: projectInviteNotification(false) },
        expect: '你收到了项目邀请',
      },
      {
        name: '这份邀请已经撤回了',
        note: '后端记下的 `status` 是 `revoked`：正文不再问「要不要加入」，改说撤回，那两颗按钮也不再交出去。',
        props: { notification: projectInviteNotification(true, 'revoked') },
        expect: '邀请已撤回',
      },
    ],
  },
  {
    id: 'render-team-invitation-notification',
    title: 'RenderTeamInvitationNotification',
    about:
      '一条「有人邀请你加入团队」的动态那张脸：谁邀请的、进哪个团队、以什么身份，邀请人写的一句话引在下面。还在等答复时那两颗「接受 / 拒绝」由外面那一行按它交出来的 `actions` 画。',
    file: 'src/components/common/Notification/renders/RenderTeamInvitationNotification.vue',
    component: RenderTeamInvitationNotification,
    needs: UI_T,
    args: { answering: answeringBridge() },
    states: [
      {
        name: '认得出是谁邀请的',
        note: '`inviter` 在：标题里是 @林夏 那颗 chip，正文说以什么身份（这里是 `MEMBER`，写成「普通成员」，不印角色码）进哪个团队。',
        props: { notification: teamInvitationNotification(true) },
        expect: '普通成员',
      },
      {
        name: '认不出是谁',
        note: '`inviter` 没解析出来：标题退回「你收到了团队邀请」，团队名退回「未知团队」。',
        props: { notification: teamInvitationNotification(false) },
        expect: '你收到了团队邀请',
      },
      {
        name: '邀请人附了一句',
        note: '`message` 带上了：正文下面把那句话引出来，带一对引号记号。',
        props: { notification: teamInvitationNotification(true, 'PENDING', '来一起把数据这块做起来。') },
        expect: '来一起把数据这块做起来。',
      },
    ],
  },
  {
    id: 'render-team-join-request-notification',
    title: 'RenderTeamJoinRequestNotification',
    about:
      '一条「有人请求加入团队」的动态那张脸：谁请求的、进哪个团队，申请人写的一句理由引在下面。还在等审批时那两颗「批准 / 拒绝」由外面那一行按它交出来的 `actions` 画。',
    file: 'src/components/common/Notification/renders/RenderTeamJoinRequestNotification.vue',
    component: RenderTeamJoinRequestNotification,
    needs: UI_T,
    args: { answering: answeringBridge() },
    states: [
      {
        name: '认得出是谁申请',
        note: '`requester` 和 `team` 都在：标题里是 @周远 那颗 chip，正文说请求进哪个团队。',
        props: { notification: teamJoinRequestNotification(true) },
        expect: '请求加入团队 "数据组"',
      },
      {
        name: '认不出是谁',
        note: '`requester` 没解析出来：标题退回「有人请求加入团队」，团队名退回「未知团队」。',
        props: { notification: teamJoinRequestNotification(false) },
        expect: '有人请求加入团队',
      },
      {
        name: '申请人写了一句理由',
        note: '`message` 带上了：正文下面把那句理由引出来。',
        props: { notification: teamJoinRequestNotification(true, 'PENDING', '想一起做数据清洗。') },
        expect: '想一起做数据清洗。',
      },
    ],
  },
  // ---- 行本身和它的头像：一张动态在列表里长什么样 --------------------------------
  {
    id: 'notification-item',
    title: 'NotificationItem',
    about:
      '动态列表里的一整行：左边是发来那条动态的人（或这一类的图标），中间是那一条自己的脸（`Render*Notification`）和它属于哪一档，右边是时间。行里就能办的那三类（项目邀请、团队邀请、加入申请）把「接受 / 拒绝」摆在行上；别的条给一颗「标为已读」和「删除」。它自己不问服务端：标已读、删除、以及那三类邀请的答法都从 props 进来。',
    file: 'src/components/common/Notification/NotificationItem.vue',
    component: NotificationItem,
    needs: VIEW_T,
    args: { answering: answeringBridge(), onMarkAsRead: () => {}, onDelete: () => {} },
    states: [
      {
        name: '一条提到你的动态',
        note: '提到你的那一条：左边画发来的人的头像，中间是那条动态的脸（谁、在哪个讨论、被 @ 的那句话），下面两颗通用动作——还算没读，所以「标为已读」在。',
        props: { notification: mentionNotification(true) },
        expect: '标为已读',
      },
      {
        name: '一条项目邀请：整行上就能答',
        note: '还在等答复的项目邀请：这一行把「接受 / 拒绝」直接摆在正文下面，不占用底下那两颗通用动作——点一下这份邀请就答了。',
        props: { notification: projectInviteNotification(true) },
        expect: '接受',
      },
      {
        name: '一条加入申请：整行上就能批',
        note: '还在等人处理的加入申请：这一行把「批准 / 拒绝」摆在正文下面，管理员在这里就把它办了。',
        props: { notification: teamJoinRequestNotification(true) },
        expect: '批准',
      },
      {
        name: '已经在别处答过的团队邀请不再问',
        note: '`answering` 说这条邀请答过了：行上不再有那两颗按钮，回到通用的「标为已读 / 删除」，正文照常说明白是哪个团队。',
        props: {
          notification: teamInvitationNotification(true),
          answering: answeringBridge({ teamInvitationAnswered: () => true }),
        },
        expect: '数据组',
      },
    ],
  },
  {
    id: 'notification-avatar',
    title: 'NotificationAvatar',
    about:
      '一条动态左边那个记号：发来的人有头像就画头像，没挑过就画他的彩色首字母；一个能画的人都没有时，画的才是这一类的图标（配色跟着这条现在是不是还等着处理走）。',
    file: 'src/components/common/Notification/NotificationAvatar.vue',
    component: NotificationAvatar,
    needs: UI,
    states: [
      {
        name: '发来的人画成他的首字母',
        note: '实体里有那一位、他没挑过头像：画的是他名字的彩色首字母，比一格空白或一张所有人的共用脸更认得出是谁。',
        props: { notification: mentionNotification(true) },
        expect: '林',
      },
      {
        name: '认不出是谁：画这一类的图标',
        note: '实体里一个能画的人都没有：退回这一类（这里是一条设备提示）自己的图标和颜色。',
        props: { notification: deviceInUseNotification(false) },
        expectSelector: '.v-icon',
      },
    ],
  },
]
