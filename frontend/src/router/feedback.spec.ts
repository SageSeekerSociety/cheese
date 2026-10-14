/**
 * `/admin` 路由表上两件容易悄悄失效的事。
 *
 * 一是改道守卫（`adminSectionGuard`）：进了后台却落在一块**自己进不去**的分区上时，
 * 改道这件事原先藏在 `AdminLayout` 的 `watch` 里静悄悄 `router.replace` —— 地址栏
 * 自己变了，看不出是「按权限改道」。现在它是路由表里一条声明式的 `beforeEnter`。
 *
 * 二是队列那条老地址：`/admin/feedback` 今天和 `/admin/queue` 是同一页，靠的是路由表
 * 里两条记录各自指向同一个组件，不是重定向、也不是别名（理由见 `router/feedback.ts`
 * 的文件头）。这件事没有别的守卫 —— 谁把其中一条改成重定向、或者合成一条 `alias`，
 * 页面照样长得出来，只有别人贴的老链接会先跳一下或者换个名字。
 *
 * 这里直接打**产品用的那些值**（`router/feedback.ts` 导出的函数和路由表），不是重写
 * 一份判据 —— 抄一份到测试里，测的就是抄本。
 *
 * 两份名单：`is_admin` 是反馈管理员（只看得见队列），`is_platform_admin` 是平台管理员
 * （看得见其余各块）。清单与顺序在 `@/lib/adminSections`。
 */
import type { RouteLocationNormalized } from 'vue-router'

import { createMemoryHistory, createRouter } from 'vue-router'
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

import routes, { adminSectionGuard } from './feedback'

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

describe('队列的老地址', () => {
  const router = createRouter({ history: createMemoryHistory(), routes })

  /** 一条记录最后那层（`/admin` 的子记录）身上挂的组件加载函数。 */
  function loaderOf(name: string) {
    const record = router.resolve({ name }).matched.at(-1)!
    return record.components?.default as unknown as () => Promise<{ default: unknown }>
  }

  it('`/admin/feedback` 还在，而且和 `/admin/queue` 指到同一页', async () => {
    const old = router.resolve({ name: 'AdminFeedback' })
    expect(old.path).toBe('/admin/feedback')
    // 地址本身还得认路：`/admin/feedback` 一旦落下，看到的人会以为是「后台坏了」。
    expect(router.resolve('/admin/feedback').name).toBe('AdminFeedback')
    expect(router.resolve('/admin/feedback').matched.length).toBeGreaterThan(0)

    // **不是别名**：别名不会替这条记录生成 name，而 `AdminFeedback` 这个名字侧栏、守卫
    // 和别处的测试都在用。所以这里比的是两条记录各自解析出来的那个组件。
    const queueLoader = loaderOf('AdminQueue')
    const oldLoader = loaderOf('AdminFeedback')
    expect(oldLoader, '两条记录共用了一个加载函数，那说明它们被合成一条了').not.toBe(queueLoader)
    const [queueModule, oldModule] = await Promise.all([queueLoader(), oldLoader()])
    expect(oldModule.default).toBe(queueModule.default)

    // 顶栏那行字也不该因为人是从哪个链接进来的而变。
    const titleOf = (name: string) => router.resolve({ name }).meta.titleKey
    expect(titleOf('AdminFeedback')).toBe(titleOf('AdminQueue'))

    // **不是重定向**：重定向会把地址栏里那个旧地址悄悄换掉，而人正在这一页上分诊时
    // 地址栏自己变一次是没必要的惊吓。
    expect(router.resolve({ name: 'AdminFeedback' }).matched.at(-1)!.redirect).toBeUndefined()
  })
})
