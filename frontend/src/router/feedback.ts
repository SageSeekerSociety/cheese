import type { RouteLocationNormalized, RouteLocationRaw, RouteRecordRaw } from 'vue-router'

import {
  adminSectionForRouteName,
  canEnterAdmin,
  firstVisibleAdminSectionTo,
  isAdminSectionVisible,
} from '@/lib/adminSections'
import { useFeedbackStore } from '@/stores/feedback'

/**
 * `/admin` 父路由的守卫：进了后台却落在一块自己**进不去**的分区上时（例如平台管理员
 * 点开 `/admin/queue`，而队列只归反馈管理员），把人领到第一块进得去的分区。
 *
 * 拎成一个具名函数是为了让 `AdminLayout.spec`（或将来的守卫测试）用**同一份**实现 ——
 * 把这段判据在测试里再抄一遍，测的就是抄本，不是产品走的那条路。
 *
 * 为什么在这里 `await loadMeta()`：改道要有答案才能决定，而答案（两份名单）是服务端给的
 * （`GET /feedback/meta`）。第一次冷打开后台时它还没到，就得等 —— 否则会先按「名单为空」
 * 放行、把一块进不去的分区画出来。`metaChecked` 为真时这一句是零成本的。
 *
 * 不是管理员（两份名单都没有）时返回 `true`：**不**在这里处理，交给 `AdminLayout` 画那扇
 * 「你不在名单里」的门 —— 后台是不是「什么都没有」和「你没在名单里」是两回事。
 */
export async function adminSectionGuard(to: RouteLocationNormalized): Promise<RouteLocationRaw | true> {
  const store = useFeedbackStore()
  if (!store.metaChecked) await store.loadMeta()
  const meta = store.meta
  if (!canEnterAdmin(meta)) return true
  const section = adminSectionForRouteName(to.name as string | undefined)
  if (!section || isAdminSectionVisible(section, meta)) return true
  const landing = firstVisibleAdminSectionTo(meta)
  return landing && landing !== to.path ? landing : true
}

/**
 * 反馈与管理后台的路由。都在这里，预览入口（`src/proto-feedback.ts`）也直接用它。
 *
 * 用户侧三条都是**顶层**的，没有一个挂在 /projects/:id 下面 —— 反馈说的是平台本身，
 * 跟「我现在在哪个项目里」没有关系，挂在项目下面会让人以为这条反馈只属于那个项目。
 *
 * `/feedback/mine` 和 `/feedback/new` 写在 `/feedback/:id` **前面**：vue-router 4 的排序
 * 本来就把静态段排在参数段前面（所以顺序其实不决定谁赢），但写在这里读起来是「固定段
 * 先声明的」，以后加 `/feedback/counts` 之类的时候不会有人担心被 `:id` 吃掉。
 *
 * `/feedback/new` 是提交表单的**页面壳**（`FeedbackSubmitPage`）。它是一条真路由而不是
 * 一个浮层，理由写在 `SubmitFeedbackPage.vue` 的文件头：刷新之后人还停在表单上、地址
 * 可以分享出去。会话里那张 agent 提案卡不走它 —— 那条走对话框，因为从对话里跳走会把
 * 「我刚看到的那张卡」留在身后。两个壳共用同一份表单（`SubmitFeedbackForm`）。
 *
 * 后台走 `/admin/*` 前缀，对应需求里的「类似 admin.okcheese.com 的独立后台」。
 * 现在它是同构里的一条普通路由（同一个 SPA、同一个构建），因为重点是那两套界面
 * 长得不一样、口径不一样；**真的拆成独立域名是运维/鉴权的事**，不是这一轮要决定的
 * 事，所以这里不为它做任何特殊处理。
 *
 * 后台和别的内部页面住在同一个外框里：分区是 `sidebar` 视图（`AdminSidebar`，手机上
 * 是抽屉），内容区是 `AdminLayout`（门、全局键）里装的子页。所以 `/admin` 是一条带
 * `children` 的父路由。
 *
 * `/admin/queue` 和 `/admin/dashboard` 是这一轮新加的两条：
 *
 * - `queue` 是「反馈管理」这个模块改叫「队列」之后的家。换地址而不是原地换内容，因为
 *   「反馈管理」这个名字说的是「一页管所有反馈」，而它其实是按状态往前推的分诊队列，
 *   旁边还站着看板和成员 —— 三个平级的模块挤在同一个名字底下，链接分享出去对不上话。
 * - `dashboard` 是看板，同一层里的第三块。
 * - `/admin/feedback` **留着**，渲染一个薄壳（`AdminFeedbackPage` → `AdminQueuePage`）。
 *   老书签、老通知、别人贴在聊天里的链接都指到这里，删掉就是一个 404；而重定向会把
 *   地址栏里那个旧地址悄悄换掉，用户回头再复制一次时会以为自己记错了。留着它，代价
 *   是六行。
 *
 * `isFullPage: true` 是给顶栏用的：AppBar 取层级里第一个 isFullPage 的标题作为
 * 中间那行字（见 components/common/Navigation/AppBar.vue 的 updateTitle），
 * 不写的话顶栏会一直显示默认的「知是」。
 */
