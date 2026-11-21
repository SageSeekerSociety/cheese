// 文件评论写不写得了：和文档评论同一条规矩（服务端 `file_comments._reached` 按角色的
// 「评论」、频道归档、项目归档三样拒）。前端照这三样决定摆不摆写的入口。
import { expect, it } from 'vitest'

import { mayCommentIn } from './projectPermissions'

const active = { status: 'active' }
const member = { my_permissions: ['speak', 'comment'], archived_at: null }

it('频道、项目都没归档，角色有「评论」：能写', () => {
  expect(mayCommentIn(active, member)).toBe(true)
})

it('角色没有「评论」：不能写', () => {
  expect(mayCommentIn(active, { my_permissions: ['speak'], archived_at: null })).toBe(false)
})

it('频道归档了：不能写', () => {
  expect(mayCommentIn({ status: 'archived' }, member)).toBe(false)
})

it('项目归档了：不能写', () => {
  expect(mayCommentIn(active, { ...member, archived_at: '2026-11-01T00:00:00Z' })).toBe(false)
})

it('还不知道是哪个频道或项目：不摆写的入口', () => {
  expect(mayCommentIn(null, member)).toBe(false)
  expect(mayCommentIn(active, undefined)).toBe(false)
})
