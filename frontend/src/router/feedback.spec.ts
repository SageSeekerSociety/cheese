/**
 * `/admin` 的改道守卫（`adminSectionGuard`）。
 *
 * 这一组钉的是 #3 那条反馈的另一半：进了后台却落在一块**自己进不去**的分区上时，
 * 改道这件事原先藏在 `AdminLayout` 的 `watch` 里静悄悄 `router.replace` —— 地址栏
 * 自己变了，看不出是「按权限改道」。现在它是路由表里一条声明式的 `beforeEnter`。
 *
 * 这里直接打**产品用的那个函数**（`router/feedback.ts` 导出、`beforeEnter` 挂的就是
 * 它），不是重写一份判据 —— 抄一份到测试里，测的就是抄本。
 *
 * 两份名单：`is_admin` 是反馈管理员（只看得见队列），`is_platform_admin` 是平台管理员
 * （看得见其余各块）。清单与顺序在 `@/lib/adminSections`。
 */
import type { RouteLocationNormalized } from 'vue-router'

import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getFeedbackMeta = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getFeedbackMeta: (...a: unknown[]) => getFeedbackMeta(...a),
  }
})

import { adminSectionGuard } from './feedback'

import { useFeedbackStore } from '@/stores/feedback'

/** 只填守卫真的读的那两个字段：`name`（认是哪一块）和 `path`（回程要比）。 */
function to(name: string, path: string): RouteLocationNormalized {
  return { name, path } as RouteLocationNormalized
}

async function runGuard(meta: object, routeName: string, path: string) {
  getFeedbackMeta.mockResolvedValue(meta)
  setActivePinia(createPinia())
  // `metaChecked` 为假时守卫会自己 `loadMeta()`，所以这里不必先灌。
  const store = useFeedbackStore()
  expect(store.metaChecked).toBe(false)
  return adminSectionGuard(to(routeName, path))
}

beforeEach(() => {
  getFeedbackMeta.mockReset()
})

describe('adminSectionGuard', () => {
  it('只是平台管理员：去队列那块会被领到平台总览', async () => {
    const result = await runGuard({ is_admin: false, is_platform_admin: true }, 'AdminQueue', '/admin/queue')
    expect(result).toBe('/admin/overview')
  })

  it('只是平台管理员：队列的旧地址也一样会被领走', async () => {
    const result = await runGuard({ is_admin: false, is_platform_admin: true }, 'AdminFeedback', '/admin/feedback')
    expect(result).toBe('/admin/overview')
  })

  it('只是反馈管理员：落到平台总览（`/admin` 的默认去处）会被领回队列', async () => {
    const result = await runGuard({ is_admin: true, is_platform_admin: false }, 'AdminOverview', '/admin/overview')
    expect(result).toBe('/admin/queue')
  })

  it('只是反馈管理员：空间申请也归平台管理员，会被领回队列', async () => {
    const result = await runGuard({ is_admin: true, is_platform_admin: false }, 'AdminSpaces', '/admin/spaces')
    expect(result).toBe('/admin/queue')
  })

  it('只是反馈管理员：平台那几块（成员）会被领回队列', async () => {
    const result = await runGuard({ is_admin: true, is_platform_admin: false }, 'AdminMembers', '/admin/members')
    expect(result).toBe('/admin/queue')
  })

  it('只是反馈管理员：功能数据的某一页也算平台那块，会被领回队列', async () => {
    const result = await runGuard(
      { is_admin: true, is_platform_admin: false },
      'AdminFeature',
      '/admin/feature-stats/foo'
    )
    expect(result).toBe('/admin/queue')
  })

  it('走进得去的那一块时放行，不改地址', async () => {
    const result = await runGuard({ is_admin: true, is_platform_admin: false }, 'AdminQueue', '/admin/queue')
    expect(result).toBe(true)
  })

  it('两份名单都有：每一块都放行', async () => {
    const result = await runGuard({ is_admin: true, is_platform_admin: true }, 'AdminMembers', '/admin/members')
    expect(result).toBe(true)
  })

  it('两份名单都没有：不在守卫这里处理，交给门（返回 true）', async () => {
    const result = await runGuard({ is_admin: false, is_platform_admin: false }, 'AdminQueue', '/admin/queue')
    expect(result).toBe(true)
  })

  it('不是后台分区路由（名字对不上）时放行', async () => {
    const result = await runGuard({ is_admin: true, is_platform_admin: true }, 'FeedbackCenter', '/feedback')
    expect(result).toBe(true)
  })
})
