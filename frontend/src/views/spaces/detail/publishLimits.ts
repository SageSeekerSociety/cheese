import type { TaskSubmissionSchemaEntry } from '@/types'

/** 从 PDF 解析的上限：后端 `preview_task_from_pdf` 里写死的 15MB 与 1..20。 */
export const MAX_PDF_BYTES = 15 * 1024 * 1024
export const MAX_DRAFTS = 20

/** 发题时带上的提交表。后端建题那条路读它，不写，题目的提交页就一个输入项都没有。 */
export const TASK_SUBMISSION_SCHEMA: TaskSubmissionSchemaEntry[] = [{ prompt: '提交文件', type: 'FILE' }] // i18n-data: 存进题目数据的提交项名，和出题人写的题面一样不随界面语言变
