/**
 * 待办页（`views/InboxView.vue`）在预览站里的两条：上面那半张画面
 * （`views/InboxViewView.vue`）和它下面的「动态」（`views/home/NotificationFeed.vue`）。
 *
 * 这一页以前是一件从头写到尾的 C 级页面：自己取「等你处理」、自己取动态、自己读地址。
 * 拆开之后，两条各自只吃 props、只发事件 —— 清单、有没有项目、读到哪一步、哪一件正在
 * 提交、哪一档动态、有几条新的，全从 props 进；重来、拍板、答复、标已读、往下翻往上发。
 * 取数、`listAwaitingMe` / `NotificationsApi` 和浏览器里那份偏好留在容器与
 * `views/home/useNotificationFeed.ts` 里，所以这里不用起假后端。
 *
 * 动态那一块的行由外面递（`itemComponent`）：行自己会问到后端，写死在动态里它就不是
 * 一件能只收 props 单独画的东西了。这里递一个认得出 id 的替身，够看清单长什么样。
 *
 * 条目和别的分册没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单放一份是因为 `catalog.ts` 已经顶到一千行的上限。这里的 `CatalogEntry`
 * 是 type-only 引用：`catalog.ts` 反过来要 `INBOX_ENTRIES` 这个值，运行时不构成循环。
 */
import type { Component } from 'vue'
import type { Notification } from '@/types/apiNotifications'
import type { WaitingItem } from '@/types/waiting'
import type { CatalogEntry, CatalogNeed } from './catalog'

import { defineComponent, h, markRaw } from 'vue'

import NotificationFeed from '@/views/home/NotificationFeed.vue'
import InboxViewView from '@/views/InboxViewView.vue'

/** 画面里的去处按 name 指路（任务 / 支线 / 频道 / 总览），所以还多要一条路由。 */
const VIEW_T: CatalogNeed[] = ['vuetify', 'i18n', 'router']
/** 动态那一块只有自己的筛选、列表和两种失败态，不指路。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

/** 一件「等你处理」：`types/waiting.ts` 的 `WaitingItem`。 */
function item(over: Partial<WaitingItem> = {}): WaitingItem {
  return {
    projectId: 'p-course',
    projectName: '课程项目',
    topicId: 'c-front',
    topicTitle: '前端',
    taskId: null,
    taskTitle: null,
    taskTitleSource: null,
    phrase: 'awaiting_review',
    reason: 'reviewer',
    at: '2026-10-07T10:00:00Z',
    ...over,
  }
}

/** 分属两个项目的四件，四种「为什么在等你」各来一件。 */
const ITEMS: WaitingItem[] = [
  item({ taskId: 't1', taskTitle: '分享页加访问口令', blockId: 'b-3', phrase: 'awaiting_review' }),
  item({
    projectId: 'p-compiler',
    projectName: '编译器实验',
    topicId: 'c-report',
    topicTitle: '实验报告',
    taskId: 't9',
    taskTitle: '补上优化那一节的图',
    reason: 'reporter',
    phrase: 'delivered',
    detail: '芝士交了一版，等你看过再认。',
  }),
  item({
    reason: 'asked',
    phrase: 'awaiting_answer',
    threadId: 'th-1',
    detail: '口令要不要只发一次？',
    at: '2026-10-07T09:00:00Z',
  }),
  item({ reason: 'read', phrase: 'change_alert', alertId: 8, headline: '报名表单改了' }),
  item({
    reason: 'read',
    phrase: 'change_alert',
    alertId: 9,
    headline: '依赖升级了',
    at: '2026-10-06T10:00:00Z',
  }),
]

const DECIDE = item({
  reason: 'decide',
  phrase: 'decision',
  alertId: 7,
  headline: '上线前要不要先灰度',
  options: ['先灰度', '直接上线'],
})

const FREE_DECIDE = item({
  reason: 'decide',
  phrase: 'decision',
  alertId: 11,
  headline: '报名表单要不要加验证码',
  options: [],
})

/** 动态列表里那一行的替身：认得出是哪一条就够，真行（`NotificationItem`）自己会问到后端。 */
const FeedRow: Component = markRaw(
  defineComponent({
    name: 'FeedRow',
    props: { notification: { type: Object, required: true } },
    setup: (props) => () => h('div', { class: 'feed-row' }, `#${(props.notification as Notification).id}`),
  })
)

/** 一条动态：`types/apiNotifications.ts` 的 `Notification`。 */
function notice(id: number, over: Partial<Notification> = {}): Notification {
  return { id, type: 'MENTION', read: false, createdAt: 0, entities: {}, contextMetadata: {}, ...over }
}

