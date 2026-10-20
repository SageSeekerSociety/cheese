// 芝士写题目时说的话（`cheese_task_write` 的参数，后端 `drafts.AGENT_FIELDS`）怎么
// 落到发题表单上。
//
// 参数是边写边流过来的：页面读到一截就先把能确定的那几格填上（`previewOf`），指针
// 停在正在写的那一格。这只是预览：调用真正写进草稿以后，服务端推回来的「改动」才是
// 定数，页面用它覆盖预览。所以这里只做页面能当场做的换算，拿不准的值（没写完的日期、
// 空间里没有的分类）先不填，等服务端那一份。
import type { TaskDraftField, TaskDraftFields, TaskSubmissionEntryType } from '@/types'

import dayjs from 'dayjs'

/** 芝士的字段名，按表单从上到下的顺序，各落在表单的哪几格。 */
export const AGENT_FIELDS: Record<string, TaskDraftField[]> = {
  name: ['name'],
  description: ['description', 'descriptionMarkdown'],
  submission: ['submissionSchema'],
  rubric: ['rubric'],
  rubric_public: ['rubricPublic'],
  form: ['submitterType'],
  team_min: ['minTeamSize'],
  team_max: ['maxTeamSize'],
  difficulty: ['rank'],
  participant_limit: ['participantLimit'],
  deadline: ['deadline'],
  days_to_submit: ['defaultDeadline'],
  category: ['categoryId'],
  tags: ['topics'],
  real_name: ['requireRealName'],
}

// 芝士写的值，按原文比对（后端 `drafts.FORMS` 等同一份）。
const FORMS: Record<string, 'USER' | 'TEAM'> = { 个人: 'USER', 团队: 'TEAM' }
const LEVELS: Record<string, number> = { 初级: 1, 中级: 2, 高级: 3 }
const KINDS: Record<string, TaskSubmissionEntryType> = { 文件: 'FILE', 文字: 'TEXT' }

/** 只认写完整的日期：流到一半的「2026-11-0」先不填。 */
const WHEN = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?$/

/** 换算名字用的：这个空间的分类和标签。 */
export interface DraftTerms {
  categories: { id: number; name: string }[]
  topics: { id: number; name: string }[]
}

export interface Preview {
  fields: TaskDraftFields
  /** 正在写的那一格（芝士的字段名）：参数里最后出现、还在写的那一个。 */
  writing: string | null
}

function text(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function count(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? Math.round(value) : null
}

/** 参数写到这里时，表单上能确定的那几格。 */
export function previewOf(args: unknown, terms: DraftTerms): Preview {
  const fields: TaskDraftFields = {}
  if (!args || typeof args !== 'object' || Array.isArray(args)) return { fields, writing: null }
  const given = args as Record<string, unknown>
  const keys = Object.keys(given).filter((k) => k in AGENT_FIELDS)
  const name = text(given.name)
  if (name !== null) fields.name = name
  const description = text(given.description)
  if (description !== null) {
    fields.description = description
    fields.descriptionMarkdown = description
  }
  const form = text(given.form)
  if (form && form in FORMS) fields.submitterType = FORMS[form]
  const level = text(given.difficulty)
  if (level && level in LEVELS) fields.rank = LEVELS[level]
  for (const [key, target] of [
    ['team_min', 'minTeamSize'],
    ['team_max', 'maxTeamSize'],
    ['days_to_submit', 'defaultDeadline'],
  ] as const) {
    const n = count(given[key])
    if (n !== null) fields[target] = n
  }
  if ('participant_limit' in given) {
    const n = count(given.participant_limit)
    if (n !== null) fields.participantLimit = n > 0 ? n : null
  }
  const deadline = text(given.deadline)
  if (deadline !== null) {
    const found = WHEN.exec(deadline.trim())
    if (found) {
      const [, y, m, d, hh, mm] = found
      const at = dayjs(`${y}-${m}-${d}T${hh ?? '23'}:${mm ?? '59'}`)
      if (at.isValid()) fields.deadline = at.valueOf()
    } else if (!deadline) fields.deadline = null
  }
  const category = text(given.category)
  const found = category ? terms.categories.find((c) => c.name === category) : undefined
  if (found) fields.categoryId = found.id
  if (Array.isArray(given.tags)) {
    fields.topics = given.tags
      .map((tag) => terms.topics.find((topic) => topic.name === tag)?.id)
      .filter((id): id is number => id !== undefined)
  }
  if (Array.isArray(given.submission)) {
    fields.submissionSchema = given.submission
      .map((item) => item as { name?: unknown; kind?: unknown })
      .filter((item) => typeof item.name === 'string')
      .map((item) => ({
        prompt: String(item.name),
        type: KINDS[String(item.kind)] ?? 'FILE',
      }))
  }
  if (Array.isArray(given.rubric)) {
    fields.rubric = given.rubric
      .map((line) => line as { item?: unknown; points?: unknown; criteria?: unknown })
      .filter((line) => typeof line.item === 'string')
      .map((line) => ({
        item: String(line.item),
        points: count(line.points) ?? 0,
        criteria: text(line.criteria) ?? '',
      }))
  }
  if (typeof given.rubric_public === 'boolean') fields.rubricPublic = given.rubric_public
  if (typeof given.real_name === 'boolean') fields.requireRealName = given.real_name
  return { fields, writing: keys.length ? keys[keys.length - 1] : null }
}
