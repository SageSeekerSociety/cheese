import type { Topic, User } from '.'

export type SpaceVisibilityType = 'PUBLIC' | 'CODE' | 'PRIVATE'

export type Space = {
  id: number
  intro: string
  name: string
  avatarId: number
  admins: SpaceAdmin[]
  announcements: string
  taskTemplates: string
  classificationTopics: Topic[]
  defaultCategoryId?: number
  visibleTaskLimit?: number | null
  /** 公开 / 凭码 / 私人. Absent only on payloads predating the tier. */
  visibility?: SpaceVisibilityType
}

/** One person the space is visible to — see `SpacesApi.listMembers`. */
export type SpaceMember = {
  userId: number
  joinedAt: number
  user?: {
    id: number
    username: string
    nickname?: string
    avatarId?: number | null
    intro?: string
  }
}

/**
 * A space's own invite code. Not the registration `InviteCode`: redeeming
 * this one puts you in the space, it does not create an account.
 */
export type SpaceInviteCode = {
  id: number
  spaceId: number
  code: string
  maxUses: number
  useCount: number
  expiresAt: number | null
  createdAt: number
}

export type SpaceCategory = {
  id: number
  name: string
  description: string | null
  displayOrder: number
  createdAt: number
  updatedAt: number
  archivedAt: number | null
}

export type SpaceAnnouncement = {
  title: string
  content: string
  createdAt: number
  updatedAt: number
  publisher: string
}

export type SpaceTaskTemplate = {
  name: string
  description: string
  title: string
  content: string
  submitterType?: 'USER' | 'TEAM' | null
  rank?: number | null
  minTeamSize?: number
  maxTeamSize?: number
  defaultDeadline?: number | null
  requireRealName?: boolean | null
}

export type SpaceAdmin = {
  role: SpaceAdminRoleType
  user: User
}

export type SpaceAdminRoleType = 'OWNER' | 'ADMIN'

export type DomainGroup = {
  id: number
  spaceId: number
  name: string
  description: string | null
  domains: string[]
  createdAt: number
  updatedAt: number
}