export default [
  {
    path: '/feedback',
    name: 'FeedbackCenter',
    component: () => import('@/views/feedback/FeedbackCenterPage.vue'),
    meta: { titleKey: 'navigation.feedback.center', isFullPage: true },
  },
  {
    path: '/feedback/mine',
    name: 'FeedbackMine',
    component: () => import('@/views/feedback/FeedbackMinePage.vue'),
    meta: { titleKey: 'navigation.feedback.mine', isFullPage: true },
  },
  {
    path: '/feedback/new',
    name: 'FeedbackSubmit',
    component: () => import('@/views/feedback/FeedbackSubmitPage.vue'),
    // `hideTabs`：这一页和详情页都是**页面栈里的一层**（从反馈中心推进来的），按仓库
    // 自己的移动端规范（`docs/plans/2026-08-18-mobile-shell-design.md` §4 第 4 条）层级
    // 进页面栈、不进底栏；去掉底栏那 56px，底部留给表单自己那条黏底的操作条。
    meta: { titleKey: 'feedback.submit.title', isFullPage: true, hideTabs: true },
  },
  {
    path: '/feedback/:id',
    name: 'FeedbackDetail',
    component: () => import('@/views/feedback/FeedbackDetailPage.vue'),
    // 同上：栈里的一层。详情页底部本来就叠着「评论框 + 操作栏」两层，再挂一条 56px
    // 的一级导航，手机上近三成屏幕被底部吃掉。
    meta: { titleKey: 'navigation.pages.feedbackDetail', isFullPage: true, hideTabs: true },
  },
  {
    path: '/admin',
    components: {
      default: () => import('@/views/admin/AdminLayout.vue'),
      sidebar: () => import('@/components/admin/AdminSidebar.vue'),
    },
    // 手机上分区侧栏是抽屉，所以顶栏给汉堡。
    meta: { drawer: true },
    // 只写地址不写组件：`/admin` 本身没有内容，直接落进队列 —— 后台里用得最多的那一块。
    redirect: '/admin/queue',
    // 进了后台却落在一块自己**进不去**的分区上时（例如平台管理员点开 `/admin/queue`，
    // 而队列只归反馈管理员），在这里把人领到第一块进得去的分区。原先这事藏在
    // `AdminLayout` 的 `watch` 里静悄悄 `router.replace`，地址栏自己变了、看不出是
    // 「按权限改道」；挪成一条声明式的重定向之后，改道这件事在路由表里读得到。
    // 判据见 `adminSectionGuard` 的注释。
    beforeEnter: adminSectionGuard,
    children: [
      {
        path: 'queue',
        name: 'AdminQueue',
        component: () => import('@/views/admin/AdminQueuePage.vue'),
        meta: { titleKey: 'navigation.admin.queue', isFullPage: true },
      },
      {
        path: 'dashboard',
        name: 'AdminDashboard',
        component: () => import('@/views/admin/AdminDashboardPage.vue'),
        meta: { titleKey: 'navigation.admin.dashboard', isFullPage: true },
      },
      {
        // 「功能数据」的目录页（`/admin/feature-stats`）：有哪些功能的数据页。
        // 它自己一个数字都不放，理由写在 `AdminFeatureStatsPage.vue` 的文件头。
        path: 'feature-stats',
        name: 'AdminFeatureStats',
        component: () => import('@/views/admin/AdminFeatureStatsPage.vue'),
        meta: { titleKey: 'navigation.admin.featureStats', isFullPage: true },
      },
      {
        // 各个功能自己的数据页：`:id` 由前端注册表（`views/admin/features/registry.ts`）
        // 翻成组件，所以加一个功能不动这个文件 —— 加一条路由是那种「忘了只在点进去
        // 那一刻才发现」的地方。
        path: 'feature-stats/:id',
        name: 'AdminFeature',
        component: () => import('@/views/admin/AdminFeaturePage.vue'),
        // 顶栏那行字对整块是同一个名字：从目录点进某一页时，它不该闪成另一句话。
        meta: { titleKey: 'navigation.admin.featureStats', isFullPage: true },
      },
      {
        // 架构还债进度（`/admin/ratchet`）。和看板、功能数据一样是**读**的页面，
        // 区别只在看的是什么：看板看平台、功能数据看某个功能，这一页看仓库自己的债。
        // 数据来自 CI 每次合入 main 后采的那份快照，页面上一个判定都不做。
        path: 'ratchet',
        name: 'AdminRatchet',
        component: () => import('@/views/admin/AdminRatchetPage.vue'),
        meta: { titleKey: 'navigation.admin.ratchet', isFullPage: true },
      },
      {
        // 网关模型管理那一页。和其它分区一样是后台里的一块，不是独立域名。
        path: 'models',
        name: 'AdminModels',
        component: () => import('@/views/admin/AdminModelsPage.vue'),
        meta: { titleKey: 'navigation.admin.models', isFullPage: true },
      },
      {
        // 方案与额度：方案、团队挂哪个方案、给团队发额度（#2397）。
        path: 'credits',
        name: 'AdminCredits',
        component: () => import('@/views/admin/AdminCreditsPage.vue'),
        meta: { titleKey: 'navigation.admin.credits', isFullPage: true },
      },
      {
        // main 后加的这一块（开板申请：有人申请开一个新题目板，平台管理员批准 / 驳回），
        // 地址与分区名都跟着它自己的 PR 走。
        path: 'spaces',
        name: 'AdminSpaces',
        component: () => import('@/views/admin/AdminSpacesPage.vue'),
        meta: { titleKey: 'navigation.admin.spaces', isFullPage: true },
      },
      {
        // 老地址，见文件头。渲染的是同一个队列，所以标题也跟它一致 —— 顶栏上那行字
        // 不该因为用户是从哪个链接进来的而变。
        path: 'feedback',
        name: 'AdminFeedback',
        component: () => import('@/views/feedback/AdminFeedbackPage.vue'),
        meta: { titleKey: 'navigation.admin.queue', isFullPage: true },
      },
      {
        path: 'members',
        name: 'AdminMembers',
        component: () => import('@/views/admin/AdminMembersPage.vue'),
        meta: { titleKey: 'navigation.admin.members', isFullPage: true },
      },
      {
        // 平台上唯一一处飞书应用凭据：管理员填一次，成员在「我的连接」里点一下就连上。
        // 它是**平台级**的设置（不属于任何一个项目），和上面几块一样是后台的一格。
        path: 'integrations',
        name: 'AdminIntegrations',
        component: () => import('@/views/admin/AdminIntegrationsPage.vue'),
        meta: { titleKey: 'navigation.admin.integrations', isFullPage: true },
      },
    ],
  },
] as RouteRecordRaw[]
