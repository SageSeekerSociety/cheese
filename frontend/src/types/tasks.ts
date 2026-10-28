import type { Material, Space, SpaceCategory, SpaceTeaching, Team, TeamSummary, Topic, User } from '.'

export type TaskSubmitterType = 'USER' | 'TEAM'

/** 怎么开始做：`DIRECT` 点「开始做这道题」就开始，所有人都按题目的截止时间交；
 *  `APPLY` 先申请，出题人通过后才开始，限时（`defaultDeadline` 天）从通过那天算起。
 *  从题目来的项目都在开始的那一刻建出来。 */
export type TaskStartMode = 'DIRECT' | 'APPLY'
export type TaskSubmissionEntryType = 'TEXT' | 'FILE'
export type TaskTeamMembershipLockPolicy = 'NO_LOCK' | 'LOCK_ON_APPROVAL'
export type EligibilityRejectReasonCode =
  | 'ALREADY_PARTICIPATING'
  | 'MEMBER_ALREADY_PARTICIPATING'
  | 'PARTICIPANT_LIMIT_REACHED'
  | 'TASK_NOT_APPROVED'
  | 'REGISTRATION_NOT_STARTED'
  | 'MISSING_REAL_NAME'
  | 'TEAM_TOO_SMALL'
  | 'TEAM_TOO_LARGE'
  | 'DEADLINE_PASSED'
  | 'USER_NOT_FOUND'
  | 'USER_ACCOUNT_ISSUE'
  | 'USER_MISSING_REAL_NAME'
  | 'USER_RANK_NOT_HIGH_ENOUGH'
  | 'TEAM_NOT_FOUND'
  | 'TEAM_SIZE_MIN_NOT_MET'
  | 'TEAM_SIZE_MAX_EXCEEDED'
  | 'TEAM_MISSING_REQUIRED_INFO'
  | 'TEAM_MEMBER_MISSING_REAL_NAME'
  | 'TEAM_MEMBERS_NOT_VERIFIED'
  | 'TEAM_MEMBER_RANK_NOT_HIGH_ENOUGH'
  | 'INDIVIDUAL_PARTICIPATION_NOT_ALLOWED'
  | 'TEAM_PARTICIPATION_NOT_ALLOWED'
  | 'UNKNOWN'

export interface EligibilityRejectReasonInfo {
  code: EligibilityRejectReasonCode
  message: string
  details?: Record<string, any>
}

export interface EligibilityStatus {
  eligible: boolean
  reasons?: EligibilityRejectReasonInfo[]
}

export interface TeamTaskEligibility {
  team: TeamSummary
  eligibility: EligibilityStatus
}

export interface ParticipationEligibility {
  user?: EligibilityStatus
  teams?: TeamTaskEligibility[]
}

export interface TaskSubmissionSchemaEntry {
  prompt: string
  type: TaskSubmissionEntryType
  /** 属于哪个阶段：阶段的 id；不分阶段的题是 null。 */
  stage?: string | null
}

/** 评分要点的一行：评什么、多少分、怎样算做到（`app/domain/task/rubric.py`）。 */
export interface TaskRubricLine {
  item: string
  points: number
  criteria: string
  /** 评的是哪个阶段的提交：阶段的 id；不分阶段的题是 null。 */
  stage?: string | null
}

/** 题目的一个阶段（`app/domain/task/stages.py`）：名称、要做到什么、截止时间。 */
export interface TaskStage {
  /** 阶段的 id，调整先后也不变：提交项、评分要点和已交的提交都按它指这个阶段。新加的
   *  阶段由表单当场给一个（`lib/taskStages.ts`）。 */
  id?: string | null
  name: string
  description: string
  /** 毫秒时间戳；`null` = 这个阶段不设截止。 */
  dueAt: number | null
}

/** 一个阶段走到哪一步（`app/domain/task/submission_state.py` 的 STAGE_*）。`MISSED`
 *  是过了截止、一版也没交，这条领取因它未通过：领取自己的状态和出题人看的人数里都有。 */
