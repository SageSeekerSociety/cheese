// 画「这是谁」要的那几样，按 handle 索引：显示名、自己挑过的头像素材 id、是不是队友。
//
// 为什么单开一张表、由名册在手的地方构造好往下传，而不是让每个画人的组件只拿一个
// `memberNames`（handle → 名字）再自己想办法：名字之外的几样（头像 id、队友身份）名册里
// 本来就有，只传名字就把它们丢了，各处只好**拿名字反查**——反查迟早猜错（换过命名规则的
// 那天、名册那一行背后没有账号的人），而名册才是真源。
//
// 这里只做「名册行 → 画人要的四样」这一件事，不认接口、不认路由，纯函数，好单测。
import type { ProjectMemberRow } from '@/cx_types'

import { memberName } from '@/lib/agentNames'

export interface MemberFace {
  /** 显示名。名册没有名字时退回 handle。 */
  name: string
  /** 自己挑过的头像素材 id；null = 没挑过，画彩色首字母。 */
  avatarId: number | null
  /** 是不是 AI 队友：名册说了算；名册那一行没这个字段时 null（未知），交给视图按 handle 兜底。 */
  agent: boolean | null
}

export type MemberFaces = Record<string, MemberFace>

export function memberFacesOf(members: readonly ProjectMemberRow[]): MemberFaces {
  const faces: MemberFaces = {}
  for (const m of members) {
    faces[m.user_handle] = {
      name: memberName(m) || m.user_handle,
      avatarId: m.avatar_id ?? null,
      agent: m.agent ?? null,
    }
  }
  return faces
}
