export type User = {
  id: number
  username: string
  nickname: string
  avatarId: number
  intro: string
  follow_count: number
  fans_count: number
  question_count: number
  answer_count: number
  is_follow?: boolean
  has_real_name_info?: boolean
  // Only on the signed-in person's own record: the account has no address of
  // its own yet and must add one.
  emailMissing?: boolean
}

// 实名认证状态
export enum RealNameStatus {
  NONE = 'none',
  VERIFIED = 'verified',
  PENDING = 'pending',
  REJECTED = 'rejected',
}
