// 芝士写进题目草稿的那些事，说成面板上的东西：每次写草稿的改动卡（改了哪几格、各是什么、
// 能不能撤销），它留给人确认的地方，以及发布的结果。格子的名字、值的说法都是题目表单的。
//
// 题目页和发布页、改题页用的是同一份：芝士在哪都能做这些事，差别只在页面上有没有
// 那张表单（没有就不给撤销）。
import type { PanelChange, PanelChangeRow, PanelResult, PanelWrites } from '@/lib/assistantView'
import type { DraftTerms } from '@/lib/taskDraftAgent'
import type { AssistantEdit, AssistantMessage, AssistantPublished } from '@/stores/agentThread'
import type { TaskRubricLine, TaskStage, TaskSubmissionSchemaEntry } from '@/types'

import dayjs from 'dayjs'

import { t } from '@/i18n'
import { proposalView, stepView, withSubagents } from '@/lib/assistantView'
import { renderNoticeMessage } from '@/lib/noticeText'
import { AGENT_FIELDS } from '@/lib/taskDraftAgent'

/** 每个字段在表单上叫什么。 */
const LABEL_KEYS: Record<string, string> = {
  name: 'tasks.form.name',
  description: 'tasks.form.description',
  stages: 'tasks.form.stages.title',
  submission: 'tasks.form.grading.items',
  rubric: 'tasks.form.grading.rubric',
  rubric_public: 'tasks.form.grading.public',
  form: 'tasks.form.submitterType',
  team_min: 'tasks.form.minTeamSize',
  team_max: 'tasks.form.maxTeamSize',
  difficulty: 'tasks.form.rank',
  participant_limit: 'tasks.form.participantLimit',
  deadline: 'tasks.form.registrationEnd',
  days_to_submit: 'tasks.form.completion',
  category: 'tasks.form.category',
  tags: 'tasks.form.topics',
  real_name: 'tasks.form.more.realName',
  registration_start: 'tasks.form.registrationStart',
  team_locking: 'tasks.form.teamLocking.label',
  start_mode: 'tasks.form.start.label',
  mentors: 'tasks.form.mentor.label',
  access: 'tasks.form.more.access',
  attachments: 'tasks.attachmentPicker.title',
  starter: 'tasks.starterPicker.title',
  ai_teammate: 'spaces.teaching.defaultTeammate.title',
  ai_guidance: 'spaces.detail.publishTask.teaching.title',
  ai_mode: 'spaces.teaching.fields.mode',
  ai_private_guidance: 'spaces.teaching.fields.privatePrompt',
  ai_goals: 'spaces.teaching.fields.learningGoals',
  ai_materials: 'spaces.teaching.fields.materials',
  ai_skills: 'spaces.teaching.fields.skills',
}

/** 直接开始的题没有报名：两个日期是开放日期和交作业的截止日期。 */
const DIRECT_LABEL_KEYS: Record<string, string> = {
  deadline: 'tasks.form.dueOn',
  registration_start: 'tasks.form.opensOn',
}

export function fieldLabel(key: string, startMode?: 'DIRECT' | 'APPLY'): string {
  const label = (startMode === 'DIRECT' && DIRECT_LABEL_KEYS[key]) || LABEL_KEYS[key]
  return label ? t(label) : key
}

/** 表单上的一格（`TaskDraftField`）属于芝士的哪个字段。 */
export function agentKeyOf(formField: string): string | null {
  for (const [key, fields] of Object.entries(AGENT_FIELDS)) if ((fields as string[]).includes(formField)) return key
  return null
}

const LEVELS: Record<number, string> = {
  1: 'tasks.form.beginner',
  2: 'tasks.form.intermediate',
  3: 'tasks.form.advanced',
}