export type TaskStageStatus = 'NOT_SUBMITTED' | 'PENDING_REVIEW' | 'PASSED' | 'REJECTED' | 'MISSED'

/** `GET /tasks/{id}/progress`：每个阶段上的进度。不分阶段的题算一个阶段。 */
export interface TaskProgress {
  stages: TaskStage[]
  /** 我那条获批的领取在每个阶段上的状态；没领到是 `null`。 */
  mine: { participantId: number; stages: TaskStageStatus[] } | null
  /** 批改的人才有：每个阶段上各状态的人数。 */
  distribution: Partial<Record<TaskStageStatus, number>>[] | null
}

/** 我在一道题上走到哪一步：没交、交了等判、通过、退回可重交。
 *
 *  由后端按**提交与评审**算（`app/domain/task/submission_state.py`），不读
 *  `TaskMembership.completion_status` —— 那一列除了领取与逾期两处没人写，交过作业
 *  的人至今读作「没交」。 */
export type TaskClaimStatus = 'IN_PROGRESS' | 'SUBMITTED' | 'PASSED' | 'REJECTED'

export interface Task {
  /** 加入每个领取者项目的导师（#3830）；没有就是空。 */
  mentors?: { handle: string; nickname: string }[]
  id: number
  name: string
  intro: string
  submitterType: TaskSubmitterType
  creator: User
  registrationStartAt?: number
  deadline: number | null
  participantLimit: number
  defaultDeadline: number
  startMode?: TaskStartMode
  resubmittable: boolean
  editable: boolean
  description: string
  space?: Space
  category?: SpaceCategory
  submissionSchema: TaskSubmissionSchemaEntry[]
  /** 评分要点。批改的人总能读到；学生只在 `rubricPublic` 时读到，否则是空的。 */
  rubric?: TaskRubricLine[]
  rubricPublic?: boolean
  /** 阶段；不分阶段是空的。详情与改题的响应才带。 */
  stages?: TaskStage[]
  participants: {
    /** 没删的申请份数，等批准的和被拒的都算。 */
    total: number
    /** 申请通过的份数：页面上的「参与人数」，名额满没满也按它。 */
    approved: number
    /** 还算数的份数（通过的和等批准的）：有一份，开始方式就不能再改。 */
    live?: number
    examples: TaskParticipantSummary[]
  }
  submittable: boolean
  submittableAsTeam?: Team[]
  createdAt: number
  updatedAt: number
  publishedAt?: number | null
  endedAt?: number | null
  /** 审核人（`user.id`）。这一列是后加的，老题与还没审过的题都是 null。 */
  reviewedBy?: number | null
  /** 审核那一刻的毫秒时间戳。**不是 `updatedAt`** —— 改标题这类编辑不写它。 */
  reviewedAt?: number | null
  rank: number
  approved: 'APPROVED' | 'DISAPPROVED' | 'NONE'
  rejectReason?: string
  requireRealName: boolean
  minTeamSize?: number
  maxTeamSize?: number
  teamLockingPolicy?: TaskTeamMembershipLockPolicy
  accessControlEnabled?: boolean
  accessDomainGroupIds?: number[]
  joined?: boolean
  joinedTeams?: Team[]
  topics?: Topic[]
  userDeadline?: number
  participationEligibility?: ParticipationEligibility
  /** 我（或我的团队）从这道题来的那个项目：题目页的「继续做」打开它。详情接口才带；
   *  还没开始（申请在等通过、被拒了、没申请）是 null。 */
  myProject?: { id: string; name: string } | null
  /** 这道题挂着几份材料。列表接口一并给（只增不改的那个数，不带文件本体）。 */
  attachmentCount?: number
  /** 我这条领取的档位。`null`/缺省 = 我没领这道题。随 `queryJoined` 一族回来。 */
  myClaimStatus?: TaskClaimStatus | null
  /**
   * 这道题自己那份「给 AI 队友的指导」覆盖（#944）。四级继承里题目那一层，整份
   * 替换、不深合；`{}` = 没说，下面（项目集）或上面（空间）的默认原样生效。
   */
  teaching?: SpaceTeaching
  /**
   * 这道题自己写下的默认队友（#944）：平台预设的名字；`null` = 这道题没写过（继承
   * 项目集 / 空间）。这是**原始覆盖值**，与提交时的「空串 = 清掉」不同。
   */
  defaultRole?: string | null
}

