/**
 * 队列那一串在预览站里的条目。
 *
 * 它们和别的条目没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单独一份是因为 `catalog.ts` 已经八百多行，这些条目塞进去会顶到
 * `frontend/src` 那一千行的上限 —— 和 `catalogRail.ts`、`catalogDashboard.ts` 同一个
 * 理由（那两件在同一个 PR 里从 `AdminDashboardPage` 拆出来）。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `QUEUE_ENTRIES` 这个
 * 值，运行时不构成循环。
 *
 * 为什么这几件值得一站：它们以前是 1309 行的 `AdminQueuePage.vue` 里的几块模板，
 * 想看其中任何一块都得先起假后端、把整页拉起来（还要等那一页的四个请求回来）；拆开
 * 以后每一块只吃 props，于是每一块都能单独摆在预览站里看。数据见
 * `catalogQueueFixtures.ts`。
 *
 * 队列页另外两件（`AdminQueueList` / `AdminQueueTable`）仍然是「从 store 取数」的那
 * 一层，和页面一样得靠假后端才画得出东西，所以不在这里单列。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  QUEUE_DETAIL_ITEM,
  queueAssigneeProps,
  queueDetailProps,
  queueEmptyProps,
  queueHeaderProps,
  queuePageProps,
  queueToolbarProps,
  queueWindowChip,
  UNASSIGNED_FEEDBACK,
} from './catalogQueueFixtures'

import AdminAssigneeSelect from '@/components/admin/AdminAssigneeSelect.vue'
import AdminQueueDetail from '@/components/admin/AdminQueueDetail.vue'
import AdminQueueEmpty from '@/components/admin/queue/AdminQueueEmpty.vue'
import AdminQueueHeader from '@/components/admin/queue/AdminQueueHeader.vue'
import AdminQueueToolbar from '@/components/admin/queue/AdminQueueToolbar.vue'
import AdminQueuePageView from '@/views/admin/AdminQueuePageView.vue'

/** 这些件都吃 vuetify（`v-icon` / `v-btn`）和 i18n（标题、栏位、页签的口径注）。 */
const UI: CatalogNeed[] = ['vuetify', 'i18n']

