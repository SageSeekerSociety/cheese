/**
 * 账号与反馈那一册：注册表单底下那两句协议（`LegalLinks`）、反馈列表里的一行
 * （`FeedbackCard`）、第一次采纳之后那张邀请小卡（`InvitePromptCard`）、每个账号说一次的
 * 「知是正在内测」（`BetaNoticeDialog`）、桌面端接入这台设备的对话框（`DeviceConnectDialog`）。
 *
 * 反馈那几行的样例（公开、支持过了、私密、芝士提的、办完了的）在
 * `../catalogFixtures.ts`。
 */
import type { CatalogEntry } from './types'

import { FEEDBACK_ROWS } from '../catalogFixtures'

import LegalLinks from '@/components/account/LegalLinks.vue'
import DeviceConnectDialog from '@/components/desktop/DeviceConnectDialog.vue'
import FeedbackCard from '@/components/feedback/FeedbackCard.vue'
import InvitePromptCard from '@/components/invites/InvitePromptCard.vue'
import BetaNoticeDialog from '@/components/usage/BetaNoticeDialog.vue'

export const ACCOUNT_ENTRIES: CatalogEntry[] = [
  {
    id: 'legal-links',
    title: 'LegalLinks',
    about: '注册表单底下那两句协议：各是一条真链接，各去各的那一页。',
    file: 'src/components/account/LegalLinks.vue',
    component: LegalLinks,
    // 一个字都不吃 props。装路由只为让那两条有 href —— 不装也画得出同样两句话，
    // 只是点不动（`NavLink` 没路由就不给 href）。
    needs: ['router'],
    states: [
      {
        name: '两句',
        note: '各自通到协议页 / 隐私政策页，而且新开一页：在注册表单里点开协议，填了一半的表单不会因此没了。',
        props: {},
        expect: '《用户协议》',
      },
    ],
  },
  {
    id: 'feedback-card',
    title: 'FeedbackCard',
    about: '反馈列表里的一行：正文那一块整块点进详情，支持按钮在链接外面。',
    file: 'src/components/feedback/FeedbackCard.vue',
    component: FeedbackCard,
    // 支持那颗按钮**只发事件**（`support`，带 id），请求由渲染它的页面发 —— 所以这一件
    // 不再需要 pinia 在场，这也正是它自己能单独挂起来的原因。路由是给正文那条链接用的
    // （不装就画成一行点不动的字）。Vuetify 是按钮和图标要的。
    needs: ['vuetify', 'router'],
    states: [
      {
        name: '一行',
        note: '一行一屏里的一行：标题一行、摘要两行封顶，底行是「哪一类、谁提的、多少人参与、走到哪一步」。',
        props: { item: FEEDBACK_ROWS.plain },
        expect: '导出一个月的数据要等四十秒',
      },
      {
        name: '支持过了',
        note: '支持是三个信号一起变（实心图标、数字颜色、底色），不只换颜色 —— 色觉障碍的读者也要看得出自己点没点过；标签挤在底行那一格里。',
        props: { item: FEEDBACK_ROWS.supported },
        expect: '性能',
      },
      {
        name: '摘要和标题是同一句话',
        note: '那就只画标题：把上面那行字再念一遍，占掉 40px 却一个字都没多说。',
        props: { item: FEEDBACK_ROWS.sameLine },
        expect: '导出一个月的数据要等四十秒',
      },
      {
        name: '长标题',
        note: '标题一行就截，摘要两行封顶：列表是用来扫的，一行的高度不能被一条撑开。',
        props: { item: FEEDBACK_ROWS.long },
        expect: '导出一个月的数据要等四十秒，而且导出到一半',
      },
      {
        name: '不能公开的条目',
        note: '私密（或管理员标了安全问题）就没有支持按钮：它不该让人知道它存在，而支持是公开表态。',
        props: { item: FEEDBACK_ROWS.private },
        expect: '私密',
      },
      {
        name: '芝士提的',
        note: '来源那一颗写「Agent 发现」；谁按的发送是另一件事（提案卡那条规矩），这里管不着。',
        props: { item: FEEDBACK_ROWS.agent },
        expect: 'AI 队友发现',
      },
      {
        name: '办完了的',
        note: '已上线的条目支持按钮变灰、提示语换成「这条已经处理完了」——它已经做完了，不该再喊人支持。',
        props: { item: FEEDBACK_ROWS.closed },
        expect: '导出一个月的数据要等四十秒',
      },
    ],
  },
  {
    id: 'invite-prompt-card',
    title: 'InvitePromptCard',
    about: '第一次采纳之后提醒一次「可以邀请好友了」：浮在右下角，不挡刚采纳的那一份。',
    file: 'src/components/invites/InvitePromptCard.vue',
    component: InvitePromptCard,
    // 复制链接、关掉都只发事件，取邀请码和记「提醒过」在 composables/useInvitePrompt.ts。
    // 目录里画在页面里（`inline`），真用时浮在右下角。
    needs: ['vuetify', 'i18n', 'router'],
    states: [
      {
        name: '刚采纳完',
        note: '多了 3 个名额；好友和自己各得多少写成两行。',
        props: { open: true, inline: true, invitesTo: { name: 'UserSettingsInvites' } },
        expect: '复制注册链接',
      },
    ],
  },
  {
    id: 'beta-notice-dialog',
    title: 'BetaNoticeDialog',
    about: '每个账号说一次「知是正在内测」：每样额度多少、怎么得；还没领体验额度的主按钮就是领取。',
    file: 'src/components/usage/BetaNoticeDialog.vue',
    component: BetaNoticeDialog,
    // 领取、关掉都只发事件，取数、领取和记「说过了」在 composables/useBetaNotice.ts。
    needs: ['vuetify', 'i18n'],
    // 一个模态框：画在 body 上。
    teleport: true,
    states: [
      {
        name: '还没领体验额度',
        note: '主按钮是「领取 500 点」，旁边「稍后」。开头那句的每月点数是这个人自己方案的数。',
        props: {
          open: true,
          offer: {
            credits: 500,
            claimed: false,
            feedback_credits: 100,
            plan_credits: 1000,
            invite_credits: 150,
            invitee_credits: 150,
          },
        },
        expect: '领取 500 点',
      },
      {
        name: '领过了',
        note: '只剩「知道了」，体验额度那一行写明已经领过。',
        props: {
          open: true,
          offer: {
            credits: 500,
            claimed: true,
            feedback_credits: 100,
            plan_credits: 1000,
            invite_credits: 150,
            invitee_credits: 150,
          },
        },
        expect: '知道了',
      },
      {
        name: '方案不按月计',
        note: '不限、或者按时间窗口计的方案没有每月的数，开头那句就不写数。',
        props: {
          open: true,
          offer: {
            credits: 500,
            claimed: false,
            feedback_credits: 100,
            plan_credits: null,
            invite_credits: 150,
            invitee_credits: 150,
          },
        },
        expect: '内测体验额度',
      },
    ],
  },
  {
    id: 'device-connect-dialog',
    title: 'DeviceConnectDialog',
    about: '桌面端接入这台设备：选团队、一步步的进度、接好之后 AI 队友要用的程序在后台下到哪了。',
    file: 'src/components/desktop/DeviceConnectDialog.vue',
    component: DeviceConnectDialog,
    // 接入、取消、完成都只发事件；桌面端的调用在 views/desktop/DeviceConnect.vue。
    needs: ['vuetify', 'i18n'],
    // 一个模态框：画在 body 上。
    teleport: true,
    args: {
      open: true,
      stage: 'done',
      steps: ['download', 'approve', 'start'],
      current: null,
      percent: null,
      failure: null,
      mac: true,
      deviceName: '我的 MacBook', // i18n-data: sample device name in a catalog fixture
      teams: [{ id: 7, name: '个人团队', handle: 'alice', personal: true }], // i18n-data: sample team in a catalog fixture
      error: null,
    },
    states: [
      {
        name: '接好了，程序在后台下载',
        note: '对话框不挡人：点「完成」就关，下载在后台继续，进度是一行字和一条细线。',
        props: { prefetch: { state: 'fetching', percent: 42 } },
        expect: '42%',
      },
      {
        name: '接好了，还没开始下',
        note: '还在问平台要哪个版本、或下载还没报进度时，只说在准备，细线来回走。',
        props: { prefetch: { state: 'fetching', percent: null } },
        expect: '正在后台准备',
      },
      {
        name: '接好了，程序已下好',
        note: '下好了说一句，AI 队友第一次在这台设备上工作不用再等它。',
        props: { prefetch: { state: 'ready' } },
        expect: '已下载好',
      },
      {
        name: '接好了，应用不报下载',
        note: '旧版桌面端、或下载失败时不多说：会话第一次运行时自己会下。',
        props: { prefetch: null },
        expect: '我的 MacBook',
      },
    ],
  },
]
