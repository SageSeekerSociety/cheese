import type { PersonIdentity } from './personIdentity'

/**
 * 「添加成员」那张候选名单上的一位：身份（见 `lib/personIdentity`）加一个「外部成员」
 * 标。候选只从项目名册来，所以身份之外只要这一样。
 */
export interface MemberCandidate extends PersonIdentity {
  /** 团队以外的外部成员（挂那个标）。 */
  external?: boolean
  /**
   * 名字下面那行显示的 @handle（`lib/agentNames` 的 `shownHandle`）：队友写它自己的
   * handle，不是座位编号。不给就写 `handle`。
   */
  sub?: string
}

/**
 * 搜索：名字或 handle 命中即可（大小写不敏感）；空词就是全部，顺序不动。
 * 名册上可能还没起名字（身份里的 `name` 是 null），那就只剩 handle 能对上——
 * 不给名字的那一位不该因此从搜索结果里被划掉，也不该让 `.toLowerCase()` 报空。
 */
export function filterCandidates(list: MemberCandidate[], query: string): MemberCandidate[] {
  const q = query.trim().toLowerCase()
  if (!q) return list
  return list.filter((c) => (c.name ?? '').toLowerCase().includes(q) || c.handle.toLowerCase().includes(q))
}

/** 勾上 / 取消一个人：已在选中里就去掉，不在就加到末尾（顺序按勾的先后）。 */
export function toggleCandidate(chosen: string[], handle: string): string[] {
  return chosen.includes(handle) ? chosen.filter((h) => h !== handle) : [...chosen, handle]
}
