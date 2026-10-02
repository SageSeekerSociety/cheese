import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import { useFeedbackStore } from '@/stores/feedback'

// 管理后台有哪几块、这个人看得见哪几块、现在停在哪一块。侧栏（`AdminSidebar`）画这份
// 清单，外壳（`AdminLayout`）按它把人领到第一块能进的分区上；两处读同一份，判据就只有
// 一处。
//
// 清单是**写死的**，不从路由表算：路由表里还有 `/feedback/*` 三条用户侧的页，按路由表
// 算会把它们画进后台的导航里。
//
// 「我是不是管理员」由服务端答（`GET /feedback/meta`），而且是两份名单：`is_admin` 是
// 反馈管理员（队列那一块），`is_platform_admin` 是平台管理员（其余各块）。两份互不包含，
// 哪一份都能进后台，各自只看见自己那几块。

export interface AdminSection {
  to: string
  name: string
  icon: string
  /** 写成函数：`t()` 要在读的那一刻取当前语言。键逐字写全、不拼 —— i18n 闸门
   *  （`src/i18n/catalog.spec.ts`）照源码字面量认「这个键有人用」。 */
  label: () => string
  /** 这一项旁边挂未读数（只有队列）。 */
  badge: boolean
}

export function useAdminSections() {
  const store = useFeedbackStore()
  const route = useRoute()
  const { t } = useI18n()

  // 顺序：每天要看的队列在最前；看数的三块（看板、功能数据、棘轮）挨着；模型和看板看的
  // 是同一条链；飞书应用是只填一次的设置，排在最后。
  const sections: AdminSection[] = [
    {
      to: '/admin/queue',
      name: 'AdminQueue',
      icon: 'mdi-tray-full',
      label: () => t('navigation.admin.queue'),
      badge: true,
    },
    {
      to: '/admin/dashboard',
      name: 'AdminDashboard',
      icon: 'mdi-chart-line',
      label: () => t('navigation.admin.dashboard'),
      badge: false,
    },
    {
      to: '/admin/feature-stats',
      name: 'AdminFeatureStats',
      icon: 'mdi-chart-box-outline',
      label: () => t('navigation.admin.featureStats'),
      badge: false,
    },
    {
      to: '/admin/ratchet',
      name: 'AdminRatchet',
      icon: 'mdi-chart-timeline-variant',
      label: () => t('navigation.admin.ratchet'),
      badge: false,
    },
    {
      to: '/admin/models',
      name: 'AdminModels',
      icon: 'mdi-cube-outline',
      label: () => t('navigation.admin.models'),
      badge: false,
    },
    {
      to: '/admin/spaces',
      name: 'AdminSpaces',
      icon: 'mdi-check-decagram-outline',
      label: () => t('navigation.admin.spaces'),
      badge: false,
    },
    {
      to: '/admin/members',
      name: 'AdminMembers',
      icon: 'mdi-account-multiple-outline',
      label: () => t('navigation.admin.members'),
      badge: false,
    },
    {
      to: '/admin/integrations',
      name: 'AdminIntegrations',
      icon: 'mdi-connection',
      label: () => t('navigation.admin.integrations'),
      badge: false,
    },
  ]

  const isPlatformAdmin = computed(() => !!store.meta?.is_platform_admin)
  const canEnter = computed(() => store.isAdmin || isPlatformAdmin.value)
  /** 队列归反馈管理员，其余归平台管理员。 */
  const visibleSections = computed(() =>
    sections.filter((section) => (section.name === 'AdminQueue' ? store.isAdmin : isPlatformAdmin.value))
  )

  /** 当前停在哪一块。有两块各对两个路由名：`/admin/feedback` 是队列的旧地址，功能数据
   *  是「目录 + 每一页」—— 两个名字都算在同一项上，不然从老书签或从目录点进来时旁边
   *  一条都不亮。 */
  function isCurrent(name: string): boolean {
    if (name === 'AdminQueue') return route.name === 'AdminQueue' || route.name === 'AdminFeedback'
    if (name === 'AdminFeatureStats') return route.name === 'AdminFeatureStats' || route.name === 'AdminFeature'
    return route.name === name
  }

  return { visibleSections, canEnter, isCurrent }
}
