/** User-shaped API rows keep the public nickname separate from a viewer's label. */
export interface UserNameFields {
  nickname?: string | null
  username?: string | null
  remark?: string | null
}

export function userOriginalName(user: UserNameFields | null | undefined): string {
  return user?.nickname || user?.username || ''
}

export function userName(user: UserNameFields | null | undefined): string {
  return user?.remark || userOriginalName(user)
}
