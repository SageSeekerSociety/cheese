// 发题时能当导师的人（#3830）：出题人自己，加上这个空间的管理员。名字有个人备注
// 用备注，否则照各自的昵称（和成员页、名册是同一条规矩）。
import type { SpaceAdmin } from '@/types/spaces'

import { userName } from './agentNames'

export interface MentorOption {
  title: string
  value: string
}

export function mentorOptions(
  admins: SpaceAdmin[] | undefined,
  me: { handle: string; name: string } | null
): MentorOption[] {
  const out: MentorOption[] = []
  const seen = new Set<string>()
  const add = (value: string, title: string) => {
    if (!value || seen.has(value)) return
    seen.add(value)
    out.push({ value, title: title || value })
  }
  if (me) add(me.handle, me.name)
  for (const admin of admins ?? []) add(admin.user.username, userName(admin.user))
  return out
}
