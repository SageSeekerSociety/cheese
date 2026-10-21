// 芝士写题目时说的话（`cheese_task_write` 的参数，后端 `drafts.AGENT_FIELDS`）怎么
// 落到发题表单上。
//
// 参数是边写边流过来的：页面读到一截就先把能确定的那几格填上（`previewOf`），指针
// 停在正在写的那一格。这只是预览：调用真正写进草稿以后，服务端推回来的「改动」才是
// 定数，页面用它覆盖预览。所以这里只做页面能当场做的换算，拿不准的值（没写完的日期、
// 空间里没有的分类）先不填，等服务端那一份。
import type { TypistPace } from '@/lib/typist'
import type { SpaceTeaching, TaskDraftField, TaskDraftFields, TaskSubmissionEntryType, TeachingMode } from '@/types'

import dayjs from 'dayjs'

/** 芝士的字段名，按表单从上到下的顺序，各落在表单的哪几格。 */
/** `cheese_task_write` 的参数怎么放（`lib/typist.ts`）：详情、改详情时换上的那段、评分
 *  标准写起来长；草稿 id、要替换的原文这些只用来定位。 */
export const DRAFT_WRITE_PACE: TypistPace = {
  long: ['description', 'replace', 'criteria'],
  instant: ['find', 'draft', 'task', 'space', 'version', 'source', 'template'],
}

export const AGENT_FIELDS: Record<string, TaskDraftField[]> = {
  name: ['name'],
  description: ['description', 'descriptionMarkdown'],
  attachments: ['attachmentIds', 'attachmentFiles'],
  submission: ['submissionSchema'],
  rubric: ['rubric'],
  rubric_public: ['rubricPublic'],
  form: ['submitterType'],
  difficulty: ['rank'],
  team_min: ['minTeamSize'],
  team_max: ['maxTeamSize'],
  team_locking: ['teamLockingPolicy'],
  participant_limit: ['participantLimit'],
  mentors: ['mentors'],
  registration_start: ['registrationStartAt'],
  deadline: ['deadline'],
  days_to_submit: ['defaultDeadline'],
  category: ['categoryId'],
  tags: ['topics'],
  access: ['accessControlEnabled', 'accessDomainGroupIds'],
  real_name: ['requireRealName'],
  ai_guidance: ['teaching', 'teachingOwn'],
  ai_mode: ['teaching', 'teachingOwn'],
}

/** 收在「更多设置」里的几格：芝士写到它们时那一块要展开。 */
export const MORE_FIELDS = ['access', 'real_name', 'ai_guidance', 'ai_mode']

// 芝士写的值，按原文比对（后端 `drafts.FORMS` 等同一份）。
const FORMS: Record<string, 'USER' | 'TEAM'> = { 个人: 'USER', 团队: 'TEAM' }
const LEVELS: Record<string, number> = { 初级: 1, 中级: 2, 高级: 3 }
const KINDS: Record<string, TaskSubmissionEntryType> = { 文件: 'FILE', 文字: 'TEXT' }
const LOCKING: Record<string, 'NO_LOCK' | 'LOCK_ON_APPROVAL'> = {
  允许变更: 'NO_LOCK',
  报名通过后锁定: 'LOCK_ON_APPROVAL',
}
const AI_MODES: Record<string, TeachingMode | null> = {
  引导为主: 'guide',
  讲解与示例: 'example',
  先明确需求再实现: 'spec',
  不加限制: 'free',
  沿用空间设置: null,
}

/** 只认写完整的日期：流到一半的「2026-11-0」先不填。 */
const WHEN = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?$/

/** 换算名字用的：这个空间的分类、标签、邮箱域名组，和能当导师的人。 */
export interface DraftTerms {
  categories: { id: number; name: string }[]
  topics: { id: number; name: string }[]
  domainGroups?: { id: number; name: string }[]
  mentors?: { handle: string; name: string }[]
}

export interface Preview {
  fields: TaskDraftFields
  /** 正在写的那一格（芝士的字段名）：参数里最后出现、还在写的那一个。 */
  writing: string | null
  /** 正在改的详情里的那一段：原文，和写到一半的新文字（还没开始写是 null，这时原文是选中的样子）。 */
  passage?: { find: string; replace: string | null }
}

