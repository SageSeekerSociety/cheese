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

/** 加一个阶段，排在最后。从「不分阶段」加第一个时，已有的提交项和评分要点都归它。 */
export function addStage(form: StagedForm): StagedForm {
  const stage: TaskStage = { id: newStageId(), name: '', description: '', dueAt: null }
  if (!form.stages.length) {
    return {
      stages: [stage],
      items: form.items.map((item) => ({ ...item, stage: stage.id })),
      rubric: form.rubric.map((line) => ({ ...line, stage: stage.id })),
    }
  }
  return {
    stages: [...form.stages, stage],
    items: [...form.items, { ...NEW_STAGE_ITEM, stage: stage.id }],
    rubric: form.rubric,
  }
}

/** 删掉第 `index` 个阶段和它的提交项、评分要点。删的是最后剩下的那一个时，题目回到
 *  不分阶段，它的提交项和评分要点留下。 */
export function removeStage(form: StagedForm, index: number): StagedForm {
  if (form.stages.length === 1) {
    return {
      stages: [],
      items: form.items.map((item) => ({ ...item, stage: null })),
      rubric: form.rubric.map((line) => ({ ...line, stage: null })),
    }
  }
  const gone = form.stages[index]?.id
  return {
    stages: form.stages.filter((_, i) => i !== index),
    items: form.items.filter((item) => item.stage !== gone),
    rubric: form.rubric.filter((line) => line.stage !== gone),
  }
}

/** 第 `from` 个阶段和第 `to` 个对调。提交项和评分要点按 id 指阶段，不用动。 */
export function moveStage(form: StagedForm, from: number, to: number): StagedForm {
  if (to < 0 || to >= form.stages.length || from === to) return form
  const stages = [...form.stages]
  ;[stages[from], stages[to]] = [stages[to], stages[from]]
  return { ...form, stages }
}

/** 状态的那一组色调，配 `stage-status` 的样式。 */
export const STAGE_TONE: Record<TaskStageStatus, 'muted' | 'warn' | 'ok' | 'danger'> = {
  NOT_SUBMITTED: 'muted',
  PENDING_REVIEW: 'warn',
  PASSED: 'ok',
  REJECTED: 'danger',
}

export const STAGE_STATUSES: TaskStageStatus[] = ['NOT_SUBMITTED', 'PENDING_REVIEW', 'PASSED', 'REJECTED']