/** 一格的值，写成卡上那一行。 */
export function valueText(
  key: string,
  value: unknown,
  terms: DraftTerms,
  before?: unknown,
  startMode?: 'DIRECT' | 'APPLY'
): string {
  if (value === null || value === undefined || value === '') return t('tasks.assistant.changes.empty')
  switch (key) {
    case 'registration_start':
      return dayjs(Number(value)).format('YYYY-MM-DD HH:mm')
    case 'days_to_submit':
      return t('tasks.assistant.changes.days', { n: Number(value) })
    case 'team_locking':
      // 锁在开始的那一刻：申请制是申请通过时，直接开始是开始做这道题时。
      if (value !== 'LOCK_ON_APPROVAL') return t('tasks.form.teamLocking.free')
      return startMode === 'DIRECT' ? t('tasks.form.teamLocking.lockedStart') : t('tasks.form.teamLocking.locked')
    case 'start_mode':
      return value === 'DIRECT' ? t('tasks.form.start.direct') : t('tasks.form.start.apply')
    case 'mentors':
      return (value as string[]).length
        ? (value as string[]).map((h) => terms.mentors?.find((p) => p.handle === h)?.name ?? h).join('、')
        : t('tasks.assistant.changes.none')
    case 'access':
      return value === true || (Array.isArray(value) && value.length)
        ? (Array.isArray(value) ? value : [])
            .map((id) => terms.domainGroups?.find((g) => g.id === id)?.name ?? String(id))
            .join('、') || t('tasks.form.more.accessDomains')
        : t('tasks.form.more.accessAll')
    case 'starter':
      return (value as { name: string }).name
    case 'attachments':
      return (value as { name: string }[]).map((f) => f.name).join('、') || t('tasks.assistant.changes.none')
    case 'ai_guidance': {
      const given = value as { mode?: string | null; systemPrompt?: string | null }
      const parts = [
        given.mode ? t(`spaces.teaching.mode.${given.mode}`) : '',
        excerpt(given.systemPrompt?.trim() ?? ''),
      ].filter(Boolean)
      return parts.join(t('spaces.detail.publishTask.strip.separator')) || t('spaces.teaching.mode.inherit')
    }
    case 'form':
      return value === 'TEAM' ? t('tasks.form.team') : t('tasks.form.individual')
    case 'difficulty':
      return LEVELS[Number(value)] ? t(LEVELS[Number(value)]) : String(value)
    case 'deadline':
      return dayjs(Number(value)).format('YYYY-MM-DD HH:mm')
    case 'category':
      return terms.categories.find((c) => c.id === value)?.name ?? String(value)
    case 'tags':
      return (value as number[]).map((id) => terms.topics.find((topic) => topic.id === id)?.name ?? id).join('、')
    case 'stages':
      return (value as TaskStage[]).map((s) => s.name).join('、') || t('tasks.assistant.changes.none')
    case 'submission':
      return (value as TaskSubmissionSchemaEntry[]).map((e) => e.prompt).join('、')
    case 'rubric': {
      const lines = value as TaskRubricLine[]
      const total = lines.reduce((sum, line) => sum + (Number(line.points) || 0), 0)
      return t('tasks.assistant.changes.rubric', { n: lines.length, total })
    }
    case 'rubric_public':
    case 'real_name':
      return value ? t('tasks.assistant.changes.yes') : t('tasks.assistant.changes.no')
    case 'participant_limit':
      return String(value)
    case 'description':
      return excerpt(typeof before === 'string' ? changedPart(before, String(value)) : String(value))
    default:
      return String(value)
  }
}

