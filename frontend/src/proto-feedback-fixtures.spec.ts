/**
 * `@/proto-feedback-fixtures` 这道出口的公开面。
 *
 * 夹具拆进 `./proto-feedback-fixtures/` 之后，这道出口是唯一还必须和以前一字不差的
 * 东西：预览构建和 `proto-preview-transport.ts` 都从它取 `routes`。少重发一个名字、
 * 或者顺手多发出一个，真实构建一点事都没有 —— 坏的只有预览，而预览正是拿来看事情
 * 长什么样的地方。所以这里钉的是「出口上有什么」，不是里面怎么分的。
 */
import type { MockReply } from '@/proto-feedback-fixtures'

import { expect, it } from 'vitest'

import * as fixtures from '@/proto-feedback-fixtures'

const api = (path: string) => new URL(`https://preview.invalid/api${path}`)

it('出口上只有 routes 一个值', () => {
  // 拆分之前这个模块也只导出这一个值（另一个是类型 `MockReply`，运行时看不见）。
  expect(Object.keys(fixtures).sort()).toEqual(['routes'])
})

it('出口那个 routes 背后仍是整张假后端的路由表', () => {
  // 最可能的坏法是「名字重发了、背后没接上」：入口上写着 routes，点开什么也不认。
  // 一条读路由、一条带参数的读路由、一条写路由都认得住，说明分片之间是接上的。
  expect(fixtures.routes(api('/feedback?tab=all'), 'GET', null)).toMatchObject({
    data: { data: expect.any(Array), total: expect.any(Number) },
  })
  expect(fixtures.routes(api('/feedback/meta'), 'GET', null)).toMatchObject({
    data: { kinds: ['bug', 'suggestion', 'other'] },
  })
  expect(fixtures.routes(api('/feedback/nope-and-missing'), 'GET', null)).toEqual({ data: undefined })
})

it('MockReply 也还从出口出去，并且就是 routes 的返回类型', () => {
  // 类型没重发的话这个注解编译不过。运行时那两种形状分得开：一无所知的路由回空，
  // 而路由表认得的写操作回一条数据。
  const nowhere: MockReply = fixtures.routes(api('/no-such-route'), 'GET', null)
  expect(nowhere).toBeUndefined()
  const deleted: MockReply = fixtures.routes(api('/feedback/does-not-exist'), 'DELETE', null)
  expect(deleted).toEqual({ missing: true })
})
