/**
 * 组件预览站的目录。
 *
 * 一份注册表，两个使用者：`DemoCatalog.vue` 照它画页面（`/demo/catalog` 是目录，
 * `/demo/catalog/<id>` 是这个组件的那一页），`catalog.spec.ts` 照它逐个挂一遍 ——
 * 「这些组件能单独渲染」于是不是一句声明，是每次跑测试都要重新过一遍的机械结论。
 *
 * 每个条目说的是四件事：**这是什么**（`about`）、**在哪儿**（`file`）、**独立渲染
 * 需要哪几样**（`needs`，见 `CatalogNeed`）、**看哪几格**（`states`，每格的 props
 * 是真会出现的形状，见 `catalogFixtures.ts`）。
 *
 * 加一个组件：这个文件末尾追加一条，把上面四件事写清。别的都不用动。
 */
import type { Component } from 'vue'

import { avatarColor } from '@/utils/avatar'

import { ACCEPT_ENTRIES } from './catalogAccept'
import { ASK_ENTRIES } from './catalogAsk'
import { CHAT_ENTRIES } from './catalogChat'
import { CREDITS_ENTRIES } from './catalogCredits'
import { DASHBOARD_ENTRIES } from './catalogDashboard'
import { DOC_BLOCK_ENTRIES } from './catalogDoc'
import {
  ACCEPT_CARD,
  ACCEPT_DONE,
  ACTION_ROWS,
  ADMIN_QUEUE,
  AGENT_NAME,
  ASK_ANSWERED,
  ASK_OPEN,
  BAR_ROWS,
  BAR_ROWS_LONG,
  CARD_FILED,
  CHANGES_EMPTY,
  changesPanelProps,
  CHEESE_LINES,
  CLOUD_SUPPLY,
  CLOUD_SUPPLY_UNKNOWN,
  COMPUTE_DEVICES,
  docPanelProps,
  docSession,
  EXCERPTS,
  FEEDBACK_ROWS,
  KPI_STATES,
  LONG_ROW,
  NAV_ITEMS,
  NUMBER_ROWS,
  OPEN_FILES,
  PREVIEW_EMPTY,
  previewPanelProps,
  roomMessageProps,
  roomNoticeProps,
  SHEET_ACTIONS,
  TURN_SUMMARY,
  WANG_CLOSING,
  WANG_LINES,
} from './catalogFixtures'
import { KNOWLEDGE_ENTRIES } from './catalogKnowledge'
import { MODELS_ENTRIES } from './catalogModels'
import { QUEUE_ENTRIES } from './catalogQueue'
import { RAIL_ENTRIES } from './catalogRail'
import { ROOM_ENTRIES } from './catalogRoom'
import { SETTINGS_ENTRIES } from './catalogSettings'
import { TASK_FORM_ENTRIES } from './catalogTaskForm'
import { USAGE_ENTRIES } from './catalogUsage'

import LegalLinks from '@/components/account/LegalLinks.vue'
import AdminActionList from '@/components/admin/AdminActionList.vue'
import AdminBarChart from '@/components/admin/AdminBarChart.vue'
import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminNumberList from '@/components/admin/AdminNumberList.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import AppPage from '@/components/common/AppPage.vue'
import MobileActionSheet from '@/components/common/MobileActionSheet.vue'
import BottomAppBar from '@/components/common/Navigation/BottomAppBar.vue'
import NavLink from '@/components/common/NavLink.vue'
import UserRef from '@/components/common/UserRef.vue'
import ComputeChoiceForm from '@/components/ComputeChoiceForm.vue'
import FeedbackCard from '@/components/feedback/FeedbackCard.vue'
import PanelChangesView from '@/components/panels/PanelChangesView.vue'
import PanelDocView from '@/components/panels/PanelDocView.vue'
import PanelPreviewView from '@/components/panels/PanelPreviewView.vue'
import { panelTabs } from '@/components/panels/panelTabList'
import PanelTabs from '@/components/panels/PanelTabs.vue'
import RoomMessage from '@/components/room/RoomMessage.vue'
import RoomNotice from '@/components/room/RoomNotice.vue'
import TopicAcceptCard from '@/components/TopicAcceptCard.vue'
import AnalyticsNavigationTabs from '@/views/spaces/detail/analytics/components/AnalyticsNavigationTabs.vue'
import LearningQuoteItem from '@/views/spaces/detail/analytics/components/LearningQuoteItem.vue'

/**
 * 这一件要在什么环境里才长得对。
 *
 * 记下来是有用的：目录页把这几颗画成标签（「它需要什么」是读的人第一个问题），
 * 测试照着它装 —— 只装这一件真正要的那几样。所以「能单独渲染」在预览站里是一条
 * 可执行的断言，而不是注释里的一句话。
 *
 * 一条都不写的（`needs: []`）是真的一件都不要：`UserRef` 只吃 props，`NavLink`
 * 没有路由就退化成一行静态的字。
 */
export type CatalogNeed = 'vuetify' | 'i18n' | 'router' | 'pinia'