export interface TaskSubmissionReview {
  reviewed: boolean
  detail: {
    accepted: boolean
    score: number
    comment: string
  }
}

export interface TaskSubmission {
  id: number
  member: TaskParticipantSummary
  submitter: User
  version: number
  /** 这一版交的是哪个阶段：阶段的 id；不分阶段的题是 null。 */
  stage: string | null
  index: number
  createdAt: number
  updatedAt: number
  content: TaskSubmissionContent[]
  review?: TaskSubmissionReview
}

export interface TaskSubmissionContent {
  title: string
  type: TaskSubmissionEntryType
  contentText?: string
  contentAttachment?: Material
}

export interface TaskParticipantSummary {
  id: number
  intro: string
  name: string
  avatarId: number
}

export interface TaskParticipantRealNameInfo {
  realName: string
  studentId: string
  grade: string
  major?: string
  className: string
}

export interface TaskTeamParticipantMemberSummary {
  name: string
  intro: string
  avatarId: number
  isLeader: boolean
  realNameInfo?: TaskParticipantRealNameInfo
}

export interface TaskMembership {
  id: number
  member: TaskParticipantSummary
  createdAt: number
  updatedAt: number
  deadline: number | null
  approved: 'APPROVED' | 'DISAPPROVED' | 'NONE'
  realNameInfo?: TaskParticipantRealNameInfo
  email?: string
  phone?: string
  applyReason?: string
  personalAdvantage?: string
  remark?: string
  /** 这条报名是不是一支团队领的（列表接口 `GET /tasks/{id}/participants` 给的）。 */
  isTeam?: boolean
  /** 这条报名背后的团队。查不到队（已解散 / 老数据）或按人领取时没有这个字段。 */
  team?: { id: number; name: string }
  teamMembers?: TaskTeamParticipantMemberSummary[]
  /** 开始做的时刻：这条报名的项目建好的那一下（列表接口给的）。还没开始做是 null。 */
  startedAt?: number | null
}

export type TaskFormSubmitData = {
  name: string
  submitterType: TaskSubmitterType
  rank: number
  registrationStartAt?: number | null
  deadline: number | null
  defaultDeadline: number
  startMode?: TaskStartMode
  resubmittable: boolean
  editable: boolean
  intro: string
  description: string
  // Optional, matching both what TaskForm.vue actually emits and what
  // PostTaskRequestData accepts: categoryId falls back to undefined. They were declared
  // required, but nothing ever caught it — TaskForm builds this object by
  // spreading vee-validate's untyped `values`, which collapses the whole
  // literal to `any` and switches off checking for every field below.
  categoryId?: number
  requireRealName: boolean
  minTeamSize?: number
  maxTeamSize?: number
  participantLimit?: number
  teamLockingPolicy?: TaskTeamMembershipLockPolicy
  accessControlEnabled?: boolean
  accessDomainGroupIds?: number[]
  videoUrl?: string | null
  /**
   * 这道题自己的「给 AI 队友的指导」覆盖（#944）。留空 = 不设，用空间的默认
   * （`PublishTask.vue` 那一栏据此决定要不要带上它）。
   */
  teaching?: SpaceTeaching
  /**
   * 这道题指定的默认队友（#944）：平台预设名，`''` = 这一层不单独指定。
   * 提交时是否带上这个键由那一栏自己按「三选一」口径决定（见 `defaultRolePayload`）。
   */
  defaultRole?: string
  /** 加入每个领取者项目的导师（#3830）；空 = 没有导师。 */
  mentors?: string[]
  /** 学生提交时逐项填写的东西。 */
  submissionSchema?: TaskSubmissionSchemaEntry[]
  rubric?: TaskRubricLine[]
  rubricPublic?: boolean
  stages?: TaskStage[]
}
