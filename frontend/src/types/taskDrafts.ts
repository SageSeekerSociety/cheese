// 题目草稿（`/task-drafts`）：写到一半的新题目，或对一道已发布题目还没保存的修改。
// 草稿存在服务端：发布页、改题页边写边存进去，芝士答题时也写进同一份。
import type { SpaceTeaching } from './spaces'
import type {
  TaskRubricLine,
  TaskStage,
  TaskStartMode,
  TaskSubmissionSchemaEntry,
  TaskSubmitterType,
  TaskTeamMembershipLockPolicy,
} from './tasks'

/** 草稿里存的那张表单，和 `POST /tasks` 收的形状一样；`descriptionMarkdown`、
 *  `teachingOwn`、`attachmentFiles` 和 `starterFile` 只属于页面（芝士读写详情用
 *  Markdown；「这道题单独写 AI 队友辅导」那一勾；附件和起始代码一栏上的名字）。 */
export interface TaskDraftFields {
  name?: string
  description?: string
  descriptionMarkdown?: string
  submitterType?: TaskSubmitterType
  rank?: number
  deadline?: number | null
  registrationStartAt?: number | null
  defaultDeadline?: number
  startMode?: TaskStartMode
  participantLimit?: number | null
  minTeamSize?: number
  maxTeamSize?: number
  teamLockingPolicy?: TaskTeamMembershipLockPolicy
  categoryId?: number
  topics?: number[]
  requireRealName?: boolean
  accessControlEnabled?: boolean
  accessDomainGroupIds?: number[]
  mentors?: string[]
  submissionSchema?: TaskSubmissionSchemaEntry[]
  rubric?: TaskRubricLine[]
  rubricPublic?: boolean
  stages?: TaskStage[]
  attachmentIds?: number[]
  /** 附件的名字和大小，页面列附件用；发布时不带。 */
  attachmentFiles?: { id: number; name: string; size: number }[]
  /** 起始代码：先传成附件的一份 .zip 的 id；null 是没有。 */
  starterAttachmentId?: number | null
  /** 起始代码的名字和大小，页面列它用；发布时不带。 */
  starterFile?: { id: number; name: string; size: number } | null
  /** 改已发布的题目时不要起始代码了（芝士写了 `starter: 0`）。 */
  removeStarter?: boolean
  teaching?: SpaceTeaching
  teachingOwn?: boolean
  /** 这道题的默认队友（预设名）；`''` = 清掉这道题自己的，`null` = 不动。 */
  defaultRole?: string | null
}

export type TaskDraftField = keyof TaskDraftFields

/** 草稿列表里的一行。 */
export interface TaskDraftSummary {
  id: number
  spaceId: number
  /** 对哪道已发布的题目的修改；新题目是 null。 */
  taskId: number | null
  taskName: string | null
  name: string
  /** 从哪个文件导入的；新建的是空。 */
  source: string
  /** 这份草稿上的芝士对话。 */
  conversationId: string | null
  version: number
  updatedAt: string
  /** 发布前还缺的格子（芝士的字段名：name、description、form、difficulty）。 */
  missing: string[]
}

export interface TaskDraft extends TaskDraftSummary {
  fields: TaskDraftFields
}

/** 草稿收的字段：页面交给它的只能是这几个（后端 `drafts.FORM_FIELDS`）。 */
export const TASK_DRAFT_FIELDS: readonly TaskDraftField[] = [
  'name',
  'description',
  'descriptionMarkdown',
  'submitterType',
  'rank',
  'deadline',
  'registrationStartAt',
  'defaultDeadline',
  'startMode',
  'participantLimit',
  'minTeamSize',
  'maxTeamSize',
  'teamLockingPolicy',
  'categoryId',
  'topics',
  'requireRealName',
  'accessControlEnabled',
  'accessDomainGroupIds',
  'mentors',
  'submissionSchema',
  'rubric',
  'rubricPublic',
  'stages',
  'attachmentIds',
  'attachmentFiles',
  'starterAttachmentId',
  'starterFile',
  'removeStarter',
  'teaching',
  'teachingOwn',
  'defaultRole',
]

/** 只留下草稿收的那几个字段。 */
export function draftFieldsOf(values: Record<string, unknown>): TaskDraftFields {
  const out: Record<string, unknown> = {}
  for (const key of TASK_DRAFT_FIELDS) if (key in values && values[key] !== undefined) out[key] = values[key]
  return out as TaskDraftFields
}