function text(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function count(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? Math.round(value) : null
}

/** 参数写到这里时，表单上能确定的那几格。`markdown` 是详情现在的样子：只改几处
 * （`description_edits`）时，在它上面把已经写出来的那几处换掉。 */
export function previewOf(args: unknown, terms: DraftTerms, markdown = ''): Preview {
  const fields: TaskDraftFields = {}
  if (!args || typeof args !== 'object' || Array.isArray(args)) return { fields, writing: null }
  const given = args as Record<string, unknown>
  const keys = Object.keys(given).filter((k) => k in AGENT_FIELDS || k === 'description_edits')
  let passage: Preview['passage']
  if (Array.isArray(given.description_edits) && markdown) {
    let text = markdown
    for (const edit of given.description_edits) {
      const { find, replace } = (edit ?? {}) as { find?: unknown; replace?: unknown }
      // 原文要写完整才找得到；新文字写到哪换到哪。
      if (typeof find !== 'string' || !find || !text.includes(find)) continue
      const typed = typeof replace === 'string' ? replace : null
      passage = { find, replace: typed }
      if (typed !== null) text = text.replace(find, typed)
    }
    if (text !== markdown) {
      fields.description = text
      fields.descriptionMarkdown = text
    }
  }
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
  for (const [key, target] of [
    ['deadline', 'deadline'],
    ['registration_start', 'registrationStartAt'],
  ] as const) {
    const when = text(given[key])
    if (when === null) continue
    const found = WHEN.exec(when.trim())
    if (found) {
      const [, y, m, d, hh, mm] = found
      const at = dayjs(`${y}-${m}-${d}T${hh ?? '23'}:${mm ?? '59'}`)
      if (at.isValid()) fields[target] = at.valueOf()
    } else if (!when) fields[target] = null
  }
  const locking = text(given.team_locking)
  if (locking && locking in LOCKING) fields.teamLockingPolicy = LOCKING[locking]
  if (Array.isArray(given.mentors)) {
    const people = terms.mentors ?? []
    fields.mentors = given.mentors
      .map((word) => String(word).replace(/^@/, '').trim())
      .map((word) => people.find((p) => p.handle === word || p.name === word)?.handle)
      .filter((handle): handle is string => handle !== undefined)
  }
  if (Array.isArray(given.access)) {
    const ids = given.access
      .map((name) => (terms.domainGroups ?? []).find((g) => g.name === name)?.id)
      .filter((id): id is number => id !== undefined)
    fields.accessControlEnabled = given.access.length > 0
    fields.accessDomainGroupIds = ids
  }
  const guidance = text(given.ai_guidance)
  const way = text(given.ai_mode)
  if (guidance !== null || (way !== null && way in AI_MODES)) {
    const teaching: SpaceTeaching = {}
    if (guidance !== null) teaching.systemPrompt = guidance.trim() ? guidance : null
    if (way !== null && way in AI_MODES) teaching.mode = AI_MODES[way]
    fields.teaching = teaching
    fields.teachingOwn = !!(teaching.mode || teaching.systemPrompt?.trim())
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
  const last = keys.length ? keys[keys.length - 1] : null
  return { fields, writing: last === 'description_edits' ? 'description' : last, passage }
}

/** 发布前还缺的格子（芝士的字段名），和后端 `drafts.missing` 同一条规矩：名称、详情、参与方式、难度。 */
export function missingOf(fields: TaskDraftFields): string[] {
  const out: string[] = []
  if (!fields.name?.trim()) out.push('name')
  const markdown = fields.descriptionMarkdown?.trim()
  const doc = typeof fields.description === 'string' ? docText(fields.description) : ''
  if (!markdown && !doc) out.push('description')
  if (fields.submitterType !== 'USER' && fields.submitterType !== 'TEAM') out.push('form')
  if (fields.rank !== 1 && fields.rank !== 2 && fields.rank !== 3) out.push('difficulty')
  return out
}

/** 详情里读得出的字：编辑器的 JSON 取文字，别的原样。 */
function docText(description: string): string {
  if (!description.trim().startsWith('{')) return description.trim()
  try {
    const words: string[] = []
    const walk = (node: { type?: string; text?: string; content?: unknown[] }) => {
      if (node.type === 'text' && node.text) words.push(node.text)
      for (const child of node.content ?? []) walk(child as typeof node)
    }
    walk(JSON.parse(description))
    return words.join('').trim()
  } catch {
    return description.trim()
  }
}
