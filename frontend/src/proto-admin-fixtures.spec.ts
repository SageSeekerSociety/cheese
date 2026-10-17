/**
 * `@/proto-admin-fixtures` 这道出口的公开面。
 *
 * 夹具拆进 `./proto-admin-fixtures/` 之后，这道出口是唯一还必须和以前一字不差的
 * 东西：`proto-feedback-fixtures/routes.ts` 从它取 `adminRoutes`，转给预览那条通道。
 * 少重发一个名字、或者顺手多发出一个，真实构建一点事都没有 —— 坏的只有预览，
 * 而预览正是拿来看后台那几页长什么样的地方。所以这里钉的是「出口上有什么」，
 * 不是里面怎么分的。
 */
import { expect, it } from 'vitest'

import * as fixtures from '@/proto-admin-fixtures'

const api = (path: string) => new URL(`https://preview.invalid/api${path}`)

it('出口上只有 adminRoutes 一个值', () => {
  // 拆分之前这个模块也只导出这一个值。
  expect(Object.keys(fixtures).sort()).toEqual(['adminRoutes'])
})

it('出口那个 adminRoutes 背后仍是整张后台路由表', () => {
  // 最可能的坏法是「名字重发了、背后没接上」：入口上写着 adminRoutes，点开什么也不认。
  // 三页各取一条读路由：模型管理、棘轮那块看板、空间申请的列表 —— 分片之间接上了才认得
  // 全。每条都钉一个具体的值，不钉「有个数组」：夹具整个空掉也是数组，那样钉不住。
  expect(fixtures.adminRoutes('/admin/gateway/models', 'GET', api('/admin/gateway/models'))).toMatchObject({
    data: {
      models: expect.arrayContaining([expect.objectContaining({ name: 'claude-opus-5-5' })]),
      totals: expect.any(Object),
    },
  })
  expect(fixtures.adminRoutes('/admin/ratchet', 'GET', api('/admin/ratchet'))).toMatchObject({
    data: { repo: 'SageSeekerSociety/cheese', areas: expect.any(Array), points: 12 },
  })
  expect(fixtures.adminRoutes('/admin/spaces', 'GET', api('/admin/spaces?status=APPROVED'))).toMatchObject({
    data: { items: [expect.objectContaining({ id: 38, owner: 'pengwenbo', reviewStatus: 'APPROVED' })] },
  })
})

it('认不出来的路由交回给反馈那份假数据', () => {
  // 走到 `featureReport` 的必须是这里不认的那几条：它认得
  // `/admin/feature-stats/task-naming` 和 `.../docs-assistant`（见
  // `proto-feature-fixtures.ts`），而这个路由表自己那条只认不带尾巴的
  // `/admin/feature-stats`。所以取一条带尾巴的：这一条回来有内容，就说明交回去了；
  // 两处都不认得的回 undefined。
  expect(
    fixtures.adminRoutes('/admin/feature-stats/task-naming', 'GET', api('/admin/feature-stats/task-naming'))
  ).toMatchObject({ data: { id: 'task-naming', numbers: expect.any(Object) } })
  expect(fixtures.adminRoutes('/no-such-route', 'GET', api('/no-such-route'))).toBeUndefined()
})
