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

import {
  ACCEPT_CARD,
  ACCEPT_DONE,
  ACTION_ROWS,
  ADMIN_QUEUE,
  AGENT_NAME,
  BAR_ROWS,
  BAR_ROWS_LONG,
  CARD_FILED,
  CHEESE_LINES,
  EXCERPTS,
  KPI_STATES,
  LONG_ROW,
  NAV_ITEMS,
  NUMBER_ROWS,
  OPEN_FILES,
  RAIL_ACTIONS,
  RAIL_PAGES,
  RAIL_ROOT_TOPIC,
  RAIL_ROWS,
  RAIL_TERMS,
  roomMessageProps,
  roomNoticeProps,
  SHEET_ACTIONS,
  TURN_SUMMARY,
  WANG_CLOSING,
  WANG_LINES,
} from './catalogFixtures'

import AdminActionList from '@/components/admin/AdminActionList.vue'
import AdminBarChart from '@/components/admin/AdminBarChart.vue'
import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminNumberList from '@/components/admin/AdminNumberList.vue'
import AppPage from '@/components/common/AppPage.vue'
import MobileActionSheet from '@/components/common/MobileActionSheet.vue'
import BottomAppBar from '@/components/common/Navigation/BottomAppBar.vue'
import NavLink from '@/components/common/NavLink.vue'
import UserRef from '@/components/common/UserRef.vue'
import { panelTabs } from '@/components/panels/panelTabList'
import PanelTabs from '@/components/panels/PanelTabs.vue'
import RoomMessage from '@/components/room/RoomMessage.vue'
import RoomNotice from '@/components/room/RoomNotice.vue'
import TopicRailArchivedGroup from '@/components/topic-sidebar/TopicRailArchivedGroup.vue'
import TopicRailBadge from '@/components/topic-sidebar/TopicRailBadge.vue'
import TopicRailGroupToggle from '@/components/topic-sidebar/TopicRailGroupToggle.vue'
import TopicRailHeader from '@/components/topic-sidebar/TopicRailHeader.vue'
import TopicRailPinnedRows from '@/components/topic-sidebar/TopicRailPinnedRows.vue'
import TopicRailRow from '@/components/topic-sidebar/TopicRailRow.vue'
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

/** 侧栏那几行看的是「当下的钟」：红灯、等了多久都按它算。固定一个时刻，免得预览
 *  站上那一格的红灯开着开着自己变了。 */
const RAIL_NOW = Date.parse('2026-09-29T09:00:00Z')