/** 详情一行里放得下的那一截：去掉 Markdown 的记号。 */
function excerpt(text: string): string {
  return text
    .replace(/[#>*_`]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 80)
}

/** 新详情里改了的那一段：去掉和原来相同的开头和结尾。整篇重写时就是整篇。 */
export function changedPart(before: string, after: string): string {
  let head = 0
  while (head < before.length && head < after.length && before[head] === after[head]) head++
  let tail = 0
  while (
    tail < before.length - head &&
    tail < after.length - head &&
    before[before.length - 1 - tail] === after[after.length - 1 - tail]
  )
    tail++
  // 说改动所在的整行（几行），读得出上下文。
  const start = after.lastIndexOf('\n', head - 1) + 1
  const lineEnd = after.indexOf('\n', after.length - tail)
  const part = after.slice(start, lineEnd < 0 ? after.length : lineEnd)
  return part.trim() ? part : after
}

/** 一格在这次写入里的值：几格并成一项的（访问范围、附件、AI 指导）取说明它的那一格。 */
function rowValue(key: string, edit: AssistantEdit) {
  const pick = (field: string) => edit.changed[field]
  if (key === 'access') {
    const on = pick('accessControlEnabled')?.after ?? true
    return { after: on ? pick('accessDomainGroupIds')?.after ?? [] : false }
  }
  if (key === 'attachments') return { after: pick('attachmentFiles')?.after ?? [] }
  if (key === 'starter') return { after: pick('starterFile')?.after ?? null }
  if (key === 'ai_guidance') {
    const own = pick('teachingOwn')?.after ?? true
    return { after: own ? pick('teaching')?.after ?? {} : {} }
  }
  if (key === 'description') return pick('descriptionMarkdown') ?? pick('description')
  const field = AGENT_FIELDS[key]?.[0]
  return field ? pick(field) : undefined
}

/** 设置值怎么说；时段（22:00-08:00）这类原样写。 */
const SETTING_VALUES: Record<string, string> = {
  on: 'tasks.assistant.changes.setting.on',
  off: 'tasks.assistant.changes.setting.off',
  instant: 'tasks.assistant.changes.setting.instant',
  digest: 'tasks.assistant.changes.setting.digest',
  daily: 'tasks.assistant.changes.setting.daily',
  weekly: 'tasks.assistant.changes.setting.weekly',
  all: 'tasks.assistant.changes.setting.all',
  mentions: 'tasks.assistant.changes.setting.mentions',
  mute: 'tasks.assistant.changes.setting.mute',
}

/** 改的是这个人自己的设置（通知设置、频道提醒）或芝士对他的记忆，不是题目草稿。 */
const isSetting = (edit: AssistantEdit) => edit.kind === 'settings' || edit.kind === 'notify' || edit.kind === 'memory'

/** 芝士记下、改写或忘掉的一条记忆：左边写做了哪一种，右边是它的描述，总能撤销。 */
function memoryView(edit: AssistantEdit): PanelChange {
  const change = (edit.changed.memory ?? {}) as { label?: string; before?: unknown; after?: unknown }
  const done = change.after == null ? 'forgotten' : change.before == null ? 'saved' : 'changed'
  return {
    id: edit.id,
    draft: -edit.id,
    created: false,
    name: '',
    title: t('tasks.assistant.changes.memory.title'),
    rows: [
      {
        change: edit.id,
        key: 'memory',
        label: t(`tasks.assistant.changes.memory.${done}`),
        value: change.label ?? '',
        undone: edit.undone.includes('memory'),
      },
    ],
    notes: [],
    canUndo: edit.undoable,
  }
}

/** 改的是这个人自己的设置：每一项写名字和改成了什么，总能撤销。 */
function settingsView(edit: AssistantEdit): PanelChange {
  const changed = edit.changed as Record<string, { label?: string; after: unknown }>
  return {
    id: edit.id,
    draft: -edit.id,
    created: false,
    name: '',
    title: edit.title,
    rows: Object.entries(changed).map(([key, change]) => {
      const after = String(change.after)
      const said = SETTING_VALUES[after]
      return {
        change: edit.id,
        key,
        label: change.label ?? key,
        value: said ? t(said) : after,
        undone: edit.undone.includes(key),
      }
    }),
    notes: [],
    canUndo: edit.undoable,
  }
}

export function changeView(edit: AssistantEdit, terms: DraftTerms, canUndo: boolean): PanelChange {
  if (edit.kind === 'memory') return memoryView(edit)
  if (isSetting(edit)) return settingsView(edit)
  const rows: PanelChangeRow[] = []
  const seen = new Set<string>()
  for (const field of Object.keys(edit.changed)) {
    const key = agentKeyOf(field)
    if (!key || seen.has(key)) continue
    seen.add(key)
    const change = rowValue(key, edit)
    if (!change) continue
    rows.push({
      change: edit.id,
      key,
      label: fieldLabel(key, edit.startMode),
      value: valueText(key, change.after, terms, (change as { before?: unknown }).before, edit.startMode),
      undone: edit.undone.includes(key),
    })
  }
  for (const key of edit.kept)
    rows.push({
      change: edit.id,
      key,
      label: fieldLabel(key),
      value: t('tasks.assistant.changes.kept'),
      undone: false,
      kept: true,
    })
  return {
    id: edit.id,
    draft: edit.draft,
    created: edit.created,
    name: edit.name,
    rows,
    notes: edit.notes.map((note) => ({ key: note.field, label: fieldLabel(note.field), text: note.text })),
    canUndo,
  }
}

/**
 * 一轮里的几次写入画成面板上的样子（原型：发布页芝士）。
 *
 * 眼前这份草稿（`current`）的几次写入并成一张「表单改动」卡：同一格写了几次只留最后一次，
 * 每格记着是哪次写入改的，撤销照旧一格一格来；「待你确认」也只列这一份的。写到的别的草稿
 * （一份文件拆成几道题）各收成一行（`collapsed`）：写了几处，点名字切过去，切过去以后这一轮
 * 再画一遍，展开的换成那一份。没有表单的页面（题目页）`current` 是 null：每份草稿各一张卡，
 * 撤销不了。
 */
export function writesView(
  edits: AssistantEdit[],
  terms: DraftTerms,
  current: number | null,
  canUndo: (edit: AssistantEdit) => boolean = () => false,
  ordinal: (draft: number) => number | undefined = () => undefined,
  effectOf: (draft: number) => AssistantEdit | undefined = (draft) => latestEffect(edits).get(draft)
): PanelWrites {
  // A change of the person's own settings is a card of its own, keyed below every draft.
  const group = (edit: AssistantEdit) => (isSetting(edit) ? -edit.id : edit.draft)
  const order: number[] = []
  for (const edit of edits) if (!order.includes(group(edit))) order.push(group(edit))
  const changes = order.map((id) => {
    const card = merged(
      edits.filter((e) => group(e) === id),
      terms,
      canUndo,
      effectOf
    )
    const collapsed = current !== null && id > 0 && id !== current
    return { ...card, ...(collapsed ? { collapsed, notes: [], canUndo: false } : {}), ordinal: ordinal(id) }
  })
  return { changes }
}

/** 同一份草稿的几次写入并成一张卡：一格只留最后一次写的。 */
function merged(
  edits: AssistantEdit[],
  terms: DraftTerms,
  canUndo: (edit: AssistantEdit) => boolean,
  effectOf: (draft: number) => AssistantEdit | undefined
): PanelChange {
  const views = edits.map((e) => changeView(e, terms, canUndo(e)))
  const rows = new Map<string, PanelChangeRow>()
  for (const view of views)
    for (const row of view.rows) {
      rows.delete(row.key)
      rows.set(row.key, row)
    }
  const last = views[views.length - 1]
  // 存下阶段的影响是草稿的事，不是哪一条回答的：每次写入都说草稿那一刻的影响，所以只看这份
  // 草稿最新的那一次写入（不管在哪一条回答里），写在它的那张卡上，更早的卡不再写。
  const latest = effectOf(last.draft)
  const effect = latest && edits.includes(latest) ? latest.stageEffect : []
  return {
    ...last,
    created: views.some((v) => v.created),
    rows: [...rows.values()],
    notes: [
      ...views.flatMap((v) => v.notes),
      ...effect.map((said) => ({
        key: 'stages',
        label: fieldLabel('stages'),
        text: renderNoticeMessage(said, ''),
      })),
    ],
    canUndo: views.some((v) => v.canUndo),
  }
}

/** 每份草稿最新的那一次写入，按写入的先后：它说的就是草稿现在存下去的影响。 */
function latestEffect(edits: AssistantEdit[]): Map<number, AssistantEdit> {
  const latest = new Map<number, AssistantEdit>()
  for (const edit of edits) if (edit.kind === 'draft') latest.set(edit.draft, edit)
  return latest
}

export function resultView(done: AssistantPublished): PanelResult {
  return {
    taskId: done.task.id,
    title: done.saved
      ? t('tasks.assistant.result.saved', { name: done.task.name })
      : t('tasks.assistant.result.published', { name: done.task.name }),
    sub: done.saved || done.approved === 'APPROVED' ? '' : t('tasks.assistant.result.pending'),
  }
}

/** 一段对话画成面板上的样子。`current` 是页面上开着的草稿（见 `writesView`）；`canUndo` 说这次
 * 写入眼前有没有表单可以撤销。 */
export function panelMessages(
  messages: AssistantMessage[],
  terms: DraftTerms,
  canUndo: (edit: AssistantEdit) => boolean = () => false,
  current: number | null = null,
  ordinal?: (draft: number) => number | undefined,
  /** 比这些消息更晚的写入：后面的话题、还在写的那条回答。 */
  newer: AssistantEdit[] = []
) {
  // 整段对话里每份草稿最新的那一次：前面的回答画自己的卡时也按它，重读这段对话得到同样的结果。
  const effects = latestEffect([...messages.flatMap((m) => m.edits ?? []), ...newer])
  return messages.map((m) => ({
    role: m.role,
    text: m.text,
    stopped: m.stopped,
    at: m.at,
    files: m.files,
    shown: m.shown,
    askedOn: m.askedOn,
    selection: m.selection,
    cited: m.cited,
    steps: withSubagents((m.steps ?? []).map(stepView), m.subagents),
    ...writesView(m.edits ?? [], terms, current, canUndo, ordinal, (draft) => effects.get(draft)),
    results: (m.published ?? []).map(resultView),
    proposals: (m.proposals ?? []).map(proposalView),
    handed: m.handed ?? [],
    ...(m.silent ? { silent: true } : {}),
  }))
}