export interface CatalogState {
  /** 这一格的名字（页面上那张小标题）。 */
  name: string
  /** 这一格在讲什么，一句话。 */
  note: string
  props: Record<string, unknown>
  /** 默认插槽里的那段字（`NavLink`、`AppPage` 这些靠插槽才有内容）。 */
  slot?: string
  /** 这一格的环境，不写就跟条目走。 */
  needs?: CatalogNeed[]
  /** 画出来之后该看得见的一句话（测试按它判「真的画出来了」）。骨架屏那类没有。 */
  expect?: string
}

export interface CatalogEntry {
  /** 地址里那一段：`/demo/catalog/<id>`。 */
  id: string
  title: string
  about: string
  /** 源码路径，相对 `frontend/`。 */
  file: string
  component: Component
  needs: CatalogNeed[]
  /** 要坐在 Vuetify 布局里的（底栏、底部动作面板本来就长在 layout 里）。 */
  layout?: boolean
  /** 画出来的东西不在容器里（浮层传送到 body）。 */
  teleport?: boolean
  states: CatalogState[]
}

/** 工作面板那几格（产品那张表，见 `panelTabList`），预览站不重复写一份。 */
const TABS = panelTabs(false).map((tab) => ({ key: String(tab.key), label: tab.label, icon: tab.icon }))

const UI: CatalogNeed[] = ['vuetify']

