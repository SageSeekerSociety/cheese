// 题目测试点的评测（`backend/app/api/routes/tasks/judging.py`）：提交页那块「将要评测的
// 代码」、一版提交的评测结果、参与者列表的评测摘要，以及出题人试跑。老师展开隐藏用例不另
// 取：能批改的人读到的评测结果里已经带着每个用例的输入和期望输出。
import type {
  TaskCodeVersion,
  TaskJudgeRun,
  TaskJudgeSummary,
  TaskTestSuite,
  TaskTestTrial,
  TaskTestTrialFile,
} from '@/types'

import { axiosRequest } from './axiosHttp'

export namespace TaskJudgeApi {
  /** 这条领题的项目默认分支现在指着的那一版；读不到是 null。 */
  export const codeVersion = (taskId: number, participantId: number) =>
    axiosRequest<{ codeVersion: TaskCodeVersion | null }>(
      { url: `/tasks/${taskId}/participants/${participantId}/code-version`, method: 'GET' },
      true
    )

  /** 一版提交最近一次评测；没评测过是 null。 */
  export const submissionJudge = (taskId: number, participantId: number, submissionId: number) =>
    axiosRequest<{ judge: TaskJudgeRun | null }>(
      { url: `/tasks/${taskId}/participants/${participantId}/submissions/${submissionId}/judge`, method: 'GET' },
      true
    )

  /** 一次最多问几版提交（后端 `MAX_SUMMARY_IDS`）。 */
  export const SUMMARY_BATCH = 200

  /** 几版提交各自最近一次评测的摘要（只给能批改的人），最多 `SUMMARY_BATCH` 版。 */
  export const summaries = (taskId: number, submissionIds: number[]) =>
    axiosRequest<{ summaries: TaskJudgeSummary[] }>(
      { url: `/tasks/${taskId}/judge/summaries`, method: 'GET', params: { submissionIds: submissionIds.join(',') } },
      true
    )

  /** 用参考代码试跑一套测试点（题目不必已经发布）。返回刚排上的这一次。 */
  export const createTrial = (spaceId: number, testSuite: TaskTestSuite, files: TaskTestTrialFile[]) =>
    axiosRequest<{ trial: TaskTestTrial }>(
      { url: `/spaces/${spaceId}/test-trials`, method: 'POST', data: { testSuite, files } },
      true
    )

  /** 读一次试跑的结果。 */
  export const trial = (runId: number) =>
    axiosRequest<{ trial: TaskTestTrial }>({ url: `/tasks/test-trials/${runId}`, method: 'GET' }, true)
}
