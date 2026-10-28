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
  /** 被驳回时审核人写的原因。 */
  reviewReason?: string | null
  /** 这块板的「AI 队友辅导」：题目上的设置和它同时生效。全空 = 没说。 */
  teaching?: SpaceTeaching
}

/** AI 队友怎么帮学生：引导为主、讲解与示例、先明确需求再实现、不加限制。 */
export type TeachingMode = 'guide' | 'example' | 'spec' | 'free'

/**
 * 「AI 队友辅导」（`Space.teaching` / 题目的那一份；服务端见
 * `backend/app/domain/task/protocol.py`，读写都是这几个 camelCase 键，读的那一侧在
 * `teaching_to_api`）。空间和题目两层同时生效：题目选了方式就用题目的，两边的补充
 * 要求、资料都算。项目自己的设置整份替换。
 *
 * 每次新建会话时现读：改了之后**新会话**立刻带上，**跑着的会话**保持它启动时那一份。
 */
export type SpaceTeaching = {
  /** 怎么帮；null = 这一层不说（题目沿用空间的）。 */
  mode?: TeachingMode | null
  /** 老师自己写的补充要求。 */
  systemPrompt?: string | null
  /**
   * 出题人的**私有**指导（#944 第二种可见性）：进 AI 队友的 prompt，但不进学生
   * 可读的响应 —— 服务端只在出题人/板管理员的读写上带这一格（`teaching_to_api`
   * 的 `includePrivate`）。所以它**可能缺席**：缺席 = 这条读看不到它，不是「没写」。
   */
  privatePrompt?: string | null
  /** 本节学习目标。写上它这份指导就按教育性走（要求留空时带默认教学要求）。 */
  learningGoals?: string[]
  /** 课件 / 知识的**引用**（id），不是副本。 */
  materialIds?: number[]
  /** 课程让 AI 队友读的团队文档（#3774）。 */
  documentIds?: string[]
  /** 项目里每个 AI 队友每一轮都带上的技能名。 */
  skills?: string[]
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
  /** 项目集级的「给 AI 队友的指导」；没设的分类六格全空。 */
  teaching?: SpaceTeaching
  /**
   * 项目集自己写的默认队友（#944）:平台预设名；`null` = 这一层没说（继承空间）。
   * 这是**原始覆盖值**，与提交时的「空串 = 清掉」不同。
   */
  defaultRole?: string | null
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
  /** 上传人的昵称；账号已经不在了是 `null`。 */
  uploaderName?: string | null
  createdAt: number
  downloadCount: number
  /**
   * 这块板里**还有几处教学配置列着它**。只有能管资料库的人拿得到这一格 ——
   * 成员那一侧连键都没有，不是 0。
   *
   * 数的是辅导设置里「列着」它的地方（空间一处，每道题各一处）。
   */
  usedByCount?: number
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

// ---- 「我发布的题目」（`SpacesApi.getMyPublishedTasks` 的形状）-----------------
//
// 这几个原来长在 `@/types/apiSpaces` 里；挪到领域类型这儿，是因为「题目列表」
// 拆出来的那半张画面（`views/spaces/detail/TasksView.vue`）只认 props、不连 API 层。

/** 我发布的题目挂在哪个分类下。 */
export type SpaceMyPublishedTaskCategory = {
  id: number
  name: string
}

/** 审到哪一步了。 */
export type AnalyticsApproveType = 'NONE' | 'APPROVED' | 'DISAPPROVED'

/** 这道题在别人眼里可不可见。 */
export type SpaceTaskVisibilityStatus =
  | 'PENDING_APPROVAL'
  | 'REJECTED'
  | 'APPROVED_HIDDEN'
  | 'APPROVED_VISIBLE'
  | 'ENDED'

/** 「我发布的」列表里的一行：出题人自己看的题目，带审核状态和几项计数。 */
export type SpaceMyPublishedTask = {
  taskId: number
  taskName: string
  /** 怎么开始：直接开始的题没有申请。 */
  startMode?: 'DIRECT' | 'APPLY'
  category: SpaceMyPublishedTaskCategory
  approved: AnalyticsApproveType
  visibilityStatus: SpaceTaskVisibilityStatus
  isVisible: boolean
  createdAt: number
  publishedAt?: number | null
  endedAt?: number | null
  deadline?: number | null
  /** 领取人数上限；`null` = 不限（真库用 0 表示不限，接口一律折成 null）。 */
  participantLimit: number | null
  participantCount: number
  approvedParticipantCount: number
  pendingParticipantApprovalCount: number
  submittedParticipantCount: number
  pendingReviewCount: number
  successfulParticipantCount: number
  failedParticipantCount: number
  submissionConversionRate: number
  successRate: number
  latestSubmissionAt?: number | null
}

/** `SpaceMyPublishedTask` 的一页。 */
export type SpaceMyPublishedTasks = {
  tasks: SpaceMyPublishedTask[]
}

// ---- 空间分析 · 告警（`SpacesApi.getAnalyticsAlerts` 的形状）--------------------
//
// 原来长在旧网络栈的空间类型里；挪到领域类型这儿，是因为「告警」那一格拆出来的半张画面
// （`views/spaces/detail/analytics/AlertsView.vue`）只认 props、不连 API 层。

/** 管理员看板「告警」那一格里的几个待办计数。 */
export type SpaceAnalyticsAlerts = {
  pendingTaskApprovalCount: number
  pendingParticipantApprovalCount: number
  pendingSubmissionReviewCount: number
  stalledTaskCount: number
  overdueUnreviewedSubmissionCount: number
  inactivePublisherCount: number
}

// ---- 空间申请（`SpacesApi.reviews` 的形状）-------------------------------------
//
// 原来长在旧网络栈的空间类型里；挪到领域类型这儿，是因为「空间申请」拆出来的那半张画面
// （`views/admin/AdminSpacesPageView.vue`）只认 props、不连 API 层。

/** 一条开版申请：谁、申请了什么版、什么时候、说了什么，以及审到哪一步。 */
export type SpaceApplication = {
  id: number
  avatarId?: number | null
  name: string
  intro: string
  reviewStatus: 'PENDING' | 'APPROVED' | 'REJECTED'
  description: string
  owner: string | null
  reviewReason: string | null
  reviewedBy: string | null
  reviewedAt: string | null
  createdAt: string
}

/**
 * 首页目录里一个空间那一行：id、名字、头像，以及还没过审时是哪一种。过审的空间点进去
 * 是它自己的目录；还在审核或被驳回的，点进去是这份申请。
 */
export type SpaceLink = {
  id: number
  name: string
  /** 素材 id；没挑过头像就是 null，那一行画按 id 派生的彩色首字母。 */
  avatarId?: number | null
  review?: 'PENDING' | 'REJECTED'
}
