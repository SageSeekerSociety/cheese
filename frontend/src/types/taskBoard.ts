// 题目（板上的赛题）一域的请求 / 响应形状，供 `@/api/taskBoard` 用。组件可以引
// `@/types`，不该引 API 层。`Task`/`TaskSubmission` 这些实体形状仍在 `@/types/tasks`。
import type {
  SpaceTeaching,
  Task,
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
  /** 这道题指定的默认队友（#944）：平台预设的名字（如 `academic-research`）。
   *  省略 = 不用说（沿用项目集 / 空间）；空串 = 明确这道题不要默认队友。 */
  defaultRole?: string
}

export type { TaskAttachmentData, TaskAttachmentListResponseData } from '@/types/taskAttachments'
import type { TaskAttachmentData } from '@/types/taskAttachments'

export type UploadTaskAttachmentResponseData = {
  attachment: TaskAttachmentData
}

/**
 * PDF 转赛题请求数据
 * @property spaceId - 目标空间 ID
 * @property file - 要上传的 PDF 文件
 * @property categoryId - 可选的分类 ID
 * @property templateIndex - 模板索引，-1 表示使用空白模板
 * @property submitterType - 提交者类型（USER / TEAM）
 * @property maxTasks - 最大解析赛题数量
 */
export type CreateTaskFromPdfRequestData = {
  spaceId: number
  file: File
  categoryId?: number
  templateIndex?: number
  submitterType?: TaskSubmitterType
  maxTasks?: number
}

/**
 * 预览时已经**落成文件行**、可以勾来当附件的东西。
 *
 * **没有 url**：与题目附件那一份同一个理由 —— 存储给的是直链，发出来就等于绕开下载
 * 那道门。要文件走 `GET /tasks/{id}/attachments/{id}/download`。
 */
export interface PdfPublishAttachmentData {
  id: number
  name: string
  size: number
  contentType: string
}

export type PdfPublishAttachmentsData = {
  /** 上传的那份 PDF 本身。没有就是没有，不画一颗点了没用的勾。 */
  pdf: PdfPublishAttachmentData | null
  /** 从 PDF 里抽出来、并且真的进了某条草稿正文的那些插图。 */
  images: PdfPublishAttachmentData[]
}

/**
 * PDF 解析预览的响应数据
 * @property drafts - 解析出的赛题草稿列表
 * @property templateUsed - 实际使用的模板信息
 * @property tokenUsed - 本次解析消耗的 token 数量
 * @property attachments - 服务端已经替调用者落好的、可以勾来当附件的文件
 */
export type PreviewTaskFromPdfResponseData = {
  drafts: PdfTaskDraftData[]
  templateUsed: Record<string, any>
  tokenUsed: number
  attachments: PdfPublishAttachmentsData
}

/** 确认发布 PDF 解析草稿的请求数据 */
export type ConfirmTaskFromPdfRequestData = {
  drafts: PdfTaskDraftData[]
  taskOptions: PostTaskRequestData
}

export type PdfTaskDraftData = {
  name: string
  intro: string
  description: string
  space: number
  categoryId?: number
}

/**
 * 确认发布 PDF 解析草稿的响应数据
 * @property tasks - 已创建的赛题列表
 * @property count - 成功创建的赛题数量
 */
export type ConfirmTaskFromPdfResponseData = {
  tasks: Task[]
  count: number
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
  /** 这道题指定的默认队友（#944）。省掉 = 不动；空串 = 清掉这道题的覆盖。 */
  defaultRole?: string
  endedAt?: number | null
  hasEndedAt?: boolean
}

export type AddTaskParticipantRequestData = {
  deadline: number | null
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
 * Which level the composed guidance came from — `null` when no level said
 * anything. Shown so a student reads 「来自项目集」 rather than a bare result;
 * carried by the same resolution loop that picks the value (server side).
 */
export type TaskInheritanceSource = 'space' | 'category' | 'task' | 'project'

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
  source: TaskInheritanceSource | null
}

/**
 * The default teammate a challenge hands over (#944): the platform preset's
 * type name, the title people read, and the skills that teammate starts with.
 * `null` when this 题目 (and every level above it) named no role, or named one
 * that matches no shipped preset — a name nobody ships is not a teammate.
 */
export interface TaskInheritanceAgent {
  /** 预设类型名；题目只带了技能、没指定发货预设时为 null。 */
  typeName: string | null
  displayName: string | null
  skills: string[]
}

/** What building a project from a challenge hands over — `GET /tasks/{id}/inheritance`. */
export interface TaskInheritanceData {
  taskId: number
  spaceId: number
  resourcePack: Record<string, unknown>
  /** 建项目时那个芝士会带上的默认队友（类型 + 技能）；没指定 = null。 */
  agent: TaskInheritanceAgent | null
  /**
   * 题目包里「手动加的一段 prompt」—— 建项目时并进默认队友的人设正文。
   * 没写 = null。与 `agent` 的技能一样，是**这道题**给的（每道题可以不同）。
   */
  prompt: string | null
  teaching: TaskInheritanceTeaching
  materials: TaskInheritanceMaterial[]
}
