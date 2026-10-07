import type { FeedbackMeta } from '@/cx_types'

// 管理后台分区的**唯一一份**定义：有哪几块、什么顺序、每块的路由与图标。侧栏
// （AdminSidebar）、外壳（AdminLayout）和路由守卫（router/feedback.ts 里 `/admin` 的
// beforeEnter）都读这一份 —— 顺序、可见性各写一遍的话，迟早对不上。
//
// 「我是不是管理员」由服务端答（`GET /feedback/meta`），而且是**两份名单**：`is_admin`
// 是反馈管理员（队列那一块），`is_platform_admin` 是平台管理员（其余各块）。两份互不
// 包含，哪一份都能进后台，各自只看见自己那几块。
//
// 清单是**写死的**，不从路由表算：路由表里还有 `/feedback/*` 三条用户侧的页，按路由表
// 算会把它们画进后台的导航里。

export type AdminGroupKey = 'pending' | 'run' | 'delivery' | 'cost' | 'product' | 'settings'

export interface AdminGroupDef {
  key: AdminGroupKey
  /** i18n 键，字面量写全（理由同 `AdminSectionDef.labelKey`）。 */
  labelKey: string
}

export interface AdminSectionDef {
  /** 分区主路由的地址。 */
  to: string
  /** 分区名（= 主路由的 name）。 */
  name: string
  icon: string
  /** i18n 键。留成字面量字符串：i18n 闸门（`src/i18n/catalog.spec.ts`）照字面量认
   *  「这个键有人在用」，键逐字写全、不拼。 */
  labelKey: string
  /** 这一项旁边挂未读数（只有队列）。 */
  badge: boolean
  /** 归在侧栏哪一组。 */
  group: AdminGroupKey
}

// 侧栏按「来后台要干什么」分组，组的顺序就是这里的顺序：要人动手的事在最前，然后是平台
// 此刻正不正常、改动走到哪了、花了多少、产品做得怎么样，只填一次的设置排在最后。
export const ADMIN_GROUPS: AdminGroupDef[] = [
  { key: 'pending', labelKey: 'navigation.admin.group.pending' },
  { key: 'run', labelKey: 'navigation.admin.group.run' },
  { key: 'delivery', labelKey: 'navigation.admin.group.delivery' },
  { key: 'cost', labelKey: 'navigation.admin.group.cost' },
  { key: 'product', labelKey: 'navigation.admin.group.product' },
  { key: 'settings', labelKey: 'navigation.admin.group.settings' },
]

