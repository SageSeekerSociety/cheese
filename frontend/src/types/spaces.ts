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
  /**
   * 这块板的默认「给 AI 队友的指导」（#944）。四级继承的最外层，与
   * `SpaceCategory.teaching` 同形状；`{}` = 没说。
   */
  teaching?: SpaceTeaching
}

/**
 * 「给 AI 队友的指导」（`Space.teaching` / `SpaceCategory.teaching` / 题目覆盖；
 * 服务端见 `backend/app/domain/task/teaching.py`）。四级继承里的同一份形状：
 * 空间 → 项目集 → 题目 → 项目，整份替换、不深合 —— 某一层留空就是「没说」，
 * 下一级原样说话。
 *
 * 每次新建会话时现读：改周次之后**新会话**立刻带上，**跑着的会话**保持它启动
 * 时那一份，要冷启动才换。
 */
export type SpaceTeaching = {
  /** system prompt 模板，`{current_week}` 等占位会被当前的值替换。 */
  systemPrompt?: string | null
  /** 这是第几周。 */
  currentWeek?: number | null
  /** 目前的内容范围，用管理员自己的话写。 */
  allowedTopics?: string[]
  /** 暂时不该用到的写法 —— 解法这周不该依赖的构造。 */
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
  /** 项目集级的「给 AI 队友的指导」；没设的分类是 `{}`。 */
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

/**
 * 一块题目板的共用资料库里的一个条目（`SpacesApi.listMaterials`）。
 *
 * **没有 `url`，这是刻意的。** 素材的 `url` 是 `/uploads/…` 下一条公开可猜的
 * 路径 —— 交出去，「仅管理员」这一档就只剩一个标签。要字节走
 * `SpacesApi.downloadMaterial` 那条判权限的路由。
 */
export type SpaceMaterial = {
  id: number
  name: string
  /** `image` / `video` / `audio` / `file`。服务端按 MIME 判，不是人挑的。 */
  type: string
  visibility: SpaceMaterialVisibility
  /** 字节数；元数据里没这一格就是 `null`，不是 0。 */
  size: number | null
  mime: string | null
  uploaderId: number | null
  createdAt: number
  downloadCount: number
}

/** 谁能看见这一份。两档，没有第三档。 */
export type SpaceMaterialVisibility = 'members' | 'admins'

/**
 * 资料库清单现在到哪一步了。
 *
 * `error` 与「这一份都没有」是两件事：读不出来的时候不能把指导里已经引用的编号当成
 * 失效的 —— 那等于让人凭一次网络失败删掉有效的引用。
 */
export type SpaceMaterialsState = 'loading' | 'ready' | 'error'