export const INBOX_ENTRIES: CatalogEntry[] = [
  {
    id: 'inbox-view',
    title: 'InboxViewView',
    about:
      '待办页里「等你处理」那一半：按项目分组，每一件先说要你做什么、再说是哪件事、等的是什么；要你拍板的那几件选项就摆在这一行上，芝士写给你的变更提醒读过点一下「标为已读」。一个项目都没有的人看到的是三条起步路。它只收 props、只发事件——读清单、拍板、标已读、清一整个项目、新建项目和用邀请码加入都在容器 `InboxView.vue` 里。',
    file: 'src/views/InboxViewView.vue',
    component: InboxViewView,
    needs: VIEW_T,
    args: {
      noProjects: false,
      loading: false,
      failed: false,
      errorReason: '',
      items: [] as WaitingItem[],
      actionError: '',
      acting: null,
      clearing: null,
    },
    states: [
      {
        name: '一个项目都没有',
        note: '刚退光或删光项目的人落在这儿：不写「这里什么都没有」，三条起步路并列，每条一句话说清进去能得到什么。',
        props: { noProjects: true },
        expect: '用邀请码加入',
      },
      {
        name: '正在读',
        note: '清单还在路上：那一块先摆一枚转着的圈，不留一片空白。',
        props: { loading: true },
        expectSelector: '.inbox__quiet .v-progress-circular',
      },
      {
        name: '这一块没读出来',
        note: '服务端拒了：就地换成那句话和「重试」，而不是装作「暂无等你处理的事项」。',
        props: { failed: true, errorReason: '服务器错误' },
        expect: '服务器错误',
      },
      {
        name: '暂时没有等你处理的事',
        note: '读到了、里面是空的：一句朴素的话，不带语气终止标点。',
        props: {},
        expect: '暂无等你处理的事项',
      },
      {
        name: '分属两个项目的几件事',
        note: '按项目分组，一组一条底边线；每一件写清要你做什么（待你审阅 / 你提的需求已有交付 / 待你回答 / 变更提醒）、是哪件事、等的是什么。任务那一条打开任务页，支线里问的那一条打开支线。',
        props: { items: ITEMS },
        expect: '分享页加访问口令',
      },
      {
        name: '要你拍板的：选项摆在行上',
        note: '带选项的决策请求：一颗选项一个按钮，点哪个就交哪个。',
        props: { items: [DECIDE] },
        expect: '上线前要不要先灰度',
      },
      {
        name: '没带选项的：写一句答复',
        note: '库里还留着以前写进来的、没带选项的决策请求：给一栏能写字的地方，空白时那颗「答复」按不下去——没有这条路，这一件永远清不掉。',
        props: { items: [FREE_DECIDE] },
        expectSelector: '.inbox-item__answer input',
      },
      {
        name: '交不上去',
        note: '拍板那一下后端拒了：这一件留在清单上，上面就地说一句为什么。',
        props: { items: [DECIDE], actionError: '无法提交' },
        expect: '无法提交',
      },
      {
        name: '攒了好几条变更提醒',
        note: '一个项目里不止一条没读的变更提醒时，项目那一行才多一颗「全部标为已读」，正在清的时候那颗按钮转着圈。',
        props: { items: ITEMS, clearing: 'p-course' },
        expect: '全部标为已读',
      },
    ],
  },
  {
    id: 'notification-feed',
    title: 'NotificationFeed',
    about:
      '待办页下面的「动态」：提到你、回复你、邀请你、截止提醒，按事由分三档，读过就点一下。它只收 props、只发事件——问服务端、记着停在哪一档、翻页、有没有新的，全在 `useNotificationFeed.ts` 里，由容器接上。行本身由外面递进来（`itemComponent`）：行自己会问到后端。',
    file: 'src/views/home/NotificationFeed.vue',
    component: NotificationFeed,
    needs: UI_T,
    args: {
      filter: 'mine',
      counts: null,
      notifications: [] as Notification[],
      loading: false,
      failed: false,
      moreFailed: false,
      errorReason: '',
      hasMore: false,
      fresh: 0,
      itemComponent: FeedRow,
    },
    states: [
      {
        name: '三档各带着自己的条数',
        note: '数字是那一档的总数（服务端算的，不是手上这几页数出来的）：有数就写「名字 · 12」，没有就先只写名字。',
        props: { counts: { mine: 3, notice: 7, system: 2 } },
        expect: '需我处理 · 3',
      },
      {
        name: '读过一条之后',
        note: '这一档里还有没读的，标题右边才多一颗「全部标为已读」；都读过了它就不画。',
        props: { counts: { mine: 3, notice: 7, system: 2 }, notifications: [notice(5)] },
        expect: '全部标为已读',
      },
      {
        name: '首次加载还在路上',
        note: '行的形状是定下来的，所以先画一列行骨架，不留一片空白，行到齐也不换高度。',
        props: { loading: true },
        expectSelector: '.notification-feed__skel',
      },
      {
        name: '这一块没读出来',
        note: '就地换成错误和「重试」，而不是留一片空白装作「暂无通知」。',
        props: { failed: true, errorReason: '服务器错误' },
        expect: '服务器错误',
      },
      {
        name: '本来就没有',
        note: '读到了、里面是空的：一句朴素的话。',
        props: {},
        expect: '暂无通知',
      },
      {
        name: '看着列表时来了新的',
        note: '不把正在读的那几行往下挤：顶上说一声「有 2 条新动态」，点了才换成最新的。',
        props: { notifications: [notice(5, { read: true })], fresh: 2 },
        expect: '有 2 条新动态',
      },
      {
        name: '还能往下翻',
        note: '这一页之后还有：列表下面一颗「加载更多」，正在读的时候它转着圈。',
        props: { notifications: [notice(5, { read: true })], hasMore: true },
        expect: '加载更多',
      },
      {
        name: '翻页失败',
        note: '接着往下翻的那一页失败了：到手的那几页一条不扔，错误只接在列表下面，重试接着翻。',
        props: { notifications: [notice(5, { read: true })], moreFailed: true, errorReason: '翻页失败' },
        expect: '翻页失败',
      },
    ],
  },
]
