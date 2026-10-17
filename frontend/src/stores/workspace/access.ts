import type { ComputedRef } from 'vue'
import type { ProjectRefusal } from '@/lib/addresses'

import { computed, ref, watch } from 'vue'

import { ApiError, isProjectArchivedError } from '@/api'

/** 这个项目为什么打不开：没登录、没权限、被归档了；都没有就是 null。 */
export type ProjectDenied = 'unauthenticated' | 'forbidden' | 'archived' | null

/** 上面两档再加上「地址里的短名换不出项目」那一档——同一个门，四种说法。 */
export type ProjectAccess = 'unauthenticated' | 'forbidden' | 'missing' | 'archived' | null

export interface AccessDeps {
  /** 跟着登录的人变：换了人，上一份「进不来」的判断就不算数了。 */
  me: ComputedRef<string>
}

/**
 * 「这个项目打不开」这一整块：一条会自己消失的全局红条（`error`），和一条不会自己
 * 消失的说明（`denied` → `accessDenied`）。
 *
 * 两档分开，因为下一步动作不一样：没登录的人要去登录，登录了的人得去要权限。
 */
export function useAccess({ me }: AccessDeps) {
  // 地址里的项目没换成 UUID，守卫已经问过服务端为什么（`projectRefusal`）：不再拿短名去
  // 请求，那只会被当成参数不合法挡回来，侧栏上露出一句「请求参数不合法」。
  const refused = ref<ProjectRefusal | null>(null)
  const error = ref<string | null>(null)

  function reportError(e: unknown, fallback: string) {
    // 项目在这期间被所有者归档了：那不是一次失败，是这个项目换了状态，整块换成说明。
    if (isProjectArchivedError(e)) {
      denied.value = 'archived'
      return
    }
    error.value = e instanceof Error ? e.message : fallback
  }

  // 非成员打开项目链接时，话题列表 401/403，而红条弹 4 秒就没了：之后页面上没有
  // 任何解释，话题列表空白、项目名也不显示。把这四种说法记成 `denied`，侧栏就能一直
  // 说明白为什么打不开。
  const denied = ref<ProjectDenied>(null)

  // 换了人（或者从没登录变成登录了）：上一个身份进不来，不代表这一个也进不来。
  watch(me, () => {
    denied.value = null
  })

  const accessDenied = computed<ProjectAccess>(() => refused.value ?? denied.value)

  function noteAccess(e: unknown) {
    if (!(e instanceof ApiError)) return false
    if (e.status === 401) denied.value = 'unauthenticated'
    else if (e.status === 403) denied.value = 'forbidden'
    else return false
    return true
  }

  return { refused, denied, error, reportError, noteAccess, accessDenied }
}
