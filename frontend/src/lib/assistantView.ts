// 芝士一轮里做的事，说成面板上的几样东西：每一步（动词 + 一个参数），每次写草稿的
// 改动卡（改了哪几格、各是什么、能不能撤销），它留给人确认的地方，以及发布的结果。
//
// 题目页和发布页、改题页用的是同一份：芝士在哪都能做这些事，差别只在页面上有没有
// 那张表单（没有就不给撤销）。
import type { AssistantEdit, AssistantMessage, AssistantPublished, AssistantStep } from '@/composables/useAssistant'
import type { DraftTerms } from '@/lib/taskDraftAgent'
import type { TaskRubricLine, TaskSubmissionSchemaEntry } from '@/types'

import dayjs from 'dayjs'

import { t } from '@/i18n'
import { partialStringField } from '@/lib/partialJson'
import { AGENT_FIELDS } from '@/lib/taskDraftAgent'
import { toolLabel } from '@/lib/toolLabels'

export interface PanelStep {
  key: string
  verb: string
  arg: string
  state: AssistantStep['state']
  error?: string
  /** 推理的那一段，不是一次工具调用。 */
  thinking?: boolean
}

export interface PanelChangeRow {
  /** 改这一格的那次写入（撤销时用）。 */
  change: number
  /** 芝士的字段名（`AGENT_FIELDS` 的键）。 */
  key: string
  label: string
  value: string
  undone: boolean
  /** 人在芝士读过之后改过这一格，芝士没动它。 */
  kept?: boolean
}

export interface PanelChange {
  id: number
  draft: number
  /** 新建的草稿：卡上写它的名字，点开切过去。 */
  created: boolean
  name: string
  rows: PanelChangeRow[]
  notes: { key: string; label: string; text: string }[]
  canUndo: boolean
  /** 一份文件拆出几道题时，这是第几道（卡头写「第 N 道」）。 */
  ordinal?: number
}

/** 一轮回答写进草稿的样子：眼前这份草稿改了哪几格（一张卡）。 */
export interface PanelWrites {
  changes: PanelChange[]
}

export interface PanelResult {
  taskId: number
  title: string
  sub: string
}

/** 每个字段在表单上叫什么。 */
const LABEL_KEYS: Record<string, string> = {
  name: 'tasks.form.name',
  description: 'tasks.form.description',
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
}

export function fieldLabel(key: string): string {
  const label = LABEL_KEYS[key]
  return label ? t(label) : key
}

/** 表单上的一格（`TaskDraftField`）属于芝士的哪个字段。 */
export function agentKeyOf(formField: string): string | null {
  for (const [key, fields] of Object.entries(AGENT_FIELDS)) if ((fields as string[]).includes(formField)) return key
  return null
}

/** 每种工具这一行跟的那个参数。 */
const STEP_ARG: Record<string, string> = {
  cheese_task_search: 'query',
  cheese_task_write: 'name',
  cheese_file_read: 'pages',
  cheese_docs_search: 'query',
  cheese_docs_read: 'page',
}

export function stepView(step: AssistantStep): PanelStep {
  if (step.name === 'thinking')
    return {
      key: step.call,
      verb:
        step.state === 'run'
          ? t('tasks.assistant.steps.thinking')
          : t('tasks.assistant.steps.thought', { s: Math.max(1, Math.round((step.ms ?? 0) / 1000)) }),
      arg: '',
      state: step.state,
      thinking: true,
    }
  const key = STEP_ARG[step.name]
  const arg = key ? partialStringField(step.args, key)?.text ?? '' : ''
  return {
    key: step.call,
    verb: toolLabel(step.name),
    arg: arg.replace(/\s+/g, ' ').trim().slice(0, 60),
    state: step.state,
    error: step.error === undefined ? undefined : stepError(step.error),
  }
}

/** 一步为什么没成，写成人读得懂的一句。工具报的是平台原样的回应（`Platform HTTP 404:
 * {...}`）时只说是哪一类；芝士自己读得到原文，接着会说它怎么办。平台拒绝时说的那句中文
 * 原样留着。 */
export function stepError(text: string): string {
  const status = /^Platform HTTP (\d{3})/.exec(text)?.[1]
  if (status === '404') return t('tasks.assistant.steps.notFound')
  if (status === '401' || status === '403') return t('tasks.assistant.steps.denied')
  if (status || !/\p{Script=Han}/u.test(text) || text.includes('{')) return t('tasks.assistant.steps.failed')
  return text.length > 80 ? `${text.slice(0, 80)}…` : text
}

const LEVELS: Record<number, string> = {
  1: 'tasks.form.beginner',
  2: 'tasks.form.intermediate',
  3: 'tasks.form.advanced',
}

/** 一格的值，写成卡上那一行。 */
export function valueText(key: string, value: unknown, terms: DraftTerms): string {
  if (value === null || value === undefined || value === '') return t('tasks.assistant.changes.empty')
  switch (key) {
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
      return String(value)
        .replace(/[#>*_`]/g, '')
        .replace(/\s+/g, ' ')
        .trim()
        .slice(0, 80)
    default:
      return String(value)
  }
}

export function changeView(edit: AssistantEdit, terms: DraftTerms, canUndo: boolean): PanelChange {
  const rows: PanelChangeRow[] = []
  const seen = new Set<string>()
  for (const [field, change] of Object.entries(edit.changed)) {
    const key = agentKeyOf(field)
    if (!key || seen.has(key)) continue
    seen.add(key)
    rows.push({
      change: edit.id,
      key,
      label: fieldLabel(key),
      value: valueText(key, change.after, terms),
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
 * （一份文件拆成几道题）不在这里画，在表单上方那一排里；切过去以后，这一轮再画一遍，卡换成
 * 那一份的。没有表单的页面（题目页）`current` 是 null：每份草稿各一张卡，撤销不了。
 */
export function writesView(
  edits: AssistantEdit[],
  terms: DraftTerms,
  current: number | null,
  canUndo: (edit: AssistantEdit) => boolean = () => false,
  ordinal: (draft: number) => number | undefined = () => undefined
): PanelWrites {
  const order: number[] = []
  for (const edit of edits) if (!order.includes(edit.draft)) order.push(edit.draft)
  const shown = current === null ? order : order.filter((id) => id === current)
  const changes = shown.map((id) => ({
    ...merged(
      edits.filter((e) => e.draft === id),
      terms,
      canUndo
    ),
    ordinal: ordinal(id),
  }))
  return { changes }
}

/** 同一份草稿的几次写入并成一张卡：一格只留最后一次写的。 */
function merged(edits: AssistantEdit[], terms: DraftTerms, canUndo: (edit: AssistantEdit) => boolean): PanelChange {
  const views = edits.map((e) => changeView(e, terms, canUndo(e)))
  const rows = new Map<string, PanelChangeRow>()
  for (const view of views)
    for (const row of view.rows) {
      rows.delete(row.key)
      rows.set(row.key, row)
    }
  const last = views[views.length - 1]
  return {
    ...last,
    created: views.some((v) => v.created),
    rows: [...rows.values()],
    notes: views.flatMap((v) => v.notes),
    canUndo: views.some((v) => v.canUndo),
  }
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
  ordinal?: (draft: number) => number | undefined
) {
  return messages.map((m) => ({
    role: m.role,
    text: m.text,
    stopped: m.stopped,
    at: m.at,
    files: m.files,
    steps: (m.steps ?? []).map(stepView),
    ...writesView(m.edits ?? [], terms, current, canUndo, ordinal),
    results: (m.published ?? []).map(resultView),
  }))
}
