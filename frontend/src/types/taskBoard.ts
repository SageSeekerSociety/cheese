// 题目（板上的赛题）一域的请求 / 响应形状，供 `@/api/taskBoard` 用。组件可以引
// `@/types`，不该引 API 层。`Task`/`TaskSubmission` 这些实体形状仍在 `@/types/tasks`。
import type {
  SpaceTeaching,
  Task,
  TaskRubricLine,
  TaskSubmissionSchemaEntry,
  TaskSubmitterType,
  TaskTeamMembershipLockPolicy,
} from '@/types'

// 添加参与者身份类型
export interface TaskParticipationIdentity {
  id: number
  type: TaskSubmitterType
  memberId: number
  teamName?: string
  deadline?: number | null
  canSubmit: boolean
  approved: 'NONE' | 'APPROVED' | 'DISAPPROVED'
}

// 添加参与信息类型
export interface TaskParticipationInfo {
  hasParticipation: boolean
  identities: TaskParticipationIdentity[]
}

export type PostTaskRequestData = {
  name: string
  submitterType: 'USER' | 'TEAM'
  deadline: number | null
  defaultDeadline: number
  resubmittable: boolean
  editable: boolean
  description: string
  submissionSchema: TaskSubmissionSchemaEntry[]
  team?: number
  space?: number
  categoryId?: number
  requireRealName?: boolean
  minTeamSize?: number
  maxTeamSize?: number
  participantLimit?: number
  teamLockingPolicy?: TaskTeamMembershipLockPolicy
  accessControlEnabled?: boolean
  accessDomainGroupIds?: number[]
  endedAt?: number | null
  hasEndedAt?: boolean
  /** 随题一起发出去的材料。文件先经 `AttachmentsApi`（`POST /attachments`）传上来拿到
   *  id，建题时一次挂上——见 `TaskAttachmentPicker` 里对这条顺序的说明。 */
  attachmentIds?: number[]
  /** 这道题自己的「给 AI 队友的指导」覆盖（#944）。整份替换；不带它 = 没说，
   *  让空间的默认生效。 */
  teaching?: SpaceTeaching
  rubric?: TaskRubricLine[]
  rubricPublic?: boolean
}

export type { TaskAttachmentData, TaskAttachmentListResponseData } from '@/types/taskAttachments'
import type { TaskAttachmentData } from '@/types/taskAttachments'

export type UploadTaskAttachmentResponseData = {
  attachment: TaskAttachmentData
}

export type PatchTaskRequestData = {
  name?: string
  deadline?: number | null
  defaultDeadline?: number
  resubmittable?: boolean
  editable?: boolean
  description?: string
  submissionSchema?: TaskSubmissionSchemaEntry[]
  approved?: 'APPROVED' | 'DISAPPROVED' | 'NONE'
  rejectReason?: string
  requireRealName?: boolean
  participantLimit?: number
  teamLockingPolicy?: TaskTeamMembershipLockPolicy
  accessControlEnabled?: boolean
  accessDomainGroupIds?: number[]
  /** 这道题自己的「给 AI 队友的指导」覆盖（#944）。整份替换；省掉它 = 不动。 */
  teaching?: SpaceTeaching
  /** 评分要点：整份替换；省掉它 = 不动。 */
  rubric?: TaskRubricLine[]
  rubricPublic?: boolean
  endedAt?: number | null
  hasEndedAt?: boolean
}

export type AddTaskParticipantRequestData = {
  deadline: number | null
  /** 领取的人同意这道题的导师加入项目（#3830）；题目有导师时必须带上。 */
  agreeMentors?: boolean
  email?: string
  phone?: string
  applyReason?: string
  personalAdvantage?: string
  remark?: string
}

export type PatchTaskParticipantRequestData = {
  deadline?: number
  approved?: 'APPROVED' | 'DISAPPROVED' | 'NONE'
  rejectReason?: string
  email?: string
  phone?: string
  applyReason?: string
  personalAdvantage?: string
  remark?: string
}

export type PostTaskSubmissionRequestData = {
  text?: string
  attachmentId?: number
}

export type PostTaskSubmissionReviewRequestData = {
  accepted: boolean
  score: number
  comment: string
}

export type PatchTaskSubmissionReviewRequestData = Partial<PostTaskSubmissionReviewRequestData>

/**
 * Where the composed guidance came from — the space, the task, both, or the
 * project; `null` when nobody said anything. Shown so a student reads where
 * it came from rather than a bare result;
 * carried by the same resolution loop that picks the value (server side).
 */
export type TaskInheritanceSource = 'space' | 'task' | 'both' | 'project'

/** A board's 资料 row as the inheritance list reports it — never a `url`. */
export interface TaskInheritanceMaterial {
  id: number
  name: string
  type: string
  visibility: string
  size: number | null
  mime: string | null
  uploaderId: number
  createdAt: number
  downloadCount: number
}

/** The composed 给 AI 队友的指导, plus the level it came from. */
export interface TaskInheritanceTeaching extends SpaceTeaching {
  /** 空间和题目都写了补充要求时，空间的那一份（`systemPrompt` 是题目的）。 */
  spacePrompt?: string | null
  source: TaskInheritanceSource | null
}

/** What building a project from a challenge hands over — `GET /tasks/{id}/inheritance`. */
export interface TaskInheritanceData {
  taskId: number
  spaceId: number
  resourcePack: Record<string, unknown>
  teaching: TaskInheritanceTeaching
  materials: TaskInheritanceMaterial[]
}