/** 一行话题那些不变的 props：每一格换的只是 `row` 和它那几个开关。 */
function rowProps(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    selected: false,
    page: false,
    renaming: false,
    menuOpen: false,
    stalled: false,
    toggleTitle: '收起',
    now: RAIL_NOW,
    agentName: AGENT_NAME,
    actions: () => RAIL_ACTIONS,
    ...over,
  }
}

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
    needs: UI,
    states: [
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
        name: '芝士的正文',
        note: '队友这一条挂的是它的身份（isAgent），头像按 handle 配色块。',
        props: roomMessageProps(CHEESE_LINES[0]),
        expect: '写完递验收卡给你',
      },
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
    // 这一件里面还留着一个 `router-link`（「去确认」那颗按钮，见文件里 287 行），
    // 所以它还不是「一件都不用装」的那一档 —— 目录里写着，免得有人以为漏了。
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
    needs: ['vuetify', 'pinia', 'router'],
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

  // ---- 项目侧栏：TopicSidebar 拆出来的那一组「只管画」的件 --------------------
  //
  // 一行话题、顶上那组置顶入口、项目名那一行、一个组头、一组已归档，外加那颗未读
  // 角标。它们一件都不认识路由、也不取数——每一格的 `needs` 就是这句话的机械证明：
  // 角标一件都不用装，话题行只要 Vuetify，要读词表的那两件才多一个 i18n。
  {
    id: 'topic-rail-badge',
    title: 'TopicRailBadge',
    about: '未读角标：一颗裸的琥珀数字，或者一个纯点。侧栏上六处未读共用它。',
    file: 'src/components/topic-sidebar/TopicRailBadge.vue',
    component: TopicRailBadge,
    // 一条依赖都不装：它只把一个数字画成「99+」，`countLabel` 是纯函数。
    needs: [],
    states: [
      {
        name: '几条新消息',
        note: '行尾唯一常驻的右对齐元素：裸的琥珀数字，不画成药丸也不画成红圆。',
        props: { count: 3 },
        expect: '3',
      },
      {
        name: '多到写不下',
        note: '超过 99 就写 99+：一个失控的计数不该把行撑开。',
        props: { count: 128 },
        expect: '99+',
      },
      {
        name: '只给一个点',
        note: '收起来的一组：别人话题里有几条与我无关，但「那边有动静」值得知道。',
        props: { dot: true, title: '其他话题里有新消息' },
      },
      {
        name: '含收起来的子话题',
        note: '折叠不能把「有新消息」吞掉：这句 title 说的就是右边那个数是怎么来的。',
        props: { count: 7, title: '含收起的子话题 4 条新消息' },
        expect: '7',
      },
    ],
  },
  {
    id: 'topic-rail-row',
    title: 'TopicRailRow',
    about: '话题列表里的一行：左边一个 16px 状态槽，右边未读和 hover 才浮现的 ⋯。',
    file: 'src/components/topic-sidebar/TopicRailRow.vue',
    component: TopicRailRow,
    // 一行不认识路由、也不取数：状态、折叠、未读全在 props 里（`row` 是
    // `lib/topicTree.ts` 的 `VisibleRow`，聚合已经在那边算完了）。
    needs: UI,
    states: [
      {
        name: '芝士在跑',
        note: '绿呼吸点。人凭它判断啥时候该派下一个任务——和归档/采纳状态无关。',
        props: rowProps({ row: RAIL_ROWS.running }),
        expect: '写第 4 章的教案',
      },
      {
        name: '等你拍板',
        note: '琥珀点：有待处理的事项。未读的 @ 不点这颗灯（芝士汇报、递卡都 @人），未读有右边的数字。',
        props: rowProps({ row: RAIL_ROWS.awaits }),
        expect: '决定这学期用哪本教材',
      },
      {
        name: '出了故障',
        note: '红灯 + 一圈淡红晕。红的判据（报错 / 等太久）是父级带钟算的，hover 才说得出是哪一种。',
        props: rowProps({ row: RAIL_ROWS.stalled, stalled: true }),
        expect: '把成绩单导出成 CSV',
      },
      {
        name: '收起来的父话题',
        note: '折叠开关顶掉状态点，所以它自己带聚合色（橙 = 里面有事等你）；右边是聚上来的未读，说的是「这里还有内容」。',
        props: rowProps({ row: RAIL_ROWS.collapsed, toggleTitle: '展开：里面有待处理的事项' }),
        expect: '期末复习',
      },
      {
        name: '子话题',
        note: '缩进一级，左边一条竖向引导线；未读角标说的是这一行自己有几条。',
        props: rowProps({ row: RAIL_ROWS.sub }),
        expect: '第 3 题：为什么天空是蓝的',
      },
      {
        name: '已采纳，在等合并',
        note: '常亮的空心绿圈：和呼吸点靠「动不动」「实心还是空心」两样分开，关掉动效也分得开。',
        props: rowProps({ row: RAIL_ROWS.merging }),
        expect: '重排第一章的目录',
      },
      {
        name: '整页形态（手机）',
        note: '行更高（44px），没有那颗 hover 才出现的 ⋯ —— 那里长按一行打开同一组操作（见 TopicSidebar.longPress）。',
        props: rowProps({ row: RAIL_ROWS.running, page: true, selected: true }),
        expect: '写第 4 章的教案',
      },
    ],
  },
  {
    id: 'topic-rail-pinned',
    title: 'TopicRailPinnedRows',
    about: '话题列表顶上那几行置顶入口：全局房间、这个项目露出来的几页、项目文档。',
    file: 'src/components/topic-sidebar/TopicRailPinnedRows.vue',
    component: TopicRailPinnedRows,
    // 只读词表和 props：哪几页露出来了、当前在哪一页，都是父级算好递进来的。
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '置顶上那组入口',
        note: '和话题行同一种语法（同图标槽、同缩进、同选中态）：点它会发生什么，不用另学一遍。',
        props: {
          rootTopic: RAIL_ROOT_TOPIC,
          selectedTopicId: null,
          pages: RAIL_PAGES,
          routeName: 'project-library',
          terms: RAIL_TERMS,
          docsActive: false,
          privateUnreadTotal: 3,
          page: false,
          unreadOf: (id: string) => (id === 't-root' ? 3 : 0),
        },
        expect: '资料库',
      },
      {
        name: '站在全局房间里',
        note: '选中态落在「全局」那一行上，它的未读角标也亮着——置顶行和话题行是同一套。',
        props: {
          rootTopic: RAIL_ROOT_TOPIC,
          selectedTopicId: 't-root',
          pages: RAIL_PAGES,
          routeName: 'workspace-running',
          terms: RAIL_TERMS,
          docsActive: false,
          privateUnreadTotal: 0,
          page: false,
          unreadOf: () => 0,
        },
        expect: '全局',
      },
      {
        name: '打开的是项目文档',
        note: '四种文档（章程/决策/周报/记忆）在侧栏只占这一行，任何一种开着它都是选中态。',
        props: {
          rootTopic: RAIL_ROOT_TOPIC,
          selectedTopicId: null,
          pages: RAIL_PAGES,
          routeName: 'project-docs',
          terms: RAIL_TERMS,
          docsActive: true,
          privateUnreadTotal: 0,
          page: false,
          unreadOf: () => 0,
        },
        expect: '项目文档',
      },
      {
        name: '手机上：列表只留话题',
        note: '那几页收进了项目名旁边那颗 ⌄（整页形态的列表只留话题），所以这里只剩「全局」一行。',
        props: {
          rootTopic: RAIL_ROOT_TOPIC,
          selectedTopicId: null,
          pages: RAIL_PAGES,
          routeName: 'workspace-running',
          terms: RAIL_TERMS,
          docsActive: false,
          privateUnreadTotal: 0,
          page: true,
          unreadOf: () => 0,
        },
        expect: '全局',
      },
    ],
  },
  {
    id: 'topic-rail-header',
    title: 'TopicRailHeader',
    about: '项目名那一行：标识 + 搜索 + 项目菜单（那些一年点几次的页面）。',
    file: 'src/components/topic-sidebar/TopicRailHeader.vue',
    component: TopicRailHeader,
    // 名字和 chevron 是两个按钮：名字回项目首页，chevron 展开收起的那几页。
    // 整页形态下这一行填进顶栏（Teleport），另外两个形态里它就在原地。
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '站在看板上',
        note: '首页是登录进项目看到的第一屏，所以它自己占着项目名那一行——菜单里不再列一遍。',
        props: {
          page: false,
          column: false,
          homeActive: true,
          homeKey: 'workspace-running',
          homeIcon: 'mdi-view-column-outline',
          projectName: '课程项目',
          privateUnreadTotal: 3,
          searchTitle: '搜索（⌘K）',
          menuOpen: false,
          menuPages: [{ key: 'calendar', label: 'navigation.project.calendar', icon: 'mdi-calendar-outline' }],
          routeName: 'workspace-running',
          terms: RAIL_TERMS,
          projectSelected: true,
          canTransfer: true,
          canLeave: false,
        },
        expect: '课程项目',
      },
      {
        name: '我不是所有者',
        note: '所有者换「转让项目」，其余的人换「退出项目」——两句是同一件事的两半。',
        props: {
          page: false,
          column: false,
          homeActive: false,
          homeKey: 'workspace-running',
          homeIcon: 'mdi-view-column-outline',
          projectName: '别人的项目',
          privateUnreadTotal: 0,
          searchTitle: '搜索（⌘K）',
          menuOpen: false,
          menuPages: [],
          routeName: 'project-members',
          terms: RAIL_TERMS,
          projectSelected: true,
          canTransfer: false,
          canLeave: true,
        },
        expect: '别人的项目',
      },
      {
        name: '还没选项目',
        note: '项目名退回一句「选择项目」，菜单里那几项点不动（`disabled` 而不是藏起来：不让人猜是不是没画）。',
        props: {
          page: false,
          column: false,
          homeActive: false,
          homeKey: 'workspace-running',
          homeIcon: 'mdi-view-column-outline',
          projectName: '选择项目',
          privateUnreadTotal: 0,
          searchTitle: '搜索（⌘K）',
          menuOpen: false,
          menuPages: [],
          routeName: null,
          terms: RAIL_TERMS,
          projectSelected: false,
          canTransfer: false,
          canLeave: false,
        },
        expect: '选择项目',
      },
      {
        name: '两栏（平板）',
        note: '这一行留在左栏自己的顶上（不填进顶栏），高度和底线照桌面那一条，和右边的顶栏接成一条线。',
        props: {
          page: true,
          column: true,
          homeActive: false,
          homeKey: 'workspace-running',
          homeIcon: 'mdi-view-column-outline',
          projectName: '课程项目',
          privateUnreadTotal: 0,
          searchTitle: '搜索',
          menuOpen: false,
          menuPages: [],
          routeName: 'calendar',
          terms: RAIL_TERMS,
          projectSelected: true,
          canTransfer: false,
          canLeave: true,
        },
        expect: '课程项目',
      },
    ],
  },
  {
    id: 'topic-rail-group-toggle',
    title: 'TopicRailGroupToggle',
    about: '一组被收起来的话题的组头：一个 chevron + 组名 + 条数。',
    file: 'src/components/topic-sidebar/TopicRailGroupToggle.vue',
    component: TopicRailGroupToggle,
    // 「其他话题」和「已归档」两处共用它——同一条侧栏里「一组被收起来的话题」
    // 只能有一种读法。
    needs: UI,
    states: [
      {
        name: '收着，里面还有新消息',
        note: '收起来时未读聚成一个点（不是数字）：别人话题里有几条与我无关，但那边有动静值得知道。',
        props: { label: '其他话题', count: 12, open: false, unread: true, unreadTitle: '其他话题里有新消息' },
        expect: '其他话题',
      },
      {
        name: '展开着',
        note: '展开着就没有那颗点：里面的事本来就在眼前。',
        props: { label: '其他话题', count: 12, open: true, unread: false, unreadTitle: '其他话题里有新消息' },
        expect: '12',
      },
      {
        name: '已归档那一组',
        note: '同一个组头，只多一个 archived 类（测试和样式凭它区分两个组头）。',
        props: {
          label: '已归档',
          count: 3,
          open: false,
          unread: false,
          unreadTitle: '归档话题里有新消息',
          archived: true,
        },
        expect: '已归档',
      },
    ],
  },
  {
    id: 'topic-rail-archived',
    title: 'TopicRailArchivedGroup',
    about: '列表最底下那组「已归档」：组头 + 收着的那批话题（新的在前）。',
    file: 'src/components/topic-sidebar/TopicRailArchivedGroup.vue',
    component: TopicRailArchivedGroup,
    // 收着的开关是这一件自己的状态，所以这里看到的是组头那一行；行本身（归档图标、
    // 种类词、行尾那颗「取消归档」）点开才有——那不在这份 props 能表达的范围内。
    needs: UI,
    states: [
      {
        name: '有一批归档话题',
        note: '没有归档话题时整件什么都不画（条件留在组件里，免得「最后一条也被取消归档」把开关复位）。',
        props: {
          rows: [RAIL_ROWS.archived],
          selectedTopicId: null,
          page: false,
          unread: true,
          unreadOf: () => 2,
        },
        expect: '已归档',
      },
      {
        name: '整页形态（手机）',
        note: '行更高，行尾那颗「取消归档」也撑到手指点得中。',
        props: {
          rows: [RAIL_ROWS.archived],
          selectedTopicId: RAIL_ROWS.archived.id,
          page: true,
          unread: false,
          unreadOf: () => 0,
        },
        expect: '已归档',
      },
    ],
  },
]

/** 按 id 找一条（地址里那一段）。 */
export function catalogEntry(id: string): CatalogEntry | null {
  return CATALOG.find((entry) => entry.id === id) ?? null
}