export const QUEUE_ENTRIES: CatalogEntry[] = [
  {
    id: 'admin-queue-header',
    title: 'AdminQueueHeader',
    about: '队列页头右边那排工具：未读徽标、「标为已读」、刷新，和列表/表格的切换器。',
    file: 'src/components/admin/queue/AdminQueueHeader.vue',
    component: AdminQueueHeader,
    needs: UI,
    states: [
      {
        name: '有未读',
        note: '未读数大于 0 才画徽标和那颗「标为已读」；这个数是页面给的（store 那份 counts），页头不自己去问。',
        props: queueHeaderProps({ unread: 12 }),
        expect: '12 未读',
      },
      {
        name: '没有未读',
        note: '0 的时候徽标和按钮整个不画 —— 「0 未读」是一句噪音。',
        props: queueHeaderProps({ unread: 0 }),
        expect: '列表',
      },
      {
        name: '停在总表',
        note: '切换器只报「人点了哪一档」，换数据不在这条路上：两个视图读的是同一份已到货的列表。',
        props: queueHeaderProps({ unread: 4, view: 'table' }),
        expect: '表格',
      },
    ],
  },
  {
    id: 'admin-queue-toolbar',
    title: 'AdminQueueToolbar',
    about: '队列的工具行：栏位（服务端的问法）、搜索、日期窗口 chip，和只在当前页里筛的状态页签。',
    file: 'src/components/admin/queue/AdminQueueToolbar.vue',
    component: AdminQueueToolbar,
    needs: UI,
    states: [
      {
        name: '公开栏，什么都没筛',
        note: '四个栏位都画出来而不是收进下拉：这一页最贵的一类错是「以为某条反馈不见了，其实它在隔壁那一栏」。',
        props: queueToolbarProps(),
        expect: '公开',
      },
      {
        name: '深链带了一段窗口进来',
        note: '看板 KPI 深链带进来的日期窗口在这里是一颗可清掉的 chip；窗口是服务端的问法，和栏位、搜索归一组。',
        props: queueToolbarProps({
          lane: 'private',
          chips: [queueWindowChip('since', '提交：近 7 天')],
        }),
        expect: '提交：近 7 天',
      },
      {
        name: '状态页签筛着',
        note: '页签和左边那两组刻意长得不一样：它换的是「看哪些（已经拿到的那一页）」，不是「去要哪一批数据」。',
        props: queueToolbarProps({ query: '崩溃', status: 'in_progress', showScopeNote: true }),
        expect: '只筛这一页',
      },
      {
        name: '三段窗口都开着',
        note: '提交、解决、上线各一颗；三颗同现时那一行整段换行，而不是把状态页签挤出容器。',
        props: queueToolbarProps({
          lane: 'security',
          chips: [
            queueWindowChip('since', '提交：近 30 天'),
            queueWindowChip('resolved_since', '解决：近 7 天'),
            queueWindowChip('deployed_since', '上线：近 7 天'),
          ],
          status: 'resolved',
          showScopeNote: true,
        }),
        expect: '上线：近 7 天',
      },
    ],
  },
  {
    id: 'admin-queue-empty',
    title: 'AdminQueueEmpty',
    about: '队列两个列表四态块里的内容：一句话、一句为什么、至多一个动作，和服务端那句原话。',
    file: 'src/components/admin/queue/AdminQueueEmpty.vue',
    component: AdminQueueEmpty,
    needs: UI,
    states: [
      {
        name: '读失败',
        note: '错误态多一样东西：服务端那句原话挂在外面那层的 title 上，鼠标停上去能问出「到底为什么读失败」。',
        props: queueEmptyProps({
          title: '队列加载失败',
          desc: '检查网络后重试。',
          action: '重试',
          tone: 'error',
          raw: 'TypeError: Failed to fetch',
        }),
        expect: '队列加载失败',
      },
      {
        name: '筛空了',
        note: '和「一条都没有」不是一回事：这一档给得出一颗「清除筛选」，因为确实有一条路能退回全量。',
        props: queueEmptyProps({
          title: '没有符合条件的反馈',
          desc: '换一个关键词，或点「全部」清除筛选。',
          action: '清除筛选',
        }),
        expect: '清除筛选',
      },
      {
        name: '一条都没有',
        note: '真的一条都没有时不给按钮：空态没有可点的东西。',
        props: queueEmptyProps(),
        expect: '暂无反馈',
      },
    ],
  },
  {
    id: 'admin-assignee-select',
    title: 'AdminAssigneeSelect',
    about: '分诊面板里的「指派给谁」：候选、当前搜索词、正在搜三样都从 props 进来，输入报 `update:search` 出去。',
    file: 'src/components/admin/AdminAssigneeSelect.vue',
    component: AdminAssigneeSelect,
    needs: UI,
    states: [
      {
        name: '还没指派',
        note: '`modelValue` 为 `null` 就是没指派 —— 空串在这个领域里没有含义，清空报回去的也是 `null`。',
        props: queueAssigneeProps({ modelValue: null, candidates: [] }),
        expect: '指派给',
      },
      {
        name: '已经指派给人',
        note: '候选是服务端按搜索词回的，不是管理员名册：下拉里昵称当标题、handle 当副标题。',
        props: queueAssigneeProps(),
        expect: '指派给',
      },
      {
        name: '正在搜',
        note: '「搜索中…」「没找到这个人」「输入 handle 或昵称」是三句不同的话，由 `searching` 和那串搜索词决定，下拉里只出现一句。',
        props: queueAssigneeProps({ search: 'che', searching: true }),
        expectSelector: '.v-field--loading',
      },
    ],
  },
  {
    id: 'admin-queue-detail',
    title: 'AdminQueueDetail',
    about:
      '一条反馈的详情面：左边身份与分诊面板、右边正文与时间线，底下一根 64px 操作栏。1280 以上自己拆成两栏，以下是一栏。',
    file: 'src/components/admin/AdminQueueDetail.vue',
    component: AdminQueueDetail,
    needs: UI,
    states: [
      {
        name: '一条完整的反馈',
        note: '宽屏那一档和抽屉那一档共用这一份：都是它，差别只在容器（整页接管还是 520px 抽屉）。',
        props: queueDetailProps(),
        expect: '返回',
      },
      {
        name: '分诊面板收起，且还没指派',
        note: '收起时左栏只留一行摘要 —— 小屏上这一块占的是纵向空间；那一行说「未指派」而不是留白。',
        props: queueDetailProps({ item: UNASSIGNED_FEEDBACK, triageOpen: false }),
        expect: '未指派',
      },
      {
        name: '正在读',
        note: '拿不到内容之前先摆骨架（四条骨头、各三行），不是一块空白：这一页从列表点进来几乎必然经过这一档。',
        props: queueDetailProps({ item: null, loading: true }),
        expectSelector: '.qdet__skel',
      },
      {
        name: '读失败',
        note: '「读不到」和「没有这一条」是两件事，各有一对句子；服务端那句原话照旧画出来，不另写一句「操作失败」。',
        props: queueDetailProps({ item: null, error: 'TypeError: Failed to fetch' }),
        expect: 'TypeError: Failed to fetch',
      },
      {
        name: '写操作失败',
        note: '指派 / 改优先级 / 标安全问题 / 加备注失败时，原话挂在分诊面板上方，面板本身照旧可用。',
        props: queueDetailProps({ writeError: '指派失败：没有这个账号' }),
        expect: '指派失败',
      },
    ],
  },
  {
    id: 'admin-queue-page-view',
    title: 'AdminQueuePageView',
    about:
      '队列整屏的版式：详情与队列二选一、工具行、列表/总表两个视图、窄屏抽屉、撤销条。取数与光标全在调用方，这一层只画。',
    file: 'src/views/admin/AdminQueuePageView.vue',
    component: AdminQueuePageView,
    // 窄屏那一档的详情住在 `v-navigation-drawer` 里，抽屉本来就得长在 layout 里。
    layout: true,
    needs: UI,
    states: [
      {
        name: '宽屏，列表视图',
        note: '队列和总表**同时只挂一个**：两个都挂的话同一个 `j` 会被两处 `role="grid"` 各收一次。',
        props: queuePageProps(),
        expect: '公开',
      },
      {
        name: '宽屏，详情整页接管',
        note: '≥1280 时详情不再和队列并排（队列行本身就要 1100px，两个并排塞不下），而是整页接管。',
        props: queuePageProps({ detailOpen: true, detail: QUEUE_DETAIL_ITEM }),
        expectSelector: '.qdet',
      },
      {
        name: '窄屏，详情在抽屉里',
        note: '<1280 时详情是右边那个 520px 的抽屉；两种宽度下都是同一个组件。',
        props: queuePageProps({ isWide: false, detailOpen: true, detail: UNASSIGNED_FEEDBACK }),
        expect: '未指派',
      },
      {
        name: '总表视图',
        note: '切到总表换的是「怎么画已经拿到的那一页」，不换数据 —— 两个视图读的是同一份 `items`。',
        props: queuePageProps({ view: 'table' }),
        expect: 'FB-42',
      },
    ],
  },
]