// 组内顺序就是这里的顺序；同一组的项写在一起。
export const ADMIN_SECTIONS: AdminSectionDef[] = [
  {
    to: '/admin/queue',
    name: 'AdminQueue',
    icon: 'mdi-tray-full',
    labelKey: 'navigation.admin.queue',
    badge: true,
    group: 'pending',
  },
  {
    to: '/admin/spaces',
    name: 'AdminSpaces',
    icon: 'mdi-check-decagram-outline',
    labelKey: 'navigation.admin.spaces',
    badge: false,
    group: 'pending',
  },
  {
    to: '/admin/overview',
    name: 'AdminOverview',
    icon: 'mdi-heart-pulse',
    labelKey: 'navigation.admin.overview',
    badge: false,
    group: 'run',
  },
  {
    to: '/admin/run-records',
    name: 'AdminRunRecords',
    icon: 'mdi-pulse',
    labelKey: 'navigation.admin.runRecords',
    badge: false,
    group: 'run',
  },
  {
    to: '/admin/performance',
    name: 'AdminPerformance',
    icon: 'mdi-speedometer',
    labelKey: 'navigation.admin.performance',
    badge: false,
    group: 'run',
  },
  {
    to: '/admin/pipeline',
    name: 'AdminPipeline',
    icon: 'mdi-source-merge',
    labelKey: 'navigation.admin.pipeline',
    badge: false,
    group: 'delivery',
  },
  {
    to: '/admin/ratchet',
    name: 'AdminRatchet',
    icon: 'mdi-chart-timeline-variant',
    labelKey: 'navigation.admin.ratchet',
    badge: false,
    group: 'delivery',
  },
  {
    to: '/admin/usage',
    name: 'AdminUsage',
    icon: 'mdi-chart-areaspline',
    labelKey: 'navigation.admin.usage',
    badge: false,
    group: 'cost',
  },
  {
    to: '/admin/models',
    name: 'AdminModels',
    icon: 'mdi-cube-outline',
    labelKey: 'navigation.admin.models',
    badge: false,
    group: 'cost',
  },
  {
    to: '/admin/credits',
    name: 'AdminCredits',
    icon: 'mdi-wallet-outline',
    labelKey: 'navigation.admin.credits',
    badge: false,
    group: 'cost',
  },
  {
    to: '/admin/product',
    name: 'AdminProduct',
    icon: 'mdi-chart-line',
    labelKey: 'navigation.admin.product',
    badge: false,
    group: 'product',
  },
  {
    to: '/admin/feedback-trends',
    name: 'AdminFeedbackTrends',
    icon: 'mdi-message-text-outline',
    labelKey: 'navigation.admin.feedbackTrends',
    badge: false,
    group: 'product',
  },
  {
    to: '/admin/feature-stats',
    name: 'AdminFeatureStats',
    icon: 'mdi-chart-box-outline',
    labelKey: 'navigation.admin.featureStats',
    badge: false,
    group: 'product',
  },
  {
    to: '/admin/members',
    name: 'AdminMembers',
    icon: 'mdi-account-multiple-outline',
    labelKey: 'navigation.admin.members',
    badge: false,
    group: 'settings',
  },
  {
    to: '/admin/integrations',
    name: 'AdminIntegrations',
    icon: 'mdi-connection',
    labelKey: 'navigation.admin.integrations',
    badge: false,
    group: 'settings',
  },
  {
    to: '/admin/integration-health',
    name: 'AdminIntegrationHealth',
    icon: 'mdi-lan-check',
    labelKey: 'navigation.admin.integrationHealth',
    badge: false,
    group: 'settings',
  },
]

/** 平台管理员进后台落在哪：平台总览（健康状态在那一页）。 */
export const PLATFORM_LANDING = '/admin/overview'

// 路由名 -> 分区名。两块各对两个路由名：`/admin/feedback` 是队列的旧地址，功能数据是
// 「目录 + 每一页」—— 两个名字都算在同一项上，不然从老书签或从目录点进来时旁边一条都不亮。
const ROUTE_TO_SECTION: Record<string, string> = Object.fromEntries([
  ...ADMIN_SECTIONS.map((section) => [section.name, section.name]),
  ['AdminFeedback', 'AdminQueue'],
  ['AdminFeature', 'AdminFeatureStats'],
])

/** 一个路由名属于哪一块分区；不是后台分区路由时 null。 */
export function adminSectionForRouteName(routeName: string | null | undefined): string | null {
  if (!routeName) return null
  return ROUTE_TO_SECTION[routeName] ?? null
}

/** 这一块分区归谁看得见：队列归反馈管理员，其余归平台管理员。 */
export function isAdminSectionVisible(name: string, meta: FeedbackMeta | null | undefined): boolean {
  return name === 'AdminQueue' ? !!meta?.is_admin : !!meta?.is_platform_admin
}

/** 两份名单任一份都能进后台。 */
export function canEnterAdmin(meta: FeedbackMeta | null | undefined): boolean {
  return !!meta?.is_admin || !!meta?.is_platform_admin
}

/** 这个人进后台该落在哪：平台管理员落平台总览，只有反馈权限的人落队列；一块都进不去时 null。 */
export function adminLandingFor(meta: FeedbackMeta | null | undefined): string | null {
  if (meta?.is_platform_admin) return PLATFORM_LANDING
  return ADMIN_SECTIONS.find((section) => isAdminSectionVisible(section.name, meta))?.to ?? null
}
