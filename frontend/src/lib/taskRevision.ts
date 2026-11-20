// 题目的一版改了什么，一句话（后端 `task/package.py` 的 `changes`）：「改了说明、评分要点、
// 新增 数据.csv」。几样拼在一起的方式跟着读者的语言走。
import i18n, { t } from '@/i18n'

/** 项目资料库里题目那个只读文件夹的名字（后端 `library/mount.py` 的 `FOLDER`）。 */
export const TASK_FOLDER = '题目' // i18n-data: the folder's name in library paths, matched against them, not shown as copy
/** 题目文件夹里起始代码那一份的名字（后端 `task/package.py` 的 `STARTER`）。 */
export const STARTER_FILE = '起始代码.zip' // i18n-data: a file's name in the library, as stored, not copy

/** 一版改了哪几样；`files` 是材料的名字。 */
export interface TaskRevisionChanges {
  published?: boolean
  description?: boolean
  stages?: boolean
  submission?: boolean
  rubric?: boolean
  files?: { added?: string[]; replaced?: string[]; removed?: string[] }
  /** 起始代码那一份怎么了。 */
  starter?: 'added' | 'replaced' | 'removed'
}

const STARTER_VERBS = {
  added: 'work.overview.revisedStarterAdded',
  replaced: 'work.overview.revisedStarterReplaced',
  removed: 'work.overview.revisedStarterRemoved',
} as const

const PARTS = [
  ['description', 'work.overview.revisedPartDescription'],
  ['stages', 'work.overview.revisedPartStages'],
  ['submission', 'work.overview.revisedPartSubmission'],
  ['rubric', 'work.overview.revisedPartRubric'],
] as const

const FILE_VERBS = [
  ['added', 'work.overview.revisedAdded'],
  ['replaced', 'work.overview.revisedReplaced'],
  ['removed', 'work.overview.revisedRemoved'],
] as const

function joined(items: string[]): string {
  const locale = String(i18n.global.locale.value)
  return new Intl.ListFormat(locale, {
    type: 'conjunction',
    style: locale.startsWith('zh') ? 'narrow' : 'long',
  }).format(items)
}

export function revisionSummary(changes: TaskRevisionChanges | null | undefined): string {
  if (!changes || changes.published) return t('work.overview.revisedPublished')
  const clauses: string[] = []
  const parts = PARTS.filter(([key]) => changes[key]).map(([, label]) => t(label))
  if (parts.length) clauses.push(t('work.overview.revisedEdited', { parts: joined(parts) }))
  for (const [verb, label] of FILE_VERBS) {
    const names = changes.files?.[verb] ?? []
    if (names.length) clauses.push(t(label, { names: joined(names) }))
  }
  if (changes.starter && changes.starter in STARTER_VERBS) clauses.push(t(STARTER_VERBS[changes.starter]))
  return joined(clauses)
}
