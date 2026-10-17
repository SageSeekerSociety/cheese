// 发题时能当导师的人（#3830）：出题人自己，加上这个空间的管理员。名字照各自的昵称。
import type { SpaceAdmin } from '@/types/spaces'

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
  for (const admin of admins ?? []) add(admin.user.username, admin.user.nickname)
  return out
}
