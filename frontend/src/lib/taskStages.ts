// 发题表单里的阶段：阶段、提交项、评分要点三样连在一起改。提交项和评分要点按序号
// 指它属于哪个阶段（`stage`，从 0 数；不分阶段时都是 0），所以加、删、挪一个阶段，
// 另外两样的序号要跟着变。这里只做这件事，不碰界面。
import type { TaskRubricLine, TaskStage, TaskStageStatus, TaskSubmissionSchemaEntry } from '@/types'

export interface StagedForm {
  stages: TaskStage[]
  items: TaskSubmissionSchemaEntry[]
  rubric: TaskRubricLine[]
}

/** 新加的阶段默认交一个文件。 */
export const NEW_STAGE_ITEM: TaskSubmissionSchemaEntry = { prompt: '提交文件', type: 'FILE' } // i18n-data: 存进题目数据的提交项名，和出题人写的题面一样不随界面语言变

const stageOf = (entry: { stage?: number }) => entry.stage ?? 0

/** 加一个阶段，排在最后。从「不分阶段」加第一个时，已有的提交项和评分要点都归它。 */
export function addStage(form: StagedForm): StagedForm {
  const stage: TaskStage = { name: '', description: '', dueAt: null, previousIndex: null }
  if (!form.stages.length) return { ...form, stages: [stage] }
  const index = form.stages.length
  return {
    stages: [...form.stages, stage],
    items: [...form.items, { ...NEW_STAGE_ITEM, stage: index }],
    rubric: form.rubric,
  }
}

/** 删掉第 `index` 个阶段和它的提交项、评分要点；后面的阶段往前挪。删的是最后剩下的
 *  那一个时，题目回到不分阶段，它的提交项和评分要点留下。 */
export function removeStage(form: StagedForm, index: number): StagedForm {
  if (form.stages.length === 1) return { ...form, stages: [] }
  const shift = <T extends { stage?: number }>(rows: T[]) =>
    rows
      .filter((row) => stageOf(row) !== index)
      .map((row) => (stageOf(row) > index ? { ...row, stage: stageOf(row) - 1 } : row))
  return {
    stages: form.stages.filter((_, i) => i !== index),
    items: shift(form.items),
    rubric: shift(form.rubric),
  }
}

/** 第 `from` 个阶段和第 `to` 个对调，各自的提交项和评分要点跟着走。 */
export function moveStage(form: StagedForm, from: number, to: number): StagedForm {
  if (to < 0 || to >= form.stages.length || from === to) return form
  const swap = <T extends { stage?: number }>(rows: T[]) =>
    rows.map((row) =>
      stageOf(row) === from ? { ...row, stage: to } : stageOf(row) === to ? { ...row, stage: from } : row
    )
  const stages = [...form.stages]
  ;[stages[from], stages[to]] = [stages[to], stages[from]]
  return { stages, items: swap(form.items), rubric: swap(form.rubric) }
}

/** 页面画阶段的那几样：每个阶段一份，不分阶段时是空的。 */
export function stageGroups<T extends { stage?: number }>(
  stages: TaskStage[],
  rows: T[]
): { stage: TaskStage; index: number; rows: { row: T; at: number }[] }[] {
  return stages.map((stage, index) => ({
    stage,
    index,
    rows: rows.map((row, at) => ({ row, at })).filter(({ row }) => stageOf(row) === index),
  }))
}

/** 状态的那一组色调，配 `stage-status` 的样式。 */
export const STAGE_TONE: Record<TaskStageStatus, 'muted' | 'warn' | 'ok' | 'danger'> = {
  NOT_SUBMITTED: 'muted',
  PENDING_REVIEW: 'warn',
  PASSED: 'ok',
  REJECTED: 'danger',
}

export const STAGE_STATUSES: TaskStageStatus[] = ['NOT_SUBMITTED', 'PENDING_REVIEW', 'PASSED', 'REJECTED']
