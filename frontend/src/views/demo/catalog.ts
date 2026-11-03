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
 * 加一个组件：新建或挑一册 —— 同级的一个 `catalog*.ts`，或者 `catalog/` 目录下按场景
 * 分的那几册 —— 把上面四件事写清。骨架可以用
 * `node scripts/catalog-scaffold.mjs <组件>` 按 props 生成（它留下的占位记号见
 * `catalogTodo.ts`）。
 *
 * 和 Storybook 的 CSF 一一对应，以后要换工具或做视觉回归能机械迁移：条目是 CSF 的
 * default export（`component`、`title`、`args`），每一格是一个命名 story（`name`、
 * `props` 即这一格的 args、`slot` 即默认插槽）；一格的参数是条目的 `args` 叠上这一格
 * 的 `props`，算法只有 `stateProps` 这一处。
 *
 * **这一份现在是个薄门面**：原先内联在这里的几十条按场景搬进了 `catalog/` 目录
 * （通用基础件、对话区、采纳横条、面板页签条、导航与外壳、空间分析、账号与反馈、
 * 面板三格），这里只留「这一册在整份目录里的哪一段」和那三个共用的函数。从这里出去
 * 的名字和原来一模一样，调用方一行都不用改。
 *
 * 一册要上预览站，得在这里（或在一册里）**既被 import 又被展开** —— 只 import
 * 不展开的那种，预览站根本没渲染它。
 */
import type { CatalogEntry, CatalogNeed, CatalogState } from './catalog/types'

import { ACCEPT_BAR_ENTRIES } from './catalog/accept-bar'
import { ACCOUNT_ENTRIES } from './catalog/account'
import { ANALYTICS_ENTRIES } from './catalog/analytics'
import { COMMON_ENTRIES } from './catalog/common'
import { CONVERSATION_ENTRIES } from './catalog/conversation'
import { DOC_SHARE_ENTRIES } from './catalog/doc-share'
import { NAVIGATION_ENTRIES } from './catalog/navigation'
import { PANEL_TABS_ENTRIES } from './catalog/panel-tabs'
import { PANEL_VIEW_ENTRIES } from './catalog/panel-views'
import { TEAM_DOCS_ENTRIES } from './catalog/team-docs'
import { ACCEPT_ENTRIES } from './catalogAccept'
import { ADMIN_ENTRIES } from './catalogAdmin'
import { ATTACHMENT_ENTRIES } from './catalogAttachments'
import { BASE_ENTRIES } from './catalogBase'
import { CANVAS_ENTRIES } from './catalogCanvas'
import { CHANNEL_DIALOG_ENTRIES } from './catalogChannelDialogs'
import { CHANNEL_FILES_ENTRIES } from './catalogChannelFiles'
import { CHAT_ENTRIES } from './catalogChat'
import { CREDITS_ENTRIES } from './catalogCredits'
import { DASHBOARD_ENTRIES } from './catalogDashboard'
import { DOC_BLOCK_ENTRIES } from './catalogDoc'
import { DOC_LINE_ENTRIES } from './catalogDocLine'
import { FEEDBACK_ENTRIES } from './catalogFeedback'
import { HOME_ENTRIES } from './catalogHome'
import { LIBRARY_ENTRIES } from './catalogLibrary'
import { MARKET_ENTRIES } from './catalogMarket'
import { MODELS_ENTRIES } from './catalogModels'
import { NOTIFICATION_ENTRIES } from './catalogNotifications'
import { OVERVIEW_ENTRIES } from './catalogOverview'
import { PALETTE_ENTRIES } from './catalogPalette'
import { PANEL_ENTRIES } from './catalogPanels'
import { PUBLIC_ENTRIES } from './catalogPublic'
import { QUEUE_ENTRIES } from './catalogQueue'
import { RAIL_ENTRIES } from './catalogRail'
import { REVIEW_ENTRIES } from './catalogReview'
import { ROOM_ENTRIES } from './catalogRoom'
import { ROUTINE_ENTRIES } from './catalogRoutine'
import { SCENES_MISC_ENTRIES } from './catalogScenesMisc'
import { SCENES_MISC2_ENTRIES } from './catalogScenesMisc2'
import { SCENES_MISC3_ENTRIES } from './catalogScenesMisc3'
import { SCENES_PAGES_ENTRIES } from './catalogScenesPages'
import { SCENES_PAGES2_ENTRIES } from './catalogScenesPages2'
import { SCENES_PAGES3_ENTRIES } from './catalogScenesPages3'
import { SCENES_SPACES_ENTRIES } from './catalogScenesSpaces'
import { SCENES_SPACES_DETAIL_ENTRIES } from './catalogScenesSpacesDetail'
import { SCENES_WORKSPACE_ENTRIES } from './catalogScenesWorkspace'
import { SETTINGS_ENTRIES } from './catalogSettings'
import { SETTINGS_DATA_ENTRIES } from './catalogSettingsData'
import { SHARED_ENTRIES } from './catalogShared'
import { SHELL_ENTRIES } from './catalogShell'
import { SKILL_ENTRIES } from './catalogSkills'
import { TASK_FORM_ENTRIES } from './catalogTaskForm'
import { TASK_STATE_ENTRIES } from './catalogTaskState'
import { USAGE_ENTRIES } from './catalogUsage'
import { VIEW_ENTRIES } from './catalogViews'

