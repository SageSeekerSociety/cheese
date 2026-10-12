/**
 * 「这是谁」的最小一组：认人的 handle、显示名、自己挑过的头像素材 id、是不是 AI 队友。
 *
 * 名册的出口（频道名册的「管理者」）和跨层转发身份的地方都用它，别再只传一个名字：
 * 只传名字的话，拿到的人画不出头像、也分不出队友和人 —— 一处漏传，那一处就退回
 * 一个光秃秃的名字。头像素材 id 由接口回，URL 由 `utils/materials` 拼，这里只放 id。
 */
export interface PersonIdentity {
  /** 认人的唯一依据（名册上的 handle，点它等于 @ 它）。 */
  handle: string
  /** 显示名；名册不记名字时给 null，调用方退回 handle。 */
  name: string | null
  /** 这个人自己挑过的头像素材 id；null = 没挑过（画彩色首字母）。 */
  avatarId: number | null
  /** 是不是 AI 队友，照名册上说。 */
  agent: boolean
}

/**
 * 正文 chip 前那张脸要的两件事（`lib/refChip` 的 `RefNames.mentionIdentities` 就是
 * handle → 这个）。名字在上面那张表里，这里只管头像和「是不是队友」。
 */
export interface MentionIdentity {
  /** 头像地址；没挑过、或名册上没这一位时 null（画彩色首字母）。 */
  avatar: string | null
  /** 是不是 AI 队友（决定那张脸画圆的还是超椭圆）。 */
  agent: boolean
}

/**
 * 名册（或 `@` 候选那串人）→ `mentionIdentities` 那张表。名册在手的地方都从这一处
 * 建表，免得「正文里的人是哪张脸」在各层各写一遍、写着写着就不一样了。
 */
export function identitiesFrom(
  rows: { handle: string; avatar?: string | null; agent?: boolean }[]
): Record<string, MentionIdentity> {
  return Object.fromEntries(rows.map((r) => [r.handle, { avatar: r.avatar ?? null, agent: !!r.agent }]))
}
