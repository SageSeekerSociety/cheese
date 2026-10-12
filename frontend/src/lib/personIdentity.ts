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
