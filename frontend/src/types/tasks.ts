import type { Material, Space, SpaceCategory, SpaceTeaching, Team, TeamSummary, Topic, User } from '.'

export type TaskSubmitterType = 'USER' | 'TEAM'
export type TaskSubmissionEntryType = 'TEXT' | 'FILE'
export type TaskTeamMembershipLockPolicy = 'NO_LOCK' | 'LOCK_ON_APPROVAL'
export type EligibilityRejectReasonCode =
  | 'ALREADY_PARTICIPATING'
  | 'MEMBER_ALREADY_PARTICIPATING'
  | 'PARTICIPANT_LIMIT_REACHED'
  | 'TASK_NOT_APPROVED'
  | 'REGISTRATION_NOT_STARTED'
  | 'REGISTRATION_CLOSED'
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
}

/** 我在一道题上走到哪一步：没交、交了等判、通过、退回可重交。
 *
 *  由后端按**提交与评审**算（`app/domain/task/submission_state.py`），不读
 *  `TaskMembership.completion_status` —— 那一列除了领取与逾期两处没人写，交过作业
 *  的人至今读作「没交」。 */
export type TaskClaimStatus = 'IN_PROGRESS' | 'SUBMITTED' | 'PASSED' | 'REJECTED'

export interface Task {
  id: number
  name: string
  intro: string
  submitterType: TaskSubmitterType
  creator: User
  registrationStartAt?: number
  deadline: number | null
  participantLimit: number
  defaultDeadline: number
  resubmittable: boolean
  editable: boolean
  description: string
  space?: Space
  category?: SpaceCategory
  submissionSchema: TaskSubmissionSchemaEntry[]
  participants: {
    total: number
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
  /** 这道题挂着几份材料。列表接口一并给（只增不改的那个数，不带文件本体）。 */
  attachmentCount?: number
  /** 我这条领取的档位。`null`/缺省 = 我没领这道题。随 `queryJoined` 一族回来。 */
  myClaimStatus?: TaskClaimStatus | null
  /**
   * 这道题自己那份「给 AI 队友的指导」覆盖（#944）。四级继承里题目那一层，整份
   * 替换、不深合；`{}` = 没说，下面（项目集）或上面（空间）的默认原样生效。
   */
  teaching?: SpaceTeaching
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
}

export type TaskFormSubmitData = {
  name: string
  submitterType: TaskSubmitterType
  rank: number
  registrationStartAt?: number | null
  deadline: number | null
  defaultDeadline: number
  resubmittable: boolean
  editable: boolean
  intro: string
  description: string
  // Optional, matching both what TaskForm.vue actually emits and what
  // PostTaskRequestData accepts: categoryId falls back to undefined, and the
  // team sizes are only set for submitterType === 'TEAM'. They were declared
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
}
