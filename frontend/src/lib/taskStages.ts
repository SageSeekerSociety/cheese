// 发题表单里的阶段：阶段、提交项、评分要点三样连在一起改。提交项和评分要点用阶段的
// id 指它属于哪个阶段（`stage`；不分阶段时是 null），所以调整阶段的先后不动它们，
// 加、删一个阶段才要跟着改。这里只做这件事，不碰界面。
import type { TaskRubricLine, TaskStage, TaskStageStatus, TaskSubmissionSchemaEntry } from '@/types'

export interface StagedForm {
  stages: TaskStage[]
  items: TaskSubmissionSchemaEntry[]
  rubric: TaskRubricLine[]
}

/** 新加的阶段默认交一个文件。 */
export const NEW_STAGE_ITEM: TaskSubmissionSchemaEntry = { prompt: '提交文件', type: 'FILE' } // i18n-data: 存进题目数据的提交项名，和出题人写的题面一样不随界面语言变

/** 新阶段的 id：表单当场给，提交项和评分要点立刻就能指它；保存后题目沿用这个 id
 *  （后端 `app/domain/task/stages.py` 收的样子：小写字母和数字）。 */
export function newStageId(): string {
  const bytes = new Uint8Array(6)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

/** 阶段列表变了以后，原来每个阶段去了第几个（按位置）—— 已交的提交跟着它走；结果里
 *  没有的那个被删掉了。已交的提交从不换阶段，也不合并。和后端 `stages.stage_moves`
 *  同一套规矩：
 *  - 原来不分阶段（算一个阶段）：它是新列表的第一个阶段；
 *  - 最后几个阶段都删掉了：原来的第一个阶段成为不分阶段时那唯一的一个，别的都算删掉；
 *  - 否则 id 相同就是同一个阶段。 */
export function stageMoves(oldIds: (string | null | undefined)[], newIds: (string | null | undefined)[]) {
  const moved = new Map<number, number>()
  if (!oldIds.length || !newIds.length) moved.set(0, 0)
  else oldIds.forEach((id, i) => id != null && newIds.includes(id) && moved.set(i, newIds.indexOf(id)))
  return moved
}

/** 提交项和评分要点去哪：同 `stageMoves`，只是最后几个阶段都删掉时，提交项和评分要点
 *  全都留下，归不分阶段时那唯一的一个（后端 `stages.row_moves`）。 */
export function rowMoves(oldIds: (string | null | undefined)[], newIds: (string | null | undefined)[]) {
  if (oldIds.length && !newIds.length) return new Map(oldIds.map((_, i) => [i, 0] as [number, number]))
  return stageMoves(oldIds, newIds)
}

/** 会被删掉、却还有已交提交的第一个旧阶段（按位置）—— 这次改动不收；没有就是 null
 *  （后端 `stages.refused_stage_change`）。 */
export function refusedStageChange(
  oldIds: (string | null | undefined)[],
  newIds: (string | null | undefined)[],
  held: Set<number>
): number | null {
  const moved = stageMoves(oldIds, newIds)
  const refused = [...held].filter((i) => !moved.has(i)).sort((a, b) => a - b)
  return refused.length ? refused[0] : null
}

/** 提交项或评分要点在阶段列表变了以后：按 `rowMoves` 挪，再按 `settledStage` 归位；
 *  指着这次新加的阶段的留在那里。和后端 `stages.rows_after_stage_change` 一一对应。 */
export function rowsAfterStageChange<T extends { stage?: string | null }>(
  rows: T[],
  oldIds: string[],
  newIds: string[]
): { kept: T[]; removed: T[] } {
  const moved = rowMoves(oldIds, newIds)
  const kept: T[] = []
  const removed: T[] = []
  for (const row of rows) {
    const ref = row.stage ?? null
    if (ref !== null && newIds.includes(ref)) {
      kept.push({ ...row, stage: ref })
      continue
    }
    // 它原来属于哪个阶段；指着一个已经没有的阶段的，算第一个。
    const was = oldIds.length ? (ref !== null && oldIds.includes(ref) ? ref : oldIds[0]) : null
    const position = was !== null ? oldIds.indexOf(was) : 0
    const target = moved.get(position)
    if (target === undefined) {
      removed.push(row)
      continue
    }
    kept.push({ ...row, stage: newIds.length ? newIds[target] : null })
  }
  return { kept, removed }
}

/** 阶段换成 `stages` 之后的整张表：提交项和评分要点按同一套规矩跟着走。 */
function withStages(form: StagedForm, stages: TaskStage[]): StagedForm {
  const oldIds = form.stages.map((stage) => stage.id ?? '')
  const newIds = stages.map((stage) => stage.id ?? '')
  return {
    stages,
    items: rowsAfterStageChange(form.items, oldIds, newIds).kept,
    rubric: rowsAfterStageChange(form.rubric, oldIds, newIds).kept,
  }
}

/** 加一个阶段，排在最后。从「不分阶段」加第一个时，已有的提交项和评分要点都归它；
 *  再加的阶段自带一个要交的文件。 */
export function addStage(form: StagedForm): StagedForm {
  const stage: TaskStage = { id: newStageId(), name: '', description: '', dueAt: null }
  const next = withStages(form, [...form.stages, stage])
  if (!form.stages.length) return next
  return { ...next, items: [...next.items, { ...NEW_STAGE_ITEM, stage: stage.id }] }
}

/** 删掉第 `index` 个阶段和它的提交项、评分要点。删的是最后剩下的那一个时，题目回到
 *  不分阶段，它的提交项和评分要点留下。 */
export function removeStage(form: StagedForm, index: number): StagedForm {
  return withStages(
    form,
    form.stages.filter((_, i) => i !== index)
  )
}

/** 第 `from` 个阶段和第 `to` 个对调。提交项和评分要点按 id 指阶段，不用动。 */
export function moveStage(form: StagedForm, from: number, to: number): StagedForm {
  if (to < 0 || to >= form.stages.length || from === to) return form
  const stages = [...form.stages]
  ;[stages[from], stages[to]] = [stages[to], stages[from]]
  return { ...form, stages }
}

/** 一项提交内容或评分要点属于哪个阶段 —— 和后端 `stages.settled_stage` 同一条规矩：
 *  不分阶段时没有阶段；分了阶段时，没指阶段的（或指着一个已经没有的阶段的）归第一个
 *  阶段。画、存、发都按它，所以哪一条路都不会把一项弄丢。 */
export function settledStage(stages: TaskStage[], ref: string | null | undefined): string | null {
  if (!stages.length) return null
  return stages.some((stage) => stage.id === ref) ? (ref as string) : stages[0].id ?? null
}

/** 状态的那一组色调，配 `stage-status` 的样式。 */
export const STAGE_TONE: Record<TaskStageStatus, 'muted' | 'warn' | 'ok' | 'danger'> = {
  NOT_SUBMITTED: 'muted',
  PENDING_REVIEW: 'warn',
  PASSED: 'ok',
  REJECTED: 'danger',
  MISSED: 'danger',
}

export const STAGE_STATUSES: TaskStageStatus[] = ['NOT_SUBMITTED', 'PENDING_REVIEW', 'PASSED', 'REJECTED', 'MISSED']
