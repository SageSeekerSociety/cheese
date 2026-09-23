import type { User } from '.'

export type TeamMemberRoleType = 'OWNER' | 'ADMIN' | 'MEMBER'

// 公开 = 能在「发现小队」里搜到；隐身 = 搜不到，只有拿到小队链接的人能找到并申请。
export type TeamVisibility = 'public' | 'stealth'

// 当前登录者和这个小队的关系：已加入 / 申请审批中 / 都不是。
export type TeamJoinStatus = 'member' | 'pending' | 'none'

export interface TeamMember {
  role: TeamMemberRoleType
  user: User
}

export interface Team {
  id: number
  // The team's address: `/teams/<handle>`. A personal team's is its owner's username.
  handle: string
  intro: string
  name: string
  avatarId: number
  // v4 个人 = 单人真团队: true for the user's auto-provisioned personal team.
  // It sorts first in my-teams and backs personal projects + personal compute.
  personal?: boolean
  // Present on authenticated detail/my-team responses.
  role?: TeamMemberRoleType
  visibility?: TeamVisibility
  // Present on a team read by id or through its join link.
  joinStatus?: TeamJoinStatus
  // true = 加入要 owner/admin 批准（申请）；false = 确认即加入。
  joinApproval?: boolean
  owner: User
  admins: {
    total: number
    examples: User[]
  }
  members: {
    total: number
    examples: User[]
  }
}

export type ApplicationType = 'REQUEST' | 'INVITATION'

export type ApplicationStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'ACCEPTED' | 'DECLINED' | 'CANCELED'

export interface TeamMemberRealNameStatus {
  memberId: number
  hasRealNameInfo: boolean
  userName: string
}

export interface TeamSummary {
  id: number
  handle: string
  name: string
  intro: string
  avatarId: number
  allMembersVerified?: boolean
  memberRealNameStatus?: TeamMemberRealNameStatus[]
  updatedAt: number
  createdAt: number
}

export interface TeamMembershipApplication {
  id: number
  user: User
  team: TeamSummary
  initiator: User
  type: ApplicationType
  status: ApplicationStatus
  role: TeamMemberRoleType
  message: string | null
  processedBy?: User
  processedAt?: number
  createdAt: number
  updatedAt: number
}

export interface TeamJoinRequestCreate {
  message?: string
}

export interface TeamInvitationCreate {
  userId: number
  role?: TeamMemberRoleType
  message?: string
}