export type { CatalogEntry, CatalogNeed, CatalogState }

export const CATALOG: CatalogEntry[] = [
  // 前八组（通用基础件、对话区、采纳横条、面板页签条、导航与外壳、空间分析、
  // 账号与反馈、面板三格）拆在 `catalog/` 目录下，每册一个场景，理由写在各自
  // 文件头上。下面带着 `...` 的还是原来那批同级分册，位置一处没动。
  ...COMMON_ENTRIES,
  ...TEAM_DOCS_ENTRIES,
  ...DOC_SHARE_ENTRIES,
  ...CONVERSATION_ENTRIES,
  ...ACCEPT_BAR_ENTRIES,
  ...PANEL_TABS_ENTRIES,
  // admin 那一组（看板的 KPI 卡与横向排行、迷你列表、待办那一列）在自己的文件里：
  // `catalogAdmin.ts`。
  ...ADMIN_ENTRIES,
  ...NAVIGATION_ENTRIES,
  ...ANALYTICS_ENTRIES,
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
  ...REVIEW_ENTRIES,
  // 输入区那一组（从 1039 行的 RoomComposer 拆出来的三件，数据就在那份里）在 `catalogRoom.ts`。
  ...ROOM_ENTRIES,
  // 任务状态那一组（同一种状态在任务卡、页头、侧栏、全部任务、等你处理里各画一遍）：
  // `catalogTaskState.ts`。
  ...TASK_STATE_ENTRIES,
  ...DOC_LINE_ENTRIES,
  // 几个路由页拆出来的画面那一组（设备审批、已归档的项目、打开预览 / 网站，以及项目里的
  // 例行事务和内容搜索两整页）：`catalogScenesWorkspace.ts`，数据在
  // `catalogScenesWorkspaceFixtures.ts`。
  ...SCENES_MISC_ENTRIES,
  // 第三批拆出来的画面（管理员看板告警、后台成员管理）在自己的文件里：
  // `catalogScenesMisc2.ts`，数据在 `catalogScenesMisc2Fixtures.ts`。
  ...SCENES_MISC2_ENTRIES,
  ...SCENES_MISC3_ENTRIES,
  // 第四批拆出来的画面（空间名录、小队链接落地页、小队项目列表、提问页）在自己的文件里：
  // `catalogScenesPages.ts`，数据在 `catalogScenesPagesFixtures.ts`。
  ...SCENES_PAGES_ENTRIES,
  // 这一批拆出来的画面（功能数据页、我的连接、反馈详情、项目的技能）：`catalogScenesPages2.ts`，
  // 数据在 `catalogScenesPages2Fixtures.ts`。
  ...SCENES_PAGES2_ENTRIES,
  // 这一批拆出来的画面（空间分析那一层、一道题的全部回答）和那张回答卡片：
  // `catalogScenesPages3.ts`，数据在 `catalogScenesPages3Fixtures.ts`。
  ...SCENES_PAGES3_ENTRIES,
  ...SCENES_SPACES_ENTRIES,
  ...SCENES_SPACES_DETAIL_ENTRIES,
  ...SCENES_WORKSPACE_ENTRIES,
  // 通知渲染那一组（十件「一条动态长什么样」，数据在 `catalogNotificationsFixtures.ts`）
  // 在自己的文件里：`catalogNotifications.ts`。
  ...NOTIFICATION_ENTRIES,
  // 资料库页拆出来的四件：`catalogLibrary.ts`。
  ...LIBRARY_ENTRIES,
  // 命令面板拆出来的四块（顶上那一栏、结果、桌面上的操作清单、底栏）：`catalogPalette.ts`。
  ...PALETTE_ENTRIES,
  // 发题表单那一组（从 1089 行的 TaskForm 拆出来的七张卡加两个弹窗，夹具在自己
  // 那一份里）在自己的文件里：`catalogTaskForm.ts`。
  ...TASK_FORM_ENTRIES,
  // 项目设置那一组（从 1041 行的 ProjectSettingsView 拆出来的六块）在自己的文件里：
  // `catalogSettings.ts`（数据在 `catalogSettingsFixtures.ts`）。
  ...SETTINGS_ENTRIES,
  // 再一组：设置页里那五件 `Project*Settings`（数据、保存、轮询、授权回跳都下沉到
  // `composables/useProject*Settings.ts` 之后升成的 A 级）在自己的文件里：
  // `catalogSettingsData.ts`（数据在 `catalogSettingsDataFixtures.ts`）。
  ...SETTINGS_DATA_ENTRIES,
  // 市场页、外壳上的版本徽标与首页外框：`catalogMarket.ts`、`catalogShell.ts`。
  ...MARKET_ENTRIES,
  ...SHELL_ENTRIES,
  // 账号与反馈那一组（注册表单底下那两句协议、反馈列表里的一行）：
  // `catalog/account.ts`。
  ...ACCOUNT_ENTRIES,
  // 工作面板那三格自己的内容（改动、预览、概览里的文档）：`catalog/panel-views.ts`。
  ...PANEL_VIEW_ENTRIES,
  // 文档里的块、基础组件各在自己的文件里：`catalogDoc.ts`、`catalogBase.ts`。
  ...DOC_BLOCK_ENTRIES,
  ...CANVAS_ENTRIES,
  ...BASE_ENTRIES,
  ...PANEL_ENTRIES,
  // 例行事务那一组（建规则的那两页）在自己的文件里：`catalogRoutine.ts`（夹具借
  // `catalogPanelsFixtures.ts` 里那一条规则，两处画的是同一个产品里的同一条）。
  ...ROUTINE_ENTRIES,
  // 跨目录的共用件那一组（表壳与它的表头格、富文本编辑器与附件图、页头、句子里的人）在
  // 自己的文件里：`catalogShared.ts`（数据在 `catalogSharedFixtures.ts`）。
  ...SHARED_ENTRIES,
  // 整页那两件（一项产物的版本历史、小队的对外一面）在自己的文件里：`catalogViews.ts`。
  ...VIEW_ENTRIES,
  // 频道和私聊「文件」那一格在自己的文件里：`catalogChannelFiles.ts`。
  ...CHANNEL_FILES_ENTRIES,
  // 频道那两个弹窗（删频道前的确认、新建支线）在自己的文件里：`catalogChannelDialogs.ts`。
  ...CHANNEL_DIALOG_ENTRIES,
  // 反馈详情那一组（从详情页右栏拆出来的处理人、评论条、评论楼、进展时间线，数据在
  // `catalogFeedbackFixtures.ts`）在自己的文件里：`catalogFeedback.ts`。
  ...FEEDBACK_ENTRIES,
  // 技能详情抽屉那一件在自己的文件里：`catalogSkills.ts`。
  ...SKILL_ENTRIES,
  // 附件那两件（一条消息里的图、一份文档的第一页）在自己的文件里：`catalogAttachments.ts`。
  ...ATTACHMENT_ENTRIES,
  // 公共站那几页（三个公共页共用的外壳、首页、方案页，和首页里那段房间）在自己的文件
  // 里：`catalogPublic.ts`。
  ...PUBLIC_ENTRIES,
  // 首页那一格（那份目录、它两端的两个画面、它带的四个弹窗）在自己的文件里：
  // `catalogHome.ts`。
  ...HOME_ENTRIES,
  // 项目总览上的现状和起草那一小块在自己的文件里：`catalogOverview.ts`。
  ...OVERVIEW_ENTRIES,
]

/** 一格实际拿到的参数：条目共用的 `args` 叠上这一格自己的 `props`。 */
export function stateProps(entry: CatalogEntry, state: CatalogState): Record<string, unknown> {
  return { ...entry.args, ...state.props }
}

/** 目录页上的分组：源码所在目录，`src/components/panels/doc/X.vue` → `components/panels/doc`。 */
export function catalogGroup(entry: CatalogEntry): string {
  return entry.file
    .replace(/^src\//, '')
    .split('/')
    .slice(0, -1)
    .join('/')
}

/** 按 id 找一条（地址里那一段）。 */
export function catalogEntry(id: string): CatalogEntry | null {
  return CATALOG.find((entry) => entry.id === id) ?? null
}
