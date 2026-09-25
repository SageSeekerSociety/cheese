// 外部成员 = 团队以外、被邀请进这一个项目的人。项目名册上 `source === 'external'` 的
// 那几行就是他们；其余的人（所有者、团队成员）都是「自己人」。所有要标出「外部」的
// 地方都问这里，别各自去读 source。
import type { ProjectMemberRow } from '@/cx_types'

export function isExternalMember(row: Pick<ProjectMemberRow, 'source'> | null | undefined): boolean {
  return row?.source === 'external'
}

export function externalHandles(rows: readonly ProjectMemberRow[]): Set<string> {
  return new Set(rows.filter((r) => !r.agent && isExternalMember(r)).map((r) => r.user_handle))
}
