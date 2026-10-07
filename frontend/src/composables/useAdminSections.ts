import type { AdminGroupKey } from '@/lib/adminSections'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import {
  ADMIN_GROUPS,
  ADMIN_SECTIONS,
  adminSectionForRouteName,
  canEnterAdmin,
  isAdminSectionVisible,
} from '@/lib/adminSections'
import { useFeedbackStore } from '@/stores/feedback'

// 管理后台有哪几块、分在哪几组、这个人看得见哪几块、现在停在哪一块。侧栏（`AdminSidebar`）
// 画这份清单。清单本身在 `@/lib/adminSections` —— 路由守卫（`router/feedback.ts` 里 `/admin`
// 的 `beforeEnter`）也读那一份，判据就只有一处。

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

export interface AdminSectionGroup {
  key: AdminGroupKey
  label: () => string
  sections: AdminSection[]
}

export function useAdminSections() {
  const store = useFeedbackStore()
  const route = useRoute()
  const { t } = useI18n()

  const sections = ADMIN_SECTIONS.map((section) => ({
    to: section.to,
    name: section.name,
    icon: section.icon,
    label: () => t(section.labelKey),
    badge: section.badge,
    group: section.group,
  }))

  const canEnter = computed(() => canEnterAdmin(store.meta))
  const visibleSections = computed(() => sections.filter((section) => isAdminSectionVisible(section.name, store.meta)))
  /** 侧栏按组画；这个人一项都看不见的组整组不画（只有反馈权限的人只剩「待处理」）。 */
  const visibleGroups = computed<AdminSectionGroup[]>(() =>
    ADMIN_GROUPS.map((group) => ({
      key: group.key,
      label: () => t(group.labelKey),
      sections: visibleSections.value.filter((section) => section.group === group.key),
    })).filter((group) => group.sections.length > 0)
  )

  /** 当前停在哪一块。路由名到分区的对应关系（含队列旧地址 `/admin/feedback`、功能数据的
   *  目录页）也在 `@/lib/adminSections` 里。 */
  function isCurrent(name: string): boolean {
    return adminSectionForRouteName(route.name as string | undefined) === name
  }

  return { visibleSections, visibleGroups, canEnter, isCurrent }
}
