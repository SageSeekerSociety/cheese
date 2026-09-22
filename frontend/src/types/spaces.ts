import type { Topic, User } from '.'

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
  /** 过审状态：没过审的板子，子资源（分类/题目/成员）一律读不到。 */
  reviewStatus?: 'PENDING' | 'APPROVED' | 'REJECTED'
  /**
   * 这块题目板是不是一门课 —— 服务端按它默认分组声明的壳算（`catalog.py` 的
   * `is_course_shell`）。题目板自己的屏幕不是项目、读不到壳，所以它只能凭这个
   * 答复决定「进来看课程首页还是题目列表」。老题目板没有声明 → false。
   */
  isCourse?: boolean
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

/** 课程里的人：学生、他的项目、他的组（`SpacesApi.getCourseRoster`，教师可见）。 */
export type CourseRosterPerson = {
  id: number
  username: string
  nickname?: string
  avatarId?: number | null
  intro?: string
}

export type CourseRosterStudent = {
  user: CourseRosterPerson
  projects: { id: string; name: string; teamId: number | null }[]
  /** 他挂在哪几个组上。一组一项目，正常只有一个；没有组就是空数组。 */
  teamIds: number[]
}

export type CourseRosterTeam = {
  id: number
  name: string
  members: CourseRosterPerson[]
}

export type CourseRoster = {
  students: CourseRosterStudent[]
  teams: CourseRosterTeam[]
}

/** 学生自己那一行（`SpacesApi.getMyCourseGroup`）：我在哪个组、组里还有谁。 */
export type MyCourseGroup = {
  projectId: string | null
  team: { id: number; name: string; members: CourseRosterPerson[] } | null
}
