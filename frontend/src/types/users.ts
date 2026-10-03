export type User = {
  id: number
  username: string
  nickname: string
  avatarId: number
  intro: string
  question_count: number
  answer_count: number
  has_real_name_info?: boolean
  // Only on the signed-in person's own record: the account has no address of
  // its own yet and must add one.
  emailMissing?: boolean
  // Only on the signed-in person's own record: the UI language they picked,
  // null until they have; what their push notifications are written in.
  language?: string | null
}

// 实名认证状态
export enum RealNameStatus {
  NONE = 'none',
  VERIFIED = 'verified',
  PENDING = 'pending',
  REJECTED = 'rejected',
}
