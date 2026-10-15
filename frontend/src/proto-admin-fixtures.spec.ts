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
  // 三页各取一条：模型管理的读路由、棘轮那块看板、空间申请的写路由 —— 分片之间接上了
  // 才认得全。认不出来的路由仍然交回给反馈那份假数据。
  expect(fixtures.adminRoutes('/admin/gateway/models', 'GET', api('/admin/gateway/models'))).toMatchObject({
    data: { models: expect.any(Array), totals: expect.any(Object) },
  })
  expect(fixtures.adminRoutes('/admin/ratchet', 'GET', api('/admin/ratchet'))).toMatchObject({
    data: { repo: 'SageSeekerSociety/cheese', areas: expect.any(Array), points: 12 },
  })
})

it('认不出来的路由交回给反馈那份假数据', () => {
  // `featureReport` 认得的路径回一块数据，两处都不认得的回 undefined。
  expect(fixtures.adminRoutes('/admin/feature-stats', 'GET', api('/admin/feature-stats'))).toMatchObject({
    data: { features: expect.any(Array) },
  })
  expect(fixtures.adminRoutes('/no-such-route', 'GET', api('/no-such-route'))).toBeUndefined()
})