export const CATALOG: CatalogEntry[] = [
  {
    id: 'user-ref',
    title: 'UserRef',
    about: '句子里提到的一个人：@名字。给了去处才可点。',
    file: 'src/components/common/UserRef.vue',
    component: UserRef,
    needs: [],
    states: [
      {
        name: '有去处',
        note: '给了 to 才是一颗「人可以点」的 chip：进 Tab 顺序，按一下 emit navigate。',
        props: { handle: 'alice', name: '爱丽丝', to: { path: '/projects/p1/members/alice' } },
        expect: '@爱丽丝',
      },
      {
        name: '只知道名字',
        note: '没有 handle 就没有去处（找不到那个人的主页）：照样画 @名字，但不可点。',
        props: { name: '新来的同学' },
        expect: '@新来的同学',
      },
      {
        name: '长名字',
        note: '名字长到换行也是这一颗，不缩字号、不截断。',
        props: {
          handle: 'someone-with-a-long-handle',
          name: '一位名字很长的同学（校外）',
          to: { path: '/projects/p1/members/x' },
        },
        expect: '@一位名字很长的同学（校外）',
      },
    ],
  },
  {
    id: 'nav-link',
    title: 'NavLink',
    about: '产品里唯一「点了去哪」的那颗字：有路由是一条链接，没有路由是一行静态的字。',
    file: 'src/components/common/NavLink.vue',
    component: NavLink,
    needs: ['router'],
    states: [
      {
        name: '有路由',
        note: '装了路由：画成 <a href>，点一下 push（Cmd/中键点还是新开一页，浏览器说了算）。',
        props: { to: { path: '/projects/p1/settings' } },
        slot: '项目设置',
        expect: '项目设置',
      },
      {
        name: '没有路由',
        note: '不装路由：一样画一个字，但没有 href、进不了 Tab 顺序 —— 同一个组件，两种环境都挂得起来。',
        props: { to: { path: '/projects/p1/settings' } },
        slot: '项目设置',
        needs: [],
        expect: '项目设置',
      },
      {
        name: '去不成的去处',
        note: '目标的名字在、必需参数不在（`resolve` 会抛）：这里什么都不画成链接 —— 不是死代码，是真会走到的退路。',
        props: { to: { name: 'workspace-running', params: {} } },
        slot: '去看看',
        expect: '去看看',
      },
    ],
  },
  {
    id: 'room-message',
    title: 'RoomMessage',
    about: '对话栏里的一条消息：人说的、芝士说的、步骤清单、自己发的。',
    file: 'src/components/room/RoomMessage.vue',
    component: RoomMessage,
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '提问：还没答（选择后提交）',
        note: '选项先保存在草稿里，明确提交后才作答。',
        props: roomMessageProps(ASK_OPEN, {
          viewer: 'wang',
          askState: {
            draft: { kind: null, option: '', note: '', later: false },
            pending: null,
            editing: false,
            busy: false,
            fresh: true,
            saved: false,
            error: null,
            conflict: false,
            storageBlocked: false,
          },
        }),
        expect: '课程平台收文件',
      },
      {
        name: '提问：已经有人答了（回执）',
        note: '显示真实答案日志，执行者是否接续仍需回执确认。',
        props: roomMessageProps(ASK_ANSWERED),
        expect: '课程平台收文件',
      },
      {
        name: '留言（没有交给芝士）',
        note: '名字、头像、时间都带上的第一条（runStart）。',
        props: roomMessageProps(WANG_LINES[0]),
        expect: '先记一下：这个项目以后放课程资料',
      },
      {
        name: '点了名交给芝士',
        note: '<@handle> 渲染成可点的 chip，靠 refs 那张 handle→昵称的表。',
        props: roomMessageProps(WANG_LINES[1]),
        expect: '@芝士',
      },
      {
        name: '引用幻灯片页',
        note: '问题正文单独显示，资料可以展开核对原文与版本；资料中的 @ 名字照原文显示。',
        props: roomMessageProps(WANG_LINES[1], {
          block: {
            ...WANG_LINES[1].block,
            content: '<@cheese> 解释这一页',
            meta: {
              quoted_context: {
                kind: 'slide-page',
                path: 'slides/@评审 <@cheese-other>.pptx ',
                source: 'committed',
                version: 'v7',
                task_id: null,
                page: 2,
                text: '  @评审 <@cheese-other>\n本页保留原始空白和正文。\n',
              },
            },
          },
        }),
        expect: '引用第 2 页文字',
      },
      {
        name: '芝士的正文',
        note: '队友这一条挂的是它的身份（isAgent），头像按 handle 配色块。',
        props: roomMessageProps(CHEESE_LINES[0]),
        expect: '写完递验收卡给你',
      },
      ...(
        [
          ['think', '在想', '思考中', ' · 已用 6 秒'],
          ['work', '在干活', '正在运行命令', ' · 已用 41 秒'],
          ['stuck', '卡住了', '重试中（第 2 次）', ' · 已用 1 分 12 秒'],
          ['done', '做完了', null, ''],
        ] as const
      ).map(([face, label, faceStatus, elapsed]) => ({
        name: `芝士的头像：${label}`,
        note: '队友在干活时，对话里它最近出现的那个头像跟着状态动；在动时点它去现场。',
        props: roomMessageProps(CHEESE_LINES[0], {
          face,
          faceLabel: faceStatus && faceStatus + elapsed,
          faceStatus,
        }),
        expect: '写完递验收卡给你',
      })),
      {
        name: '步骤清单',
        note: '同一个块，正文换成清单：三项都打勾，末尾跟一句结果。',
        props: roomMessageProps(CHEESE_LINES[1]),
        expect: '读一下项目现有文件',
      },
      {
        name: '长正文',
        note: '一整段交代写进一条消息里：正文自己换行、自己撑高，行里别的格子不动。',
        props: roomMessageProps(LONG_ROW),
        expect: '我把这周的进度理了一遍',
      },
      {
        name: '自己发的：没送出去',
        note: '还没落库的那一条淡一档，形状不变；送失败时带「重试」和「编辑」。',
        props: roomMessageProps(WANG_CLOSING, { mine: true, outgoing: { failed: true } }),
        expect: '好了，谢谢',
      },
    ],
  },
  {
    id: 'room-notice',
    title: 'RoomNotice',
    about: '平台在房间里说的话：本轮摘要、验收卡的动静、谁采纳了。',
    file: 'src/components/room/RoomNotice.vue',
    component: RoomNotice,
    // 「去确认」那颗按钮是一条真链接（`NavLink`），所以路由还是要装：不装也画得出来，
    // 但那一颗会退化成一行点不动的字。Vuetify 是它自己那几颗图标要的。
    needs: ['vuetify', 'router'],
    states: [
      {
        name: '本轮摘要',
        note: '一轮里的动作行折成一行，改动摘要挂在后面（「改了 1 个文件（+6 -0）」）。',
        props: roomNoticeProps(TURN_SUMMARY),
        expect: '改动了 1 个文件',
      },
      {
        name: '验收卡已递出',
        note: '一条平台事件：写清是谁的改动、待谁审阅。',
        props: roomNoticeProps(CARD_FILED),
        expect: '待 王长鑫 审阅',
      },
      {
        name: '已采纳',
        note: '有人采纳、PR 合并：同一个组件，换的是事件本身。',
        props: roomNoticeProps(ACCEPT_DONE),
        expect: '采纳了这次改动',
      },
    ],
  },
  {
    id: 'accept-card',
    title: 'TopicAcceptCard',
    about: '输入框上方那张验收卡：平时一行横条，点开是整张卡。',
    file: 'src/components/TopicAcceptCard.vue',
    component: TopicAcceptCard,
    // 这张卡自己去接口取数（`getAcceptCards` / `getPrChecks`），也读工作区 store。
    needs: ['vuetify', 'pinia', 'router', 'i18n'],
    states: [
      {
        name: '贴在输入框上方',
        note: '收着的一行：待谁审阅、改动的标题。点一下才摊开（这里不点，看的就是这一行）。',
        // 卡上带着哪一条活的 id：不传就只看「不属于任何一条活」的那些卡（见组件里那个 filter）。
        props: { topicId: 'demo', topicStatus: 'active', taskId: ACCEPT_CARD?.task_id, docked: true },
        expect: 'docs: add a welcome note',
      },
      {
        name: '整张摊开',
        note: '不贴底的时候（任务卡详情里）整张摊开：改动说明、检查、采纳与退回都在。',
        props: { topicId: 'demo', topicStatus: 'active', taskId: ACCEPT_CARD?.task_id, docked: false },
        expect: '采纳',
      },
    ],
  },
  {
    id: 'panel-tabs',
    title: 'PanelTabs',
    about: '工作面板的页签条：四格固定页签，外加读者自己打开的那几份文件。',
    file: 'src/components/panels/PanelTabs.vue',
    component: PanelTabs,
    needs: UI,
    states: [
      {
        name: '四格',
        note: '哪四格、叫什么、挂哪个图标来自产品那张表（panelTabList），这里不另写一份。',
        props: { tabs: TABS, active: 'site' },
        expect: '现场',
      },
      {
        name: '有一格是空的',
        note: '这一格此刻没东西：字退到 --faint，但照样能点。',
        props: {
          tabs: TABS.map((tab) =>
            tab.key === 'changes'
              ? { ...tab, title: '改动（还没有改动）', empty: true }
              : tab.key === 'preview'
                ? { ...tab, title: '预览（README.md）', signal: { kind: 'dot' } }
                : tab
          ),
          active: 'preview',
        },
        expect: '改动',
      },
      {
        name: '有东西等你看',
        note: '信号三档：现场在跑是呼吸点、改动是数字、预览是新内容。',
        props: {
          tabs: TABS.map((tab) =>
            tab.key === 'site'
              ? { ...tab, signal: { kind: 'pulse' } }
              : tab.key === 'changes'
                ? { ...tab, signal: { kind: 'count', count: 3 } }
                : tab
          ),
          active: 'changes',
        },
        expect: '3',
      },
      {
        name: '打开了一份文件',
        note: '自由区：读者自己打开的那几份文件各占一格，可以关、可以钉住。',
        props: { tabs: TABS, active: 'file:README.md', files: OPEN_FILES },
        expect: 'README.md',
      },
      {
        name: '手机上',
        note: '窄屏上这条栏加高、横向滚动，阶段自动选中的那一格会被带回视野里。',
        props: { tabs: TABS, active: 'site', phone: true },
        expect: '总览',
      },
    ],
  },
  {
    id: 'admin-kpi-card',
    title: 'AdminKpiCard',
    about: '看板 KPI 行里的一张卡：有没有 to 决定它是不是一个可点的东西。',
    file: 'src/components/admin/AdminKpiCard.vue',
    component: AdminKpiCard,
    // 口径注那一颗（AdminNoteTip）用 useI18n。
    needs: ['vuetify', 'i18n', 'router'],
    states: [
      {
        name: '可点的一张',
        note: '有去向：指针、hover 底色、进 Tab 顺序三样都有，还带着环比和逐日折线。',
        props: KPI_STATES.linked,
        expect: '12,048',
      },
      {
        name: '只看不点的一张',
        note: '没有去向：cursor / hover / Tab 三样一样都不给；口径注（note）这时才生效。',
        props: KPI_STATES.plain,
        expect: '316',
      },
      {
        name: '拿不到值',
        note: 'value 是空串画长破折号，不画 0：「没读到」和「读出来了，是零」必须长得不一样。',
        props: KPI_STATES.empty,
        expect: '—',
      },
      {
        name: '首次加载',
        note: '骨架的形状和真卡完全一样：到货那一刻不重排。',
        props: { label: '待分诊', value: '', loading: true, spark: [] },
        needs: UI,
      },
    ],
  },
  {
    id: 'admin-bar-chart',
    title: 'AdminBarChart',
    about: '看板上的横向排行：名字在左、条在中间、数值在右。',
    file: 'src/components/admin/AdminBarChart.vue',
    component: AdminBarChart,
    needs: ['vuetify', 'i18n', 'router'],
    states: [
      {
        name: '一张榜',
        note: '每一行本身就是文字（名字和数值都是真文本，条是装饰），读屏直接读得到。',
        props: { title: '最花 token 的项目', rows: BAR_ROWS },
        expect: '空间协作',
      },
      {
        name: '名字长到装不下',
        note: '名字那一格走省略号、挂 title，数值那两列不被挤走。',
        props: { title: '最花 token 的项目', rows: BAR_ROWS_LONG },
        expect: '一个名字长到会走省略号的项目',
      },
      {
        name: '没有数据',
        note: '空态是「还没读到」，不是画一根 0 的柱。',
        props: { title: '最花 token 的项目', rows: [] },
      },
      {
        name: '首次加载',
        note: '骨架行，行高和真行一样。',
        props: { title: '最花 token 的项目', rows: [], loading: true },
        needs: ['vuetify', 'i18n'],
      },
    ],
  },
  {
    id: 'admin-number-list',
    title: 'AdminNumberList',
    about: '看板右侧的迷你列表：整行一个 <a>，行里没有第二个可聚焦的东西。',
    file: 'src/components/admin/AdminNumberList.vue',
    component: AdminNumberList,
    needs: ['vuetify', 'i18n', 'router'],
    states: [
      {
        name: '三行',
        note: '状态名和颜色各只有一处定义（feedbackMeta），这里只画。',
        props: { title: '需处理 · 3', rows: NUMBER_ROWS, moreTo: ADMIN_QUEUE },
        expect: '导出一个月的数据要等四十秒',
      },
      {
        name: '空',
        note: '没有行也没有「查看全部」：这一格就是没东西。',
        props: { title: '需处理 · 0', rows: [] },
      },
      {
        name: '首次加载',
        note: '骨架行，和真行的行高一样（28px 那条行高是这一块立得住的前提）。',
        props: { title: '需处理 · 3', rows: [], loading: true },
        needs: ['vuetify', 'i18n'],
      },
    ],
  },
  {
    id: 'admin-action-list',
    title: 'AdminActionList',
    about: '「等你处理 / 卡住了」那一列：有去向的行整行是目的地，没有的就不装成链接。',
    file: 'src/components/admin/AdminActionList.vue',
    component: AdminActionList,
    needs: ['vuetify', 'i18n', 'router'],
    states: [
      {
        name: '三条',
        note: 'tone 只有 warn / danger 用状态色，其余走中性阶；第二行没有去向，是静态的。',
        props: { title: '等你处理', empty: '今天没有卡住的事', rows: ACTION_ROWS, moreTo: ADMIN_QUEUE },
        expect: '合并 #2100（前端边界闸）',
      },
      {
        name: '空屏',
        note: '空态是一句邀请（「今天没有卡住的事」），不是一句道歉。',
        props: { title: '等你处理', empty: '今天没有卡住的事', rows: [] },
        expect: '今天没有卡住的事',
      },
      {
        name: '首次加载',
        note: '骨架行。',
        props: { title: '等你处理', empty: '今天没有卡住的事', rows: [], loading: true },
        needs: ['vuetify', 'i18n'],
      },
    ],
  },
  {
    id: 'app-page',
    title: 'AppPage',
    about: '一页的外形：一条页头 + 下面唯一会滚的正文。',
    file: 'src/components/common/AppPage.vue',
    component: AppPage,
    needs: ['vuetify', 'router'],
    states: [
      {
        name: '一页',
        note: '页头写的是「这一页是什么」；项目名在侧栏上，这里不写第二遍。',
        props: { title: '项目设置' },
        slot: '正文从这里开始，页头之外只有这一块会滚。',
        expect: '项目设置',
      },
      {
        name: '另一页里的一项',
        note: '给了 parent 的页头写成「成员 / 名字」，前一段点得回去。',
        props: { title: '爱丽丝', parent: { label: '成员', to: { path: '/projects/p1/members' } } },
        slot: '这个人参加过的题、交过的作业。',
        expect: '成员',
      },
      {
        name: '多列的工作面',
        note: 'width="full" 给看板那类要摊开的面（read 是读和填表的那一栏）。',
        props: { title: '数据看板', width: 'full' },
        slot: '四列的工作面。',
        expect: '数据看板',
      },
    ],
  },
  {
    id: 'mobile-action-sheet',
    title: 'MobileActionSheet',
    about: '手机上一组操作从屏幕底部升起来。',
    file: 'src/components/common/MobileActionSheet.vue',
    component: MobileActionSheet,
    needs: ['vuetify', 'router'],
    layout: true,
    teleport: true,
    states: [
      {
        name: '升起来的一组',
        note: '危险操作是 --danger-ink；禁用和转圈的行点不动；有 to 的选完就换页。',
        props: { modelValue: true, title: '话题', actions: SHEET_ACTIONS },
        expect: '删除话题',
      },
      {
        name: '带一段自制抬头',
        note: '默认抬头不够用时用 #header 插槽（一排表情就是这么放进去的）。',
        props: { modelValue: true, title: '话题', actions: SHEET_ACTIONS.slice(0, 2) },
        slot: '一排表情从这里开始',
        expect: '重命名',
      },
    ],
  },
  {
    id: 'bottom-app-bar',
    title: 'BottomAppBar',
    about: '手机上那一排底栏：三格，带角标。',
    file: 'src/components/common/Navigation/BottomAppBar.vue',
    component: BottomAppBar,
    needs: ['vuetify', 'router'],
    layout: true,
    states: [
      {
        name: '三格',
        note: '哪一格亮着由调用方说了算（match 那一条是给「一格底下住着好几条路由」准备的）。',
        props: { items: NAV_ITEMS },
        expect: '待办',
      },
      {
        name: '有一颗只做事、不换页',
        note: '没有 to 的格子（「＋新建项目」那种）点了跑 action，不换页；角标超过 99 就写 99+，读屏从 aria-label 里听到件数。',
        props: {
          items: [
            ...NAV_ITEMS.slice(0, 2).map((item) => ({ ...item, badge: item.key === 'inbox' ? 128 : undefined })),
            { key: 'new', type: 'item', title: '新建项目', icon: 'mdi-plus', action: () => {} },
          ],
        },
        expect: '新建项目',
      },
    ],
  },
  {
    id: 'analytics-tabs',
    title: 'AnalyticsNavigationTabs',
    about: '数据看板那六格：跳哪儿由挂着它的那棵树说了算。',
    file: 'src/views/spaces/detail/analytics/components/AnalyticsNavigationTabs.vue',
    component: AnalyticsNavigationTabs,
    needs: ['vuetify', 'i18n', 'router'],
    states: [
      {
        name: '在空间里',
        note: '路由里有 :spaceId：六格都带着它（换一格不丢当前那一串筛选条件）。',
        props: {},
        expect: '参与者',
      },
      {
        name: '没有路由',
        note: '不装路由也照样画六格（每一格只是一颗 v-tab）——组件不因为缺路由就消失。',
        props: {},
        needs: ['vuetify', 'i18n'],
        expect: '学习',
      },
    ],
  },
  {
    id: 'learning-quote',
    title: 'LearningQuoteItem',
    about: '学习者说的一句话：引文、名字、知识点、时间，和「查看原文」。',
    file: 'src/views/spaces/detail/analytics/components/LearningQuoteItem.vue',
    component: LearningQuoteItem,
    needs: ['vuetify', 'i18n', 'router'],
    states: [
      {
        name: '一条摘录',
        note: '「查看原文」带着这条发言的位置去那个话题（buildSourceLink，走 useNavigation）。',
        props: { excerpt: EXCERPTS[0] },
        expect: '因为瑞利散射',
      },
      {
        name: '长引用',
        note: '引文长到好几行：正文自己撑高，右下那颗按钮不掉下去。',
        props: { excerpt: EXCERPTS[1] },
        expect: '至少需要多少',
      },
      {
        name: '批量挑选时',
        note: 'selectable 才画前面那颗勾选框（队列页要一批一批挑）。',
        props: { excerpt: EXCERPTS[0], selectable: true },
        expect: '查看原文',
      },
    ],
  },
  // 侧栏那一组（TopicSidebar 拆出来的那六件）在自己的文件里：`catalogRail.ts`。
  ...RAIL_ENTRIES,
  // 看板那八件（从 2880 行的 AdminDashboardPage 拆出来的七屏 + 页头）在自己的文件里：
  // `catalogDashboard.ts`（数据在 `catalogDashboardFixtures.ts`）。
  ...DASHBOARD_ENTRIES,
  // 队列那三件（从 1309 行的 AdminQueuePage 拆出来的页头、工具行、四态块）在自己的
  // 文件里：`catalogQueue.ts`（数据在 `catalogQueueFixtures.ts`）。
  ...QUEUE_ENTRIES,
  // 聊天面板那一组（ChatPanel 拆出来的那四件）在自己的文件里：`catalogChat.ts`。
  ...CHAT_ENTRIES,
  // 模型管理那六件（从 1428 行的 AdminModelsPage 拆出来的三段 + 页头 + 那条横条 +
  // 确认框）在自己的文件里：`catalogModels.ts`（数据在 `catalogModelsFixtures.ts`）。
  ...MODELS_ENTRIES,
  // 方案与额度那五件在自己的文件里：`catalogCredits.ts`。
  ...CREDITS_ENTRIES,
  ...USAGE_ENTRIES,
  // 验收卡那一组（从 1215 行的 TopicAcceptCard 拆出来的八件，数据在
  // `catalogFixtures.ts`）在自己的文件里：`catalogAccept.ts`。
  ...ACCEPT_ENTRIES,
  // 输入区那一组（从 1039 行的 RoomComposer 拆出来的三件，数据就在那份里）在 `catalogRoom.ts`。
  ...ROOM_ENTRIES,
  // 知识库那六件（从 1508 行的 Knowledge.vue 拆出来的四块模板 + 两个对话框）在自己的
  // 文件里：`catalogKnowledge.ts`（数据在 `catalogKnowledgeFixtures.ts`）。
  ...KNOWLEDGE_ENTRIES,
  // 发题表单那一组（从 1089 行的 TaskForm 拆出来的七张卡加两个弹窗，夹具在自己
  // 那一份里）在自己的文件里：`catalogTaskForm.ts`。
  ...TASK_FORM_ENTRIES,
  // 项目设置那一组（从 1041 行的 ProjectSettingsView 拆出来的六块）在自己的文件里：
  // `catalogSettings.ts`（数据在 `catalogSettingsFixtures.ts`）。
  ...SETTINGS_ENTRIES,
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
    // 支持那颗按钮读反馈 store（`toggleSupport`），所以 store 要在场；路由是给正文
    // 那条链接用的（不装就画成一行点不动的字）。Vuetify 是按钮和图标要的。
    needs: ['vuetify', 'pinia', 'router'],
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
        expect: 'Agent 发现',
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
    id: 'panel-changes',
    title: 'PanelChangesView',
    about: '改动那一格：一棵标着增删的树，点开是这一份文件自己的逐行 diff。',
    file: 'src/components/panels/PanelChangesView.vue',
    component: PanelChangesView,
    needs: UI,
    states: [
      {
        name: '读改动的时候',
        note: '转圈，不是骨架：这一格等的东西说不准是一份 diff、一个编辑器、一张图，还是「只读 / 二进制」那一句提示 —— 等的是什么形状，它并不知道。',
        props: changesPanelProps({
          loading: true,
          openPath: null,
          openDiff: null,
          openDiffLines: [],
          fileToolReady: false,
        }),
      },
      {
        name: '这一轮什么都没改',
        note: '树上写「暂无改动」，右边那一半装的是提交记录 —— 它也没有，于是写「暂无提交」。',
        props: CHANGES_EMPTY,
        expect: '暂无提交',
      },
      {
        name: '一份文件自己的 diff',
        note: '树上每行一个文件（+N −M 标着改了多少），点开的是它自己那一段：文件头、hunk 头、增删各自着色，定位得到行。',
        props: changesPanelProps(),
        expect: '这个项目放本课程的课件和作业',
      },
      {
        name: '保存冲突',
        note: '你编辑期间芝士又改了同一份文件：两个版本都留着，由人按一下决定谁赢 —— 静默替人选一个，就是改动消失的方式。',
        props: changesPanelProps({ fileConflict: true, fileDirty: true }),
        expect: '你编辑期间，这个文件已被修改',
      },
      {
        name: '没绑仓库',
        note: '这个项目没有代码仓库：一句话说清，不画一棵空树。',
        props: changesPanelProps({ noRepo: true }),
        expect: '暂无代码仓库',
      },
      {
        name: '读不到改动',
        note: '取 diff 失败：说清失败的是什么，还留着「回到已提交版本」这条出路。',
        props: changesPanelProps({ errorMsg: '拉取改动失败：请求超时', fileSource: 'live' }),
        expect: '拉取改动失败',
      },
    ],
  },
  {
    id: 'panel-preview',
    title: 'PanelPreviewView',
    about: '预览那一格：芝士最后摆出来的那一样 —— 一篇文档、一张表、一个跑着的应用，或者一句「还没有」。',
    file: 'src/components/panels/PanelPreviewView.vue',
    component: PanelPreviewView,
    needs: UI,
    states: [
      {
        name: '读预览的时候',
        note: '转圈，不是骨架：底下那一格可能是网页、文档、表格、图片，等完才知道是哪一样。',
        props: previewPanelProps({ loading: true }),
      },
      {
        name: '还没有东西可看',
        note: '房间里还没摆出过任何东西：一句话，不是一块空白。',
        props: PREVIEW_EMPTY,
        expect: '暂无预览',
      },
      {
        name: '一篇 markdown',
        note: '正文直接画出来（和聊天用的是同一个渲染器）：标题、清单都在，顶上是文件名和类型。',
        props: previewPanelProps(),
        expect: '课件和作业都在这里',
      },
      {
        name: '这一份读不到了',
        note: '文件还在清单上、字节取不到：说的是「无法读取文件」，和「预览加载失败」不是同一件事。',
        props: previewPanelProps({
          previewFile: null,
          documentType: null,
          documentName: '',
          previewMime: '',
          previewNamed: false,
          previewReadError: '这个文件已经不在了',
        }),
        expect: '无法读取文件',
      },
      {
        name: '跑着的应用断了',
        note: '机器上没在跑，还是跑了但应用自己没了 —— 两句不同的话。这里给的是第一句。',
        props: previewPanelProps({
          previewFile: null,
          documentType: null,
          documentName: '',
          previewMime: '',
          previewNamed: true,
          previewAppNote: '课程网站',
          previewNamedPath: 'app',
          previewTunnelUp: false,
        }),
        expect: '应用预览暂不可用',
      },
      {
        name: '取预览失败',
        note: '读这一格本身失败：一句「预览加载失败」，底下一行是这个错。',
        props: previewPanelProps({
          previewFile: null,
          documentType: null,
          documentName: '',
          previewMime: '',
          previewError: '接口返回 502',
        }),
        expect: '预览加载失败',
      },
    ],
  },
  {
    id: 'panel-doc',
    title: 'PanelDocView',
    about: '总览那一格的文档：工具条、正文和评论侧栏，窄面板中评论以抽屉展开。',
    file: 'src/components/panels/PanelDocView.vue',
    component: PanelDocView,
    needs: UI,
    states: [
      {
        name: '没有话题',
        note: '这一格属于一个话题；没有话题时说的话和「文档是空的」不一样。',
        props: docPanelProps({ topic: null }),
        expect: '选择一个话题查看文档',
      },
      {
        name: '文档还在路上的时候',
        note: '加载期间摆的是骨架，编辑器让位 —— 空编辑器会亮出「芝士会在这里维护文档」，那句话的意思是「这篇是空的」，而它还没到。',
        props: docPanelProps({ loading: true }),
      },
      {
        name: '一篇文档',
        note: '标题（话题名）、工具条、正文和评论入口都在。正文是一份协同文档，几个人同时打开时横条上是他们的头像。',
        props: docPanelProps({
          session: docSession(
            '## 课程资料\n\n每周一更新一次，作业在这里登记。\n\n- 第一周：读书报告\n- 第二周：小组讨论'
          ),
          peers: [
            {
              clientId: 2,
              handle: 'li-laoshi',
              agent: false,
              name: '李老师',
              avatar: '',
              color: avatarColor('li-laoshi'),
            },
            {
              clientId: 3,
              handle: 'cheese-demo',
              agent: true,
              name: AGENT_NAME,
              avatar: '',
              color: avatarColor('cheese-demo'),
            },
          ],
        }),
        expect: '课程资料',
      },
      {
        name: '没连上',
        note: '断开时改动留在本地，连上后自动合并：顶栏上说的是状态，不是错误。',
        props: docPanelProps({ session: docSession('还没同步的改动也在这里。'), connection: 'offline' }),
        expect: '没连上，改动会在连上后同步',
      },
      {
        name: '没有编辑权限',
        note: '只读由凭证决定：横条上写着只读，点不回编辑，⋯ 里也没有那一项。',
        props: docPanelProps({ session: docSession('只能看，不能改。'), editable: false, readOnly: true }),
        expect: '只读',
      },
    ],
  },
  // 提案那两件（`AskCard`、`AskFlow`）和文档里的块各在自己的文件里：`catalogAsk.ts`、`catalogDoc.ts`。
  ...ASK_ENTRIES,
  ...DOC_BLOCK_ENTRIES,
  {
    id: 'compute-choice-form',
    title: 'ComputeChoiceForm',
    about: '选一台工作电脑：云端或自有设备；云端可自定义规格，先看云端此刻能开的范围。',
    file: 'src/components/ComputeChoiceForm.vue',
    component: ComputeChoiceForm,
    needs: UI,
    states: [
      {
        name: '查到了范围',
        note: '勾「自定义」后显示可选范围（云端供应与平台允许值的交集）；填超的那一格标红，按钮变灰。数字是示例。',
        props: { devices: COMPUTE_DEVICES, cloudAvailable: true, supply: CLOUD_SUPPLY },
        expect: '自定义 CPU、内存和磁盘',
      },
      {
        name: '查不到范围',
        note: '云端没应答时照实说查不到、说原因，不显示任何范围数字，仍可保存，开机时由云端校验。',
        props: { devices: COMPUTE_DEVICES, cloudAvailable: true, supply: CLOUD_SUPPLY_UNKNOWN },
        expect: '自定义 CPU、内存和磁盘',
      },
      {
        name: '正在查',
        note: '范围还在路上时按钮不可点，不拿旧数或默认数先顶上。',
        props: { devices: COMPUTE_DEVICES, cloudAvailable: true, supply: null, supplyLoading: true },
        expect: '自定义 CPU、内存和磁盘',
      },
    ],
  },
  {
    id: 'base-button',
    title: 'BaseButton',
    about: '知是的按钮：调用处只说角色（主操作 / 次要 / 轻量 / 危险）和大小，颜色与样式由角色推出。',
    file: 'src/components/base/BaseButton.vue',
    component: BaseButton,
    needs: UI,
    states: [
      { name: '主操作', note: '一块区域里让事情往下走的那一颗，琥珀实心。同一组并排按钮里只有一颗。', props: { kind: 'primary' }, slot: '提交', expect: '提交' },
      { name: '次要', note: '和主操作并排的其余操作。描边用 --line-2，字用 --text，不抢主操作。', props: { kind: 'secondary' }, slot: '取消', expect: '取消' },
      { name: '轻量', note: '工具条、列表行、卡片角落里的操作。无底无框，字用 --muted，悬停变深。', props: { kind: 'ghost' }, slot: '查看全部', expect: '查看全部' },
      { name: '危险', note: '删除、移除这类不可逆操作。字用 --danger-ink，不用 --danger 写字。', props: { kind: 'danger' }, slot: '移除成员', expect: '移除成员' },
      { name: '危险（确认）', note: '只用在确认弹窗里那一颗「删除」：实心红。', props: { kind: 'danger', solid: true }, slot: '删除', expect: '删除' },
      { name: '小号', note: '28px：列表行、工具条、卡片内。字号 13。', props: { kind: 'secondary', size: 'sm' }, slot: '重试', expect: '重试' },
      { name: '大号', note: '44px：门口页面的单个大按钮、手机上整行宽的提交。', props: { kind: 'primary', size: 'lg' }, slot: '登录', expect: '登录' },
      { name: '纯图标', note: '传 icon，必须同时给 aria-label。轻量样式，图标 20px（小号 18px）。', props: { kind: 'ghost', icon: 'mdi-dots-horizontal', 'aria-label': '更多' } },
      { name: '加载中', note: 'loading 原样透传给 v-btn：转圈时按钮宽度不变、不可再点。', props: { kind: 'primary', loading: true }, slot: '保存' },
    ],
  },
]

/** 按 id 找一条（地址里那一段）。 */
export function catalogEntry(id: string): CatalogEntry | null {
  return CATALOG.find((entry) => entry.id === id) ?? null
}
