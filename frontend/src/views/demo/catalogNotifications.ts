/**
 * 「通知渲染」那一组在预览站里的条目。
 *
 * 主题的每一条动态都有自己的一张脸：这批 `Render*Notification.vue` 就是那张脸——收一个
 * `Notification`，画出标题、正文（有时还有一句预览或一条理由）。它们以前只能靠整个
 * `NotificationItem`（跟着列表、图标、已读状态一起）才看得见，现在每一件都只吃 props、
 * `defineExpose({ content })`，于是能单独摆在预览站里看。
 *
 * 它们和别的条目没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单独一份是因为 `catalog.ts` 已经顶到一千行的上限 —— 和 `catalogRail.ts`、
 * `catalogModels.ts` 同一个理由。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `NOTIFICATION_ENTRIES`
 * 这个值，运行时不构成循环。数据见 `catalogNotificationsFixtures.ts`。
 *
 * 每件给两格，走的是组件里 `computed` 真会有的岔路：`entities` 里点名的那一位在，与不在。
 * 在时标题里就是这个人（正文那一句也一样）；不在时组件退回「有人……」的匿名标题，
 * 团队名字退回「未知团队」。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  deviceInUseNotification,
  mentionNotification,
  reactionNotification,
  replyNotification,
  teamInvitationAcceptedNotification,
  teamInvitationCanceledNotification,
  teamInvitationDeclinedNotification,
  teamRequestApprovedNotification,
  teamRequestCanceledNotification,
  teamRequestRejectedNotification,
} from './catalogNotificationsFixtures'

import RenderDeviceInUseNotification from '@/components/common/Notification/renders/RenderDeviceInUseNotification.vue'
import RenderMentionNotification from '@/components/common/Notification/renders/RenderMentionNotification.vue'
import RenderReactionNotification from '@/components/common/Notification/renders/RenderReactionNotification.vue'
import RenderReplyNotification from '@/components/common/Notification/renders/RenderReplyNotification.vue'
import RenderTeamInvitationAcceptedNotification from '@/components/common/Notification/renders/RenderTeamInvitationAcceptedNotification.vue'
import RenderTeamInvitationCanceledNotification from '@/components/common/Notification/renders/RenderTeamInvitationCanceledNotification.vue'
import RenderTeamInvitationDeclinedNotification from '@/components/common/Notification/renders/RenderTeamInvitationDeclinedNotification.vue'
import RenderTeamRequestApprovedNotification from '@/components/common/Notification/renders/RenderTeamRequestApprovedNotification.vue'
import RenderTeamRequestCanceledNotification from '@/components/common/Notification/renders/RenderTeamRequestCanceledNotification.vue'
import RenderTeamRequestRejectedNotification from '@/components/common/Notification/renders/RenderTeamRequestRejectedNotification.vue'

/** 只画词条和人名（`UserRef` 认 props）：`useI18n` 那几件够了。 */
const TEXT: CatalogNeed[] = ['i18n']

/** 提及 / 回复那两件还带一枚 `v-icon`（预览框前面那个记号），所以多要 Vuetify。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

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
]
