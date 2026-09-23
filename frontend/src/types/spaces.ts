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
  /**
   * 这门课开着哪几个模块。**缺省是开着**：没写的键 = 该模块显示，所以 `{}` 就是
   * 一间全都露出来的课，老题目板也自然落在这一档；存下来的只有**偏离缺省**的那
   * 几格（键表在 `lib/courseNav.ts` 的 `COURSE_MODULES`）。
   *
   * 它只决定界面露出哪几格，**不决定能力** —— 关掉一个模块是把它从侧栏与课程
   * 首页收起来，地址仍然打得开、接口照样答话。
   */
  courseModules?: Record<string, boolean>
}

/**
 * 课程级教学配置（`SpaceCategory.teaching`；服务端见
 * `backend/app/domain/task/teaching.py`）。它存在**分组**上 —— 一门课一份教学
 * 安排，二十道题共享 —— 再继承给从这门课建的项目。
 *
 * 每次新建会话时现读：改周次之后**新会话**立刻带上，**跑着的会话**保持它启动
 * 时那一份，要冷启动才换。
 */
export type SpaceTeaching = {
  /** 课程级 system prompt 模板，`{current_week}` 等占位会被本周的值替换。 */
  systemPrompt?: string | null
  /** 这是这门课的第几周。 */
  currentWeek?: number | null
  /** 本周讲到的内容，用老师自己的话写。 */
  allowedTopics?: string[]
  /** 这门课还没教到的东西 —— 解法这周不该依赖的构造。 */
  avoidInCode?: string[]
  /** 课件 / 知识的**引用**（id），不是副本。 */
  materialIds?: number[]
  knowledgeIds?: number[]
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
  /** 课程级教学配置；不是课的板子是 `{}`。 */
  teaching?: SpaceTeaching
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
