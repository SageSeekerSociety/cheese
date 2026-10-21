/** Where a friend who registered with someone's invite code has got to. */
export type InviteStage = 'registered' | 'onboarded' | 'activated' | 'lapsed'

export interface Invitee {
  handle: string
  nickname: string | null
  avatar_id: number | null
  registered_at: string
  stage: InviteStage
  /** Credits the inviter actually received for this friend: 0 when past the monthly cap. */
  credits: number
}

/** `GET /users/me/invites`: the person's own invite code, its uses, and who came in on it. */
export interface MyInvites {
  code: string
  max_uses: number
  use_count: number
  /** The owner's own first accepted delivery has added `unlock_uses` more uses. */
  unlocked: boolean
  unlock_uses: number
  invite_credits: number
  invitees: Invitee[]
}

/** One row of the platform admins' invite-code list (`GET /users/invite-codes`, camelCase on the wire). */
export interface AdminInviteCode {
  id: number
  code: string
  maxUses: number
  useCount: number
  isActive: boolean
  createdBy: number | null
  /** `personal` marks a person's own code, made by `/users/me/invites`. */
  note: string | null
  createdAt: string | null
  expiresAt: string | null
}

/** The note the backend writes on a person's own code. */
export const PERSONAL_INVITE_NOTE = 'personal'
