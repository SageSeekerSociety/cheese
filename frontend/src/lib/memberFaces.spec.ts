/**
 * `memberFacesOf`：把名册几行折成「画人要的四样」，按 handle 索引。
 *
 * 这条函数是「往下传身份」的源头 —— 传错一处，头像/队友身份在各处一起错，所以在这里
 * 把边界钉住：名字退回 handle、没挑过头像给 null、队友身份照名册说（缺席是 null 未知）。
 */
import type { ProjectMemberRow } from '@/cx_types'

import { describe, expect, it } from 'vitest'

import { memberFacesOf } from './memberFaces'

function row(over: Partial<ProjectMemberRow>): ProjectMemberRow {
  return { user_handle: 'h', ...over }
}

describe('memberFacesOf', () => {
  it('名字、头像素材 id、队友身份各自从名册那一行取', () => {
    const faces = memberFacesOf([row({ user_handle: 'alice', name: '爱丽丝', avatar_id: 7, agent: false })])
    expect(faces.alice).toEqual({ name: '爱丽丝', avatarId: 7, agent: false })
  })

  it('没有名字时退回 handle', () => {
    const faces = memberFacesOf([row({ user_handle: 'bob' })])
    expect(faces.bob).toEqual({ name: 'bob', avatarId: null, agent: null })
  })

  it('没挑过头像给 null（画彩色首字母），不塞一个假 id', () => {
    const faces = memberFacesOf([row({ user_handle: 'carol', name: '卡罗尔', avatar_id: null })])
    expect(faces.carol.avatarId).toBeNull()
  })

  it('队友身份照名册：true 就是队友，缺席是 null 未知（不乱说是人）', () => {
    const faces = memberFacesOf([
      row({ user_handle: 'cheese-1', name: '芝士', agent: true }),
      row({ user_handle: 'dan' }),
    ])
    expect(faces['cheese-1'].agent).toBe(true)
    expect(faces.dan.agent).toBeNull()
  })
})
