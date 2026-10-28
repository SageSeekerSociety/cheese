// 题目（板上的赛题）一域的请求 / 响应形状，供 `@/api/taskBoard` 用。组件可以引
// `@/types`，不该引 API 层。`Task`/`TaskSubmission` 这些实体形状仍在 `@/types/tasks`。
import type {
  SpaceTeaching,
  Task,
  TaskRubricLine,
  TaskStage,
  TaskStartMode,
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
  startMode?: TaskStartMode
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
   *  id，建题时拷进题目资料库：题目建好之前还没有资料库可放。 */
  attachmentIds?: number[]
  /** 这道题自己的「给 AI 队友的指导」覆盖（#944）。整份替换；不带它 = 没说，
   *  让空间的默认生效。 */
  teaching?: SpaceTeaching
  /** 这道题指定的默认队友（#944）：平台预设的名字（如 `academic-research`）。
   *  省略 = 不用说（沿用项目集 / 空间）；空串 = 明确这道题不要默认队友。 */
  defaultRole?: string
  rubric?: TaskRubricLine[]
  rubricPublic?: boolean
  stages?: TaskStage[]
}

export type { TaskFileData, TaskFileListData } from '@/types/taskFiles'

export type PatchTaskRequestData = {
  name?: string
  deadline?: number | null
  defaultDeadline?: number
  startMode?: TaskStartMode
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
  /** 这道题指定的默认队友（#944）。省掉 = 不动；空串 = 清掉这道题的覆盖。 */
  defaultRole?: string
  /** 评分要点：整份替换；省掉它 = 不动。 */
  rubric?: TaskRubricLine[]
  rubricPublic?: boolean
  /** 阶段：整份替换；省掉它 = 不动。 */
  stages?: TaskStage[]
  endedAt?: number | null
  hasEndedAt?: boolean
}

export type AddTaskParticipantRequestData = {
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

/**
 * The default teammate a challenge hands over (#944): the platform preset's
 * type name, the title people read, and the skills that teammate starts with.
 * `null` when this 题目 (and every level above it) named no role, or named one
 * that matches no shipped preset — a name nobody ships is not a teammate.
 */
export interface TaskInheritanceAgent {
  typeName: string
  displayName: string
  skills: string[]
}

/** What building a project from a challenge hands over — `GET /tasks/{id}/inheritance`. */
export interface TaskInheritanceData {
  taskId: number
  spaceId: number
  /** 资源包：算力额度（`compute_credits`）。 */
  resourcePack: Record<string, unknown>
  /** 建项目时那个芝士会带上的默认队友（类型 + 预设技能）；没指定 = null。题目给每个
   *  队友的技能在 `teaching.skills`。 */
  agent: TaskInheritanceAgent | null
  /** 资源包里的那段 prompt：建项目时加在默认队友的人设末尾；没有 = null。 */
  packPrompt: string | null
  teaching: TaskInheritanceTeaching
  materials: TaskInheritanceMaterial[]
}
