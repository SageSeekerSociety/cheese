import type { Topic, User } from '.'

export type Space = {
  id: number
  intro: string
  name: string
  avatarId: number
  admins: SpaceAdmin[]
  taskTemplates: string
  classificationTopics: Topic[]
  defaultCategoryId?: number
  visibleTaskLimit?: number | null
  /** 过审状态：没过审的板子，子资源（分类/题目/成员）一律读不到。 */
  reviewStatus?: 'PENDING' | 'APPROVED' | 'REJECTED'
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
  /** 本周讲到的内容，用管理员自己的话写。 */
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
  /**
   * 加入方式：这个人当初是靠哪张码进来的。
   *
   * `null`（老接口里则是缺这一格）是**「没有记录」**，不是「没用过码」：
   * 加这一格之前进来的成员谁都没记过，所有者直接加进来的人本来就没用码，而成员行
   * 分不开这两种。所以界面一律说「未知」，绝不替它认领一张码 —— 见
   * `views/spaces/board/pages/Members.vue`。
   *
   * 可选，因为它比这个类型新；缺了按 `null` 读。
   */
  inviteCode?: { id: number; code: string } | null
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
  /**
   * 这张码给谁 / 干什么用，建码人自己写的一句话（「十月这批同学」）。
   * `null` = 他什么都没写，包括每一张在这一格存在之前建的码 —— 那不是「未知」，
   * 是「本来就没有」，所以界面说的是「没写说明」而不是「未知」。
   * 可选，因为它比这个类型新；缺了按 `null` 读。
   */
  note?: string | null
  /**
   * 谁建的这张码。**没有这一格时不要显示「未知」以外的东西** —— `created_by`
   * 可空，一个没有 actor 的调用者建的码会留着空，界面那行就写「未知」。
   * 形状与成员行的 `user` 同一个（同一次批量查询给的）。可选，同上。
   */
  createdBy?: {
    id: number
    username: string
    nickname?: string
    avatarId?: number | null
    intro?: string
  } | null
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

/** One 公告 — see `SpacesApi.listAnnouncements`. */
export type SpaceAnnouncement = {
  id: number
  spaceId: number
  title: string
  /** Rich text (the editor's HTML). */
  content: string
  pinned: boolean
  /** Epoch ms from which it is 已到期; null = it does not expire. */
  expiresAt: number | null
  createdAt: number
  /** Moves when the title, body or expiry change — not on pinning. Later than `createdAt` means 已编辑. */
  updatedAt: number
  /** Null only for an announcement carried over without a known publisher. */
  author: { id: number; username: string; nickname: string; avatarId: number | null } | null
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
